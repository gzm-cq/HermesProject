"""Hindsight API 适配器。

封装与 Hindsight 服务的所有 HTTP 交互，支持重试、超时和错误处理。
使用模块级 Session 复用（2026-06-13），避免每轮新建/关闭连接。
"""

from __future__ import annotations

import json
import logging
import time

import requests

from knowledge_navigation.config import CONFIG

logger = logging.getLogger(__name__)


class HindsightClient:
    """Hindsight API 客户端，提供可靠的 recall 功能。

    使用模块级共享 Session（2026-06-13），避免每轮新建 TCP 连接。
    """

    _shared_session: requests.Session | None = None
    _shared_session_lock = __import__("threading").Lock()

    @classmethod
    def _get_session(cls) -> requests.Session:
        """获取或创建共享 Session。"""
        if cls._shared_session is None:
            with cls._shared_session_lock:
                if cls._shared_session is None:
                    s = requests.Session()
                    s.headers.update({
                        "Content-Type": "application/json",
                        "User-Agent": "knowledge-navigation-plugin/1.1.0",
                    })
                    cls._shared_session = s
        return cls._shared_session

    def __init__(
        self,
        base_url: str = CONFIG.hindsight_api_url,
        timeout: int = CONFIG.timeout_seconds,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = self._get_session()

    def recall(
        self,
        query: str,
        budget: str = "low",
        trace: bool = True,
        max_results: int = 10,
    ) -> dict | None:
        """执行 recall 请求。

        Args:
            query: 查询文本。
            budget: 预算级别 ("low", "medium", "high")。
            trace: 是否启用 trace 模式。
            max_results: 最大返回结果数；传 0 时不发送该字段，
                由 Hindsight 服务端使用其默认值（不等于"不限制"）。

        Returns:
            API 响应字典，失败时返回 None。
        """
        payload: dict[str, object] = {
            "query": query,
            "budget": budget,
            "trace": trace,
        }
        if max_results > 0:
            payload["max_results"] = max_results

        for attempt in range(CONFIG.max_retries + 1):
            try:
                response = self.session.post(
                    f"{self.base_url}",
                    json=payload,
                    timeout=self.timeout,
                )

                if response.status_code == 200:
                    try:
                        return response.json()
                    except json.JSONDecodeError as e:
                        logger.warning("JSON 解析失败: %s", e)
                        return None
                elif response.status_code == 429:
                    wait_time = min(2**attempt + 0.1 * attempt, 30.0)
                    logger.warning("API 限流，等待 %.1f 秒后重试...", wait_time)
                    time.sleep(wait_time)
                    continue
                else:
                    logger.warning("API 请求失败，状态码: %s", response.status_code)
                    return None

            except requests.exceptions.Timeout:
                if attempt < CONFIG.max_retries:
                    wait_time = min(2**attempt + 0.1 * attempt, 30.0)
                    logger.warning("请求超时，%.1f 秒后重试...", wait_time)
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error("请求超时，已达到最大重试次数")
                    return None
            except requests.exceptions.ConnectionError:
                if attempt < CONFIG.max_retries:
                    wait_time = min(2**attempt + 0.1 * attempt, 30.0)
                    logger.warning("连接失败，%.1f 秒后重试...", wait_time)
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error("连接失败，已达到最大重试次数")
                    return None
            except Exception as e:
                logger.error("请求异常: %s", e)
                return None

        return None

    def close(self) -> None:
        """空操作：Session 全局共享，不由单个实例关闭。"""
        pass

    def __enter__(self) -> "HindsightClient":
        """上下文管理器入口。"""
        return self

    def __exit__(self, *args: object) -> None:
        """上下文管理器出口，close 为空操作。"""
        self.close()
