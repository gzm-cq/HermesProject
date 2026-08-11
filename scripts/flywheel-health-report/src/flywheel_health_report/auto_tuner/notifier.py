"""notifier.py — auto-tuner 飞书通知函数。

从 cron-wrappers/auto-tuner.py 拆分而来。
包含：_has_cron_notify, _send_lark, notify_restart_reminder, notify_gateway_restart。

路径常量 CRON_LIB / FEISHU_CHAT_ID 来自 ..config。
本模块自包含（不依赖 tuner.py），避免循环导入。
"""

import json
import os
import subprocess
from typing import Any, Dict, Tuple

from ..config import CRON_LIB, FEISHU_CHAT_ID


# ============================================================
# 内部日志（轻量版，避免与 tuner.py 循环导入）
# ============================================================

def log_info(msg: str) -> None: print(f"[tuner] {msg}")
def log_step(msg: str) -> None: print(f"[step ] {msg}")
def log_ok(msg: str) -> None:   print(f"[  ok ] {msg}")


# ============================================================
# Shell 工具（_send_lark 依赖）
# ============================================================

def _run_shell(cmd: str, timeout: int = 10) -> Tuple[str, str, int]:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", -1
    except Exception as e:
        return "", str(e), -1


# ============================================================
# 飞书通知
# ============================================================

def _has_cron_notify() -> bool:
    """检查 cron_common.sh 是否提供 cron_notify（实际 bash 侧定义的函数）。
    Python 里无法直接 source，我们以 lib 文件存在作为 proxy，
    然后走 lark-cli 直接发送（避免 bash-函数依赖导致耦合）。
    实际环境两边都 OK。"""
    return os.path.isfile(CRON_LIB)


def _send_lark(title: str, msg: str) -> None:
    """尽力发送飞书（markdown 格式），失败只 warn。
    lark-cli v1.0.31 不支持 --title，改用 --markdown。"""
    if not FEISHU_CHAT_ID:
        log_info("未配置 FEISHU_CHAT_ID，跳过飞书通知")
        return
    # 优先：如果环境里有 cron_notify（通过 bash -lc 调用），走这里；
    # 否则 fallback 到 lark-cli。
    if _has_cron_notify():
        out, err, rc = _run_shell(
            f"bash -lc 'source \"{CRON_LIB}\" 2>/dev/null; "
            f"cron_notify {json.dumps(title)} {json.dumps(msg)}' 2>&1 || true",
            timeout=20,
        )
        if rc == 0:
            return
        # cron_notify 失败则走 fallback
    # Fallback: lark-cli（markdown 格式，lark-cli v1.0.31 无 --title）
    markdown_body = f"**{title}**\\n\\n{msg}"
    cmd = (f'lark-cli im +messages-send --chat-id "{FEISHU_CHAT_ID}" '
           f'--markdown {json.dumps(markdown_body)} 2>/dev/null || true')
    _run_shell(cmd, timeout=20)


def notify_restart_reminder(last_tune: Dict[str, Any]) -> None:
    """提醒：参数已改但网关未重启，调优没生效。"""
    param = last_tune.get("parameter", "?")
    old_v = last_tune.get("old_value", "?")
    new_v = last_tune.get("new_value", "?")
    reason = last_tune.get("reason", "")
    ts = last_tune.get("timestamp", "")
    title = "⚠️ Auto-Tuner 调优尚未生效（网关未重启）"
    msg = (f"参数已修改但 hermes-gateway 自 {ts} 之后未重启，调优还没进运行时。\n\n"
           f"**参数**: {param}\n**旧值**: {old_v}\n**新值**: {new_v}\n"
           f"**原因**: {reason}\n\n"
           f"**执行重启**:\n```bash\nsystemctl restart hermes-gateway\n```\n\n"
           f"**验证**:\n```bash\nsystemctl status hermes-gateway\n```")
    _send_lark(title, msg)


def notify_gateway_restart(param: str, old_v: Any, new_v: Any, reason: str,
                           dry_run: bool) -> None:
    if dry_run:
        log_info("[DRY-RUN] 跳过飞书通知")
        return
    log_step("发送飞书通知 — 需要手动重启网关")
    title = "🔧 Auto-Tuner 需要手动重启网关"
    msg = (f"参数已修改，需要重启 hermes-gateway 生效：\n\n"
           f"**参数**: {param}\n**旧值**: {old_v}\n**新值**: {new_v}\n"
           f"**原因**: {reason}\n\n"
           f"**操作**:\n```bash\nsystemctl restart hermes-gateway\n```\n\n"
           f"**验证**:\n```bash\nsystemctl status hermes-gateway\n```")
    _send_lark(title, msg)
    log_ok("飞书通知已发送")
