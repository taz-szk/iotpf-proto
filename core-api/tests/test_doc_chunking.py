from app.services.doc_chunking import chunk_html, chunk_markdown


def test_chunk_markdown_splits_by_heading():
    content = """# タイトル

イントロ文。

## セクション1

セクション1の本文です。

## セクション2

セクション2の本文です。
"""
    chunks = chunk_markdown(content)
    headings = [c["heading"] for c in chunks]
    assert "セクション1" in headings
    assert "セクション2" in headings
    section1 = next(c for c in chunks if c["heading"] == "セクション1")
    assert "セクション1の本文です" in section1["content"]


def test_chunk_markdown_handles_no_headings():
    content = "見出しなしの本文だけのドキュメント。"
    chunks = chunk_markdown(content)
    assert len(chunks) == 1
    assert chunks[0]["heading"] is None
    assert "見出しなしの本文" in chunks[0]["content"]


def test_chunk_html_splits_by_h2_and_strips_tags():
    content = """
    <html><body>
    <h1>design</h1>
    <h2>API一覧</h2>
    <p>ここにAPIの説明が入ります。</p>
    <h2>データモデル</h2>
    <p>ここにデータモデルの説明が入ります。</p>
    </body></html>
    """
    chunks = chunk_html(content)
    headings = [c["heading"] for c in chunks]
    assert "API一覧" in headings
    assert "データモデル" in headings
    api_chunk = next(c for c in chunks if c["heading"] == "API一覧")
    assert "<p>" not in api_chunk["content"]
    assert "ここにAPIの説明が入ります" in api_chunk["content"]


def test_chunk_html_handles_h3_sections():
    content = "<h2>親</h2><p>親の説明</p><h3>子</h3><p>子の説明</p>"
    chunks = chunk_html(content)
    headings = [c["heading"] for c in chunks]
    assert "親" in headings
    assert "子" in headings


def test_chunk_html_returns_single_chunk_when_no_headings():
    content = "<html><body><p>見出しが全く無い本文です。install-guide.htmlのような文書。</p></body></html>"
    chunks = chunk_html(content)
    assert len(chunks) == 1
    assert chunks[0]["heading"] is None
    assert "見出しが全く無い本文です" in chunks[0]["content"]


def test_chunk_html_splits_oversized_chunk():
    paragraphs = [f"段落{i}のテキストです。" * 30 for i in range(10)]
    body = "\n\n".join(paragraphs)
    content = f"<h2>長いセクション</h2><p>{body}</p>"
    chunks = chunk_html(content)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c["content"]) <= 1200
    assert all(c["heading"].startswith("長いセクション") for c in chunks)


def test_chunk_markdown_splits_oversized_chunk_by_paragraph():
    paragraphs = [f"段落{i}のテキストです。" * 30 for i in range(10)]
    body = "\n\n".join(paragraphs)
    content = f"## 長いセクション\n\n{body}"
    chunks = chunk_markdown(content)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c["content"]) <= 1200
