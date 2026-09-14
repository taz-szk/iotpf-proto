from datetime import datetime, timezone
from unittest.mock import patch, mock_open, MagicMock

import pytest

from app.services.rag_indexing import DOCUMENT_GLOBS, get_last_reindexed_at, reindex_all_documents


def test_document_globs_excludes_plans_directory():
    globs_str = " ".join(DOCUMENT_GLOBS)
    assert "specs" in globs_str
    assert "plans" not in globs_str


def test_get_last_reindexed_at_returns_max_created_at():
    mock_db = MagicMock()
    expected = datetime(2026, 9, 14, 1, 0, 0, tzinfo=timezone.utc)
    mock_db.query.return_value.scalar.return_value = expected
    assert get_last_reindexed_at(mock_db) == expected


def test_get_last_reindexed_at_returns_none_when_no_chunks():
    mock_db = MagicMock()
    mock_db.query.return_value.scalar.return_value = None
    assert get_last_reindexed_at(mock_db) is None


def test_reindex_all_documents_replaces_existing_chunks():
    mock_db = MagicMock()
    fake_md_path = MagicMock()
    fake_md_path.__str__ = lambda self: "docs/superpowers/specs/example.md"
    fake_md_path.suffix = ".md"

    with patch("app.services.rag_indexing._iter_target_files", return_value=[fake_md_path]), \
         patch("app.services.rag_indexing.open", mock_open(read_data="# タイトル\n\n## セクション\n\n本文です。\n")), \
         patch("app.services.rag_indexing.embed", return_value=[0.1] * 768) as mock_embed:
        count = reindex_all_documents(mock_db)

    mock_db.query.return_value.delete.assert_called_once()
    assert mock_db.add.called
    assert count == mock_db.add.call_count
    mock_embed.assert_called()
    mock_db.commit.assert_called_once()


def test_reindex_all_documents_raises_when_no_target_files_found():
    mock_db = MagicMock()
    with patch("app.services.rag_indexing._iter_target_files", return_value=[]):
        with pytest.raises(RuntimeError):
            reindex_all_documents(mock_db)
    mock_db.query.return_value.delete.assert_not_called()
