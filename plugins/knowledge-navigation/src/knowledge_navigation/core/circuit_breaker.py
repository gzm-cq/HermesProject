"""熔断器 + 飞书通知。

Hindsight / SAG recall 连续失败时触发熔断（跳过 recall），冷却后自动恢复。
熔断打开时发送飞书卡片通知。

纯独立模块，只引用 CONFIG 和 logging。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

from knowledge_navigation.config import CONFIG

logger = logging.getLogger(__name__)

# 模块级文件锁：保护共享 JSON 文件的读-改-写原子性，
# 防止 _hindsight_cb 与 _sag_cb 并发 _save_state/_load_state 导致 lost-update
_file_lock = threading.Lock()


class CircuitBreaker:
    """命名空间化的熔断器，每路召回独立一个实例。

    状态持久化到 JSON 文件，服务重启后可恢复。

    线程安全设计：
    - self._lock：保护单实例的内存状态（_failures/_open_until 等）
    - _file_lock（模块级）：保护共享 JSON 文件的读-改-写原子性，
      防止 hindsight_cb 与 sag_cb 并发 _save_state 导致 lost-update
    """

    def __init__(self, name: str, threshold: int, cooldown: int, state_file: str = ""):
        self.name = name
        self.threshold = threshold
        self.cooldown = cooldown
        self.state_file = state_file
        self._failures: int = 0
        self._open_until: float = 0.0
        self._total_failures: int = 0
        self._failure_types: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._load_state()

    def _state_file_path(self) -> str:
        if self.state_file:
            return self.state_file
        module_dir = Path(__file__).resolve().parent.parent.parent
        return str(module_dir / "circuit_breaker.json")

    def _load_state(self) -> None:
        """从 JSON 文件加载熔断器状态。"""
        try:
            path = self._state_file_path()
            if not Path(path).exists():
                return
            with _file_lock:  # 与 _save_state 共享文件锁，防止并发读写冲突
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            cb_data = data.get(self.name, {})
            self._failures = int(cb_data.get("consecutive_failures", 0))
            self._open_until = float(cb_data.get("open_until", 0.0))
            self._total_failures = int(cb_data.get("total_failures", 0))
            types = cb_data.get("failure_types", {})
            if isinstance(types, dict):
                self._failure_types = Counter({k: int(v) for k, v in types.items()})
        except Exception as e:
            logger.debug("[%s] 加载熔断器状态失败: %s", self.name, e)

    def _save_state(self) -> None:
        """持久化熔断器状态到 JSON 文件（文件锁保护读-改-写原子性）。"""
        try:
            path = self._state_file_path()
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            # 文件锁保证读-改-写原子性，防止 hindsight_cb 与 sag_cb 并发写入丢失更新
            with _file_lock:
                data: dict[str, Any] = {}
                try:
                    if Path(path).exists():
                        with open(path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                except Exception:
                    pass
                data[self.name] = {
                    "state": "open" if self._open_until > time.time() else "closed",
                    "consecutive_failures": self._failures,
                    "open_until": self._open_until,
                    "total_failures": self._total_failures,
                    "failure_types": dict(self._failure_types),
                    "last_updated": time.time(),
                }
                tmp_path = path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                Path(tmp_path).replace(path)
        except Exception as e:
            logger.debug("[%s] 保存熔断器状态失败: %s", self.name, e)

    def is_open(self) -> bool:
        with self._lock:
            if self._open_until <= 0:
                return False
            if time.time() < self._open_until:
                return True
            self._failures = 0
            self._open_until = 0.0
            self._failure_types.clear()
            # 在锁内调用 _save_state，确保状态转换与持久化原子
            self._save_state()
        return False

    def record_failure(self, category: str = "unknown") -> bool:
        """记录一次失败，返回是否触发了熔断。"""
        _should_notify = False
        _failure_snapshot: dict[str, int] = {}
        with self._lock:
            self._failures += 1
            self._total_failures += 1
            self._failure_types[category] += 1
            if self._failures >= self.threshold:
                self._open_until = time.time() + self.cooldown
                logger.warning(
                    "[%s] 熔断器开启：连续 %d 次 recall 失败，跳过 %d 秒",
                    self.name, self._failures, self.cooldown,
                )
                _should_notify = True
                _failure_snapshot = dict(self._failure_types)
                self._failure_types.clear()
            # 在锁内调用 _save_state，确保状态转换与持久化原子
            self._save_state()
        if _should_notify:
            _notify_feishu_circuit_open(self.name, _failure_snapshot)
        return _should_notify

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0
            self._failure_types.clear()
            # 在锁内调用 _save_state，确保状态转换与持久化原子
            self._save_state()


_hindsight_cb = CircuitBreaker(
    name="hindsight",
    threshold=CONFIG.circuit_breaker_threshold,
    cooldown=CONFIG.circuit_breaker_cooldown,
)
_sag_cb = CircuitBreaker(
    name="sag",
    threshold=CONFIG.circuit_breaker_threshold,
    cooldown=CONFIG.circuit_breaker_cooldown,
)

# ── 飞书通知状态 ──
_CATEGORY_LABELS: dict[str, str] = {
    "exception": "🔴 未预期异常",
    "service_error": "🟡 服务返回空",
}
_CATEGORY_LEVELS: dict[str, str] = {
    "exception": "red",
    "service_error": "yellow",
}
_LAST_NOTIFICATION_TIME: float = 0.0
_NOTIFICATION_MIN_INTERVAL: float = 300.0  # 同 session 至少间隔 5 分钟
_FEISHU_TOKEN: str = ""
_FEISHU_TOKEN_EXPIRES_AT: float = 0.0
_FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_FEISHU_MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"


# ====================================================================
# 兼容旧 API（Hindsight 专用）
# ====================================================================


def circuit_is_open() -> bool:
    """检查 Hindsight 熔断器是否开启（跳过 recall）。"""
    return _hindsight_cb.is_open()


def circuit_record_failure(category: str = "unknown") -> None:
    """记录一次 Hindsight 失败，必要时触发熔断并发送飞书通知。"""
    _hindsight_cb.record_failure(category)


def circuit_record_success() -> None:
    """Hindsight 成功调用后重置熔断器。"""
    _hindsight_cb.record_success()


# ====================================================================
# SAG 熔断器 API
# ====================================================================


def sag_circuit_is_open() -> bool:
    """检查 SAG 熔断器是否开启。"""
    return _sag_cb.is_open()


def sag_circuit_record_failure(category: str = "unknown") -> None:
    """记录一次 SAG 失败。"""
    _sag_cb.record_failure(category)


def sag_circuit_record_success() -> None:
    """SAG 成功后重置熔断器。"""
    _sag_cb.record_success()


# ====================================================================
# 飞书通知
# ====================================================================


def _get_feishu_token() -> str:
    """获取飞书 tenant_access_token，带 2 小时缓存。

    使用 CONFIG.feishu_app_id / feishu_app_secret 调用内部应用 API。
    返回空字符串表示获取失败。
    """
    global _FEISHU_TOKEN, _FEISHU_TOKEN_EXPIRES_AT
    now = time.time()
    if _FEISHU_TOKEN and now < _FEISHU_TOKEN_EXPIRES_AT:
        return _FEISHU_TOKEN

    app_id = CONFIG.feishu_app_id
    app_secret = CONFIG.feishu_app_secret
    if not app_id or not app_secret:
        return ""

    try:
        import requests as _req  # type: ignore[import-untyped]

        resp = _req.post(
            _FEISHU_TOKEN_URL,
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code != 200 or "tenant_access_token" not in data:
            logger.warning("获取飞书 token 失败：HTTP %s", resp.status_code)
            return ""
        _FEISHU_TOKEN = data["tenant_access_token"]
        expires_in = data.get("expire", 7200)  # 默认 7200 秒
        # 提前 60 秒过期，防边界情况
        _FEISHU_TOKEN_EXPIRES_AT = now + max(expires_in - 60, 60)
        logger.info("飞书 token 刷新成功（过期 %d 秒）", expires_in)
        return _FEISHU_TOKEN
    except Exception as e:
        logger.warning("获取飞书 token 异常: %s", e)
        return ""


def _notify_feishu_circuit_open(name: str, failure_types: dict[str, int]) -> None:
    """熔断器打开时通过飞书发送卡片消息（限频 5 分钟一次）。"""
    global _LAST_NOTIFICATION_TIME
    now = time.time()
    if now - _LAST_NOTIFICATION_TIME < _NOTIFICATION_MIN_INTERVAL:
        logger.info("飞书通知跳过：距上次通知不足 5 分钟")
        return

    app_id = CONFIG.feishu_app_id
    app_secret = CONFIG.feishu_app_secret
    channel = CONFIG.feishu_home_channel
    if not app_id or not app_secret or not channel:
        logger.info("飞书通知跳过：未配置 app_id / app_secret / home_channel")
        return

    token = _get_feishu_token()
    if not token:
        logger.warning("飞书通知跳过：无法获取 tenant_access_token")
        return

    # 构建失败分布
    lines: list[str] = []
    for cat, count in sorted(failure_types.items(), key=lambda x: -x[1]):
        label = _CATEGORY_LABELS.get(cat, cat)
        lines.append(f"- {label}：{count} 次")
    dist_text = "\n".join(lines) if lines else "- 未知"

    top_cat = max(failure_types, key=failure_types.get)  # type: ignore[arg-type]
    template = _CATEGORY_LEVELS.get(top_cat, "red")
    name_display = name.upper()

    card_body = {
        "header": {
            "title": {"tag": "plain_text", "content": f"\u26a0\ufe0f {name_display} Recall \u6545\u969c"},
            "template": template,
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**\u7194\u65ad\u5668\u5df2\u5f00\u542f**\n"
                        f"\u8fde\u7eed {CONFIG.circuit_breaker_threshold} \u6b21 recall \u5931\u8d25\uff0c"
                        f"\u8df3\u8fc7 {CONFIG.circuit_breaker_cooldown} \u79d2\n\n"
                        f"**\u5931\u8d25\u5206\u5e03\uff1a**\n{dist_text}\n\n"
                        f"**\u65f6\u95f4\uff1a**{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"**\u76ee\u6807\u670d\u52a1\uff1a**{name}"
                    ),
                },
            },
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "knowledge-navigation plugin \u00b7 \u51b7\u5374\u671f\u540e\u5c06\u81ea\u52a8\u6062\u590d"},
                ],
            },
        ],
    }

    _LAST_NOTIFICATION_TIME = now
    try:
        import requests as _req  # type: ignore[import-untyped]

        payload = {
            "receive_id": channel,
            "msg_type": "interactive",
            "content": json.dumps(card_body, ensure_ascii=False),
        }
        resp = _req.post(
            _FEISHU_MESSAGE_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code != 200 or data.get("code", 0) != 0:
            logger.warning("飞书通知失败：HTTP %s, code=%s", resp.status_code, data.get("code"))
        else:
            logger.info("飞书通知已发送（类别分布：%s）", failure_types)
    except Exception as e:
        logger.warning("飞书通知异常: %s", e)
