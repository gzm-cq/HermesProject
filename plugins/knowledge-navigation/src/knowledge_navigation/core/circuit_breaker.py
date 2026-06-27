"""熔断器 + 飞书通知。

Hindsight recall 连续失败时触发熔断（跳过 recall），冷却后自动恢复。
熔断打开时发送飞书卡片通知。

纯独立模块，只引用 CONFIG 和 logging。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from typing import Any

from knowledge_navigation.config import CONFIG

logger = logging.getLogger(__name__)

# ── 熔断器状态 ──
_circuit_failures: int = 0
_circuit_open_until: float = 0.0
_circuit_failure_types: Counter[str] = Counter()
_circuit_lock = threading.Lock()

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
# 熔断器
# ====================================================================


def circuit_is_open() -> bool:
    """检查熔断器是否开启（跳过 recall）。"""
    global _circuit_failures, _circuit_open_until
    with _circuit_lock:
        if _circuit_open_until <= 0:
            return False
        if time.time() < _circuit_open_until:
            return True
        # 冷却期已过，重置
        _circuit_failures = 0
        _circuit_open_until = 0.0
        _circuit_failure_types.clear()
        return False


def circuit_record_failure(category: str = "unknown") -> None:
    """记录一次失败，必要时触发熔断并发送飞书通知。

    Args:
        category: 失败类别（exception / service_error）。
    """
    global _circuit_failures, _circuit_open_until, _circuit_failure_types
    _should_notify = False
    _failure_snapshot: dict[str, int] = {}
    with _circuit_lock:
        _circuit_failures += 1
        _circuit_failure_types[category] += 1
        if _circuit_failures >= CONFIG.circuit_breaker_threshold:
            _circuit_open_until = time.time() + CONFIG.circuit_breaker_cooldown
            logger.warning(
                "熔断器开启：连续 %d 次 recall 失败，跳过 %d 秒",
                _circuit_failures, CONFIG.circuit_breaker_cooldown,
            )
            _should_notify = True
            _failure_snapshot = dict(_circuit_failure_types)
            _circuit_failure_types.clear()
    # 锁外发送通知（HTTP 不阻塞其他线程）
    if _should_notify:
        _notify_feishu_circuit_open(_failure_snapshot)


def circuit_record_success() -> None:
    """成功调用后重置熔断器。"""
    global _circuit_failures, _circuit_open_until, _circuit_failure_types
    with _circuit_lock:
        _circuit_failures = 0
        _circuit_open_until = 0.0
        _circuit_failure_types.clear()


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


def _notify_feishu_circuit_open(failure_types: dict[str, int]) -> None:
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

    card_body = {
        "header": {
            "title": {"tag": "plain_text", "content": "\u26a0\ufe0f Hindsight Recall \u6545\u969c"},
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
                        f"**\u76ee\u6807\u670d\u52a1\uff1a**localhost:9177"
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
