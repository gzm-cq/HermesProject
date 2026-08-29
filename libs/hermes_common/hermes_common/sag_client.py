"""SAG 公共客户端：统一「先取 token → 再调 SAG」。

背景
----
SAG（结构化文档检索服务）此前在三个位置各写了一遍「读 token + 调接口」：

  - knowledge-navigation : ``core/hooks/router.py::_do_sag_recall``（POST /api/v1/search）
  - dream-synth          : ``dream-daily.py`` 的 sag_search / sag_ingest / sag_health_check
  - system-health-check  : ``health-check-all.py``（直读 token 文件做 curl 探针）

历史背景：401 自愈此前只存在于测试的 import 语句里、从未真正实现 —— SAG 的 JWT
有过期时间，过期后各路调用会集体 401 而无人处理。本模块已把该能力真正落地
（见 :func:`refresh_sag_token`）；原先为兼容那条测试而建的 ``sag_auth_refresh``
薄封装，连同该测试已于 2026-08-29 一并清理，不再是依赖入口。

本模块把上述逻辑收敛为一处，对外只暴露两件事：

  1. **取 token**（:meth:`SagClient.get_token`）：进程内缓存 → 环境变量 → token 文件
  2. **调接口**（:meth:`SagClient.request`）：注入 Bearer 发起请求；遇 401 自动
     换发 token（走 ``/api/v1/auth/login``）并重试一次

依赖说明（重要）
----------------
本模块是 ``hermes_common`` 中**唯一**依赖第三方 ``requests`` 的子模块，因此不参与
``hermes_common/__init__.py`` 的顶层 re-export —— 包的「零第三方依赖」承诺由
``ledger`` / ``llm_guard`` / ``text_utils`` 继续保持。使用方请显式导入::

    from hermes_common.sag_client import SagClient

配置（环境变量，全部可选）
--------------------------
``SAG_API_URL`` / ``KN_SAG_API_URL``        : 服务地址，默认 http://127.0.0.1:4173
``KN_SAG_AUTH_TOKEN`` / ``SAG_AUTH_TOKEN``  : 直接指定 Bearer token
``SAG_TOKEN_PATH``                          : token 文件，默认 ~/.hermes/.sag_token
``KN_SAG_AUTH_NAME`` / ``SAG_AUTH_NAME``    : 401 换发用的登录名，默认 hermes
``KN_SAG_AUTH_EMAIL`` / ``SAG_AUTH_EMAIL``  : 登录邮箱（可选）
``KN_SAG_AUTH_PASSWORD`` / ``SAG_AUTH_PASSWORD`` : 登录密码（可选）

契约依据（SAG 服务端源码核对 2026-08-28）
-----------------------------------------
``/api/v1/search`` 请求体字段为 **snake_case**：``top_k`` / ``strategy`` / ``source_ids``
（``sag_api/schemas/search.py``）。此前 dream-synth 使用的 ``topK`` / ``searchMode`` /
``sourceIds`` 并非服务端字段，会被静默忽略 —— 收敛后统一按服务端契约发送。

``/api/v1/auth/login`` 走 ``authenticate_or_register``：**未提供 password 时不校验密码**，
仅按 name（或回退首个用户）匹配，不存在则自动注册。故单机场景下换发 token 只需 name。
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:4173"
DEFAULT_TOKEN_PATH = "~/.hermes/.sag_token"
DEFAULT_AUTH_NAME = "hermes"

SEARCH_PATH = "/api/v1/search"
AUTH_LOGIN_PATH = "/api/v1/auth/login"
HEALTH_PATH = "/api/v1/system/health"
INGEST_PATH_TMPL = "/api/v1/sources/{source_id}/documents/ingest"


def _env(*names: str) -> str:
    """按优先级返回首个非空环境变量值。"""
    for name in names:
        val = os.getenv(name)
        if val and val.strip():
            return val.strip()
    return ""


class SagClientError(Exception):
    """SAG 调用错误。

    status_code:
      - 4xx : 客户端错误（query 超长、鉴权失败等），不应触发服务熔断
      - 5xx : 服务端错误，应触发熔断
      - None: 非 HTTP 失败（超时 / 连接失败 / JSON 解析失败）
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class SagClient:
    """SAG 服务客户端：先取 token，再调接口（401 自动换发重试一次）。

    默认以 ``requests`` 模块本身作为 session（而非 Session 实例），这样
    ``patch("requests.post")`` 仍能生效 —— knowledge-navigation 现有十余个
    SAG 测试依赖该打桩方式。需要连接池复用时可显式传入 ``session``。
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        token_path: str | None = None,
        source_ids: list[str] | None = None,
        timeout: float = 30.0,
        session: Any | None = None,
    ) -> None:
        self.base_url = (
            base_url or _env("KN_SAG_API_URL", "SAG_API_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.token_path = Path(
            os.path.expanduser(token_path or _env("SAG_TOKEN_PATH") or DEFAULT_TOKEN_PATH)
        )
        self.source_ids = list(
            source_ids
            if source_ids is not None
            else [s.strip() for s in _env("KN_SAG_SOURCE_IDS").split(",") if s.strip()]
        )
        self.timeout = timeout
        self._session: Any = session if session is not None else requests
        self._token = token or ""

    # ── 第一步：取 token ────────────────────────────────────────────────

    def get_token(self, force: bool = False) -> str:
        """返回可用 Bearer token；取不到返回空串（调用方按无鉴权处理）。

        优先级：进程内缓存 → 环境变量 → token 文件。``force=True`` 时跳过缓存
        重新解析（换发后由 :meth:`refresh_token` 内部维护缓存，一般无需使用）。
        """
        if not force and self._token:
            return self._token
        token = (
            self._token
            or _env("KN_SAG_AUTH_TOKEN", "SAG_AUTH_TOKEN")
            or self._read_token_file()
        )
        self._token = token
        return token

    def _read_token_file(self) -> str:
        try:
            if self.token_path.is_file():
                return self.token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("读取 SAG token 文件失败 %s: %s", self.token_path, exc)
        return ""

    def _write_token_file(self, token: str) -> None:
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(token, encoding="utf-8")
            os.chmod(self.token_path, 0o600)
        except OSError as exc:
            logger.warning("回写 SAG token 文件失败 %s: %s", self.token_path, exc)

    def refresh_token(self) -> str | None:
        """调用 ``/api/v1/auth/login`` 换发 token，成功后回写文件与进程内缓存。

        未配置 password 时不发送该字段 —— 服务端在此情况下不校验密码，仅按
        name（或回退首个用户）匹配，故单机场景可无凭据完成换发。

        Returns:
            新 token；换发失败返回 None（调用方按原失败处理，不抛异常）。
        """
        payload: dict[str, Any] = {
            "name": _env("KN_SAG_AUTH_NAME", "SAG_AUTH_NAME") or DEFAULT_AUTH_NAME,
            "email": _env("KN_SAG_AUTH_EMAIL", "SAG_AUTH_EMAIL"),
        }
        if password := _env("KN_SAG_AUTH_PASSWORD", "SAG_AUTH_PASSWORD"):
            payload["password"] = password

        try:
            resp = self._session.post(
                f"{self.base_url}{AUTH_LOGIN_PATH}", json=payload, timeout=15
            )
        except Exception as exc:
            logger.warning("SAG token 换发请求失败: %s", exc)
            return None

        if resp.status_code != 200:
            logger.warning(
                "SAG token 换发失败: HTTP %s %s", resp.status_code, resp.text[:200]
            )
            return None

        try:
            token = (resp.json() or {}).get("access_token", "")
        except Exception:
            token = ""
        if not token:
            logger.warning("SAG token 换发响应缺少 access_token")
            return None

        self._token = token
        self._write_token_file(token)
        logger.info("SAG token 已换发并回写 %s", self.token_path)
        return token

    # ── 第二步：调用（核心公共方法） ────────────────────────────────────

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        timeout: float | None = None,
        retry_on_401: bool = True,
    ) -> Any:
        """先取 token 注入 Bearer 再发请求；401 时换发 token 并重试一次。

        这是本模块收敛的核心：调用方无需关心 token 从哪来、过期了怎么办。
        """
        url = f"{self.base_url}{path}"
        call = getattr(self._session, method.lower())
        effective_timeout = timeout or self.timeout

        token = self.get_token()
        headers = {"Authorization": f"Bearer {token}"} if token else None
        resp = call(url, json=json, headers=headers, timeout=effective_timeout)

        if resp.status_code == 401 and retry_on_401:
            if new_token := self.refresh_token():
                resp = call(
                    url,
                    json=json,
                    headers={"Authorization": f"Bearer {new_token}"},
                    timeout=effective_timeout,
                )
        return resp

    # ── 业务封装：search / ingest / health ──────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 3,
        source_ids: list[str] | None = None,
        strategy: str = "vector",
        timeout: float | None = None,
    ) -> list[dict]:
        """语义检索，返回 sections 列表。

        字段名对齐 SAG 服务端契约（top_k / strategy / source_ids）。

        Raises:
            SagClientError: 非 200 响应（携带 status_code 供熔断判定）。
        """
        payload = {
            "query": query,
            "top_k": top_k,
            "strategy": strategy,
            "source_ids": source_ids if source_ids is not None else self.source_ids,
        }
        resp = self.request("POST", SEARCH_PATH, json=payload, timeout=timeout)
        if resp.status_code != 200:
            raise SagClientError(
                f"SAG search HTTP {resp.status_code}", status_code=resp.status_code
            )
        return (resp.json() or {}).get("sections", [])

    def ingest(
        self,
        title: str,
        text: str,
        metadata: dict | None = None,
        source_id: str | None = None,
        timeout: float = 180.0,
        max_retries: int = 3,
        base_delay: float = 5.0,
    ) -> str | None:
        """写入单篇文档，返回 documentId；失败返回 None。

        5xx 与超时按指数退避重试 ``max_retries`` 次；4xx 直接失败不重试。
        """
        sid = source_id or (self.source_ids[0] if self.source_ids else "")
        if not sid:
            raise ValueError("SAG ingest 缺少 source_id（构造时传 source_ids 或调用时传 source_id）")

        payload = {
            "title": title,
            "text": text,
            "metadata": metadata or {},
            "chunking": {"maxTokens": 8192},
        }
        path = INGEST_PATH_TMPL.format(source_id=sid)

        for attempt in range(max_retries):
            try:
                resp = self.request("POST", path, json=payload, timeout=timeout)
            except requests.exceptions.Timeout as exc:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt) + random.uniform(0, 1)
                    logger.warning(
                        "SAG ingest 超时，重试 %d/%d（%.1fs）...", attempt + 1, max_retries, delay
                    )
                    time.sleep(delay)
                    continue
                logger.error("SAG ingest 最终超时: %s", exc)
                return None
            except Exception as exc:
                logger.error("SAG ingest 异常: %s", exc)
                return None

            if resp.status_code in (200, 201):
                data = resp.json() or {}
                doc_id = data.get("documentId") or data.get("id", "")
                return doc_id or None

            last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
            if 500 <= resp.status_code < 600 and attempt < max_retries - 1:
                delay = base_delay * (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    "SAG ingest %d，重试 %d/%d（%.1fs）...",
                    resp.status_code, attempt + 1, max_retries, delay,
                )
                time.sleep(delay)
                continue

            logger.error("SAG ingest 失败: %s", last_error)
            return None
        return None

    def health_check(self, timeout: float = 5.0) -> bool:
        """服务健康检查。

        先探 ``/api/v1/system/health``，失败再探根路径。健康检查为公开端点，
        故关闭 401 重试（避免把无效 token 误判为服务不可用）。
        """
        try:
            if self.request("GET", HEALTH_PATH, timeout=timeout, retry_on_401=False).status_code == 200:
                return True
        except Exception:
            pass
        try:
            return self.request("GET", "/", timeout=timeout, retry_on_401=False).status_code < 500
        except Exception:
            return False


# ── 进程级共享实例与模块级便捷函数 ──────────────────────────────────────

_default_client: SagClient | None = None
_default_lock = threading.Lock()


def get_client(**kwargs: Any) -> SagClient:
    """获取进程级共享 ``SagClient``（首次调用时按 kwargs 构造，之后忽略）。"""
    global _default_client
    if _default_client is None:
        with _default_lock:
            if _default_client is None:
                _default_client = SagClient(**kwargs)
    return _default_client


def refresh_sag_token(
    base_url: str | None = None, token_path: str | None = None
) -> str | None:
    """模块级：换发 SAG token（外部脚本 / 运维入口可直接调用）。"""
    return SagClient(base_url=base_url, token_path=token_path).refresh_token()


def sag_search(query: str, **kwargs: Any) -> list[dict]:
    """模块级：SAG 语义检索，返回 sections 列表。"""
    return get_client().search(query, **kwargs)


def sag_ingest(title: str, text: str, **kwargs: Any) -> str | None:
    """模块级：SAG 文档写入，返回 documentId。"""
    return get_client().ingest(title, text, **kwargs)


def sag_health_check(timeout: float = 5.0) -> bool:
    """模块级：SAG 健康检查。"""
    return get_client().health_check(timeout=timeout)
