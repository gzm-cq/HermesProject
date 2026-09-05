"""test_router_emv_timeout.py — Router embedding 超时参数验收（2026-09-04）。

Rationale: 原 timeout=15s 过宽，本地 bge-m3 GPU 服务正常 0.4s。
若 GPU 服务抖动，15s 会叠加在路由慢路径上；改为 3s（7× 正常延迟余量）。
测试验证实际 HTTP 调用使用 3s timeout。
"""
import inspect
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/mnt/d/HermesProject/plugins/knowledge-navigation/src")
from knowledge_navigation.core import router as r  # noqa: E402


class TestRouterEmbeddingTimeout(unittest.TestCase):
    """Router embedding 超时参数验证。"""

    @patch("httpx.post")
    def test_embedding_timeout_is_3s(self, mock_post):
        """验证 _get_router_embedding 实际调用 httpx.post 时 timeout=3。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"embedding": [0.0] * 1024}]
        }
        mock_post.return_value = mock_resp

        # 设置 embedding 配置
        r._router_emb_config = lambda: ("bge-m3", "http://127.0.0.1:8082/v1", "", None)

        result = r._get_router_embedding("test message")

        self.assertIsNotNone(result)
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        self.assertEqual(call_kwargs["timeout"], 3,
            msg=f"Expected timeout=3, got {call_kwargs['timeout']}")

    def test_embedding_timeout_source(self):
        """源码里 _get_router_embedding 的超时字面量应为 3（非 15）。"""
        source = inspect.getsource(r._get_router_embedding)
        self.assertIn("timeout=3", source,
            msg="Source should contain timeout=3 (not 15)")


if __name__ == "__main__":
    unittest.main()
