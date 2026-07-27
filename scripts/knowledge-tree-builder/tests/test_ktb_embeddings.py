"""embedding API 单元测试。"""

from unittest.mock import MagicMock, patch

from knowledge_tree_builder.core import embeddings


def _resp(data):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"data": data}
    return r


def test_batch_embed_rejects_count_mismatch() -> None:
    """数量不匹配时，部分失败用 None 占位而非整体返回 None。

    改进后行为：调用方可区分"全部失败"和"部分失败"，便于降级处理。
    """
    with patch.object(embeddings, "time") as mock_time, patch.object(embeddings.requests, "post") as post:
        post.return_value = _resp([{"index": 0, "embedding": [0.1, 0.2]}])
        result = embeddings.batch_embed(["a", "b"], api_key="k")
    # 修复后：部分失败时返回 [None, None]（与输入同长度），不再整体返回 None
    assert result == [None, None]
    assert post.call_count == 3
    assert mock_time.sleep.call_count == 2


def test_batch_embed_preserves_index_order() -> None:
    with patch.object(embeddings.requests, "post") as post:
        post.return_value = _resp([
            {"index": 1, "embedding": [0.2]},
            {"index": 0, "embedding": [0.1]},
        ])
        result = embeddings.batch_embed(["a", "b"], api_key="k")
    assert result == [[0.1], [0.2]]
