"""数据库适配器 mock 测试"""

from unittest.mock import MagicMock, patch

import pytest

from clustering_analysis.adapters.database import DatabaseAdapter


class TestDatabaseAdapter:
    """测试 DatabaseAdapter 的基本行为"""

    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_init_stores_db_url(self, mock_connect: MagicMock) -> None:
        adapter = DatabaseAdapter("postgresql://localhost/test")
        assert adapter.db_url == "postgresql://localhost/test"
        # 初始化时不应立即连接
        mock_connect.assert_not_called()

    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_conn_lazy_connect(self, mock_connect: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        adapter = DatabaseAdapter("postgresql://localhost/test")
        conn = adapter.conn

        mock_connect.assert_called_once_with("postgresql://localhost/test")
        assert conn is mock_conn

    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_fetch_memory_units(self, mock_connect: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchall.return_value = [
            (1, "hermes", "text1", "[0.1]"),
            (2, "hermes", "text2", "[0.2]"),
        ]

        adapter = DatabaseAdapter("postgresql://localhost/test")
        result = adapter.fetch_memory_units(100)

        assert len(result) == 2
        assert result[0] == (1, "hermes", "text1", "[0.1]")
        mock_cur.execute.assert_called_once()

    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_fetch_unit_entities(self, mock_connect: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchall.return_value = [
            (1, "entity_1"),
            (2, "entity_2"),
        ]

        adapter = DatabaseAdapter("postgresql://localhost/test")
        result = adapter.fetch_unit_entities(["1", "2"])

        assert len(result) == 2
        mock_cur.execute.assert_called_once()

    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_fetch_unit_text(self, mock_connect: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = ("sample text",)

        adapter = DatabaseAdapter("postgresql://localhost/test")
        result = adapter.fetch_unit_text("123")

        assert result == "sample text"
        mock_cur.execute.assert_called_once()

    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_fetch_unit_text_none(self, mock_connect: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = None

        adapter = DatabaseAdapter("postgresql://localhost/test")
        result = adapter.fetch_unit_text("123")

        assert result is None

    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_cleanup_old_clusters(self, mock_connect: MagicMock, capsys: pytest.CaptureFixture) -> None:
        adapter = DatabaseAdapter("postgresql://localhost/test")
        adapter.cleanup_old_clusters(force=True, bank_id="test_bank")

        captured = capsys.readouterr()
        assert "保留全部聚类数据" in captured.out

    @patch("clustering_analysis.adapters.database.psycopg2.extras.execute_values")
    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_apply_to_db(self, mock_connect: MagicMock, mock_execute_values: MagicMock, capsys: pytest.CaptureFixture) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        # 模拟 entities INSERT 返回 UUID
        mock_execute_values.return_value = [("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",)]
        mock_cur.fetchone.return_value = ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]

        adapter = DatabaseAdapter("postgresql://localhost/test")
        adapter.apply_to_db(
            entity_write_plan=[{"entity_id": "e1", "canonical_name": "测试实体", "member_count": 1}],
            unit_entity_write_plan=[{"unit_id": "123", "entity_id": "e1"}],
            memory_link_plan=[{"from_id": "123", "to_id": "456", "link_type": "causes", "weight": 0.8}],
            enriched_texts={"123": ["test"]},
        )

        captured = capsys.readouterr()
        assert "写入 entities:" in captured.out
        assert "写入 unit_entities:" in captured.out

    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_update_embedding(self, mock_connect: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        adapter = DatabaseAdapter("postgresql://localhost/test")
        adapter.update_embedding("123", [0.1, 0.2, 0.3])

        mock_cur.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_fetch_all_links(self, mock_connect: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchall.return_value = [
            ("id_1", "id_2", "causes"),
            ("id_2", "id_3", "causes"),
            ("id_3", "id_1", "caused_by"),
        ]

        adapter = DatabaseAdapter("postgresql://localhost/test")
        result = adapter.fetch_all_links(bank_id="hermes")

        assert len(result) == 3
        assert ("id_1", "id_2", "causes") in result
        assert ("id_3", "id_1", "caused_by") in result
        # 验证 SQL 中含有 bank_id 过滤
        sql = mock_cur.execute.call_args[0][0]
        assert "bank_id" in sql

    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_fetch_all_links_empty(self, mock_connect: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchall.return_value = []

        adapter = DatabaseAdapter("postgresql://localhost/test")
        result = adapter.fetch_all_links(bank_id="empty_bank")

        assert result == set()

    @patch("clustering_analysis.adapters.database.psycopg2.connect")
    def test_close(self, mock_connect: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        adapter = DatabaseAdapter("postgresql://localhost/test")
        # 触发连接
        _ = adapter.conn
        adapter.close()

        mock_conn.close.assert_called_once()
