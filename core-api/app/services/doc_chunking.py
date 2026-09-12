import re


def chunk_markdown(content: str) -> list[dict]:
    """Markdownを見出し(#〜###)単位で分割する。見出しが無ければ全体を1チャンクにする。"""
    lines = content.split("\n")
    chunks: list[dict] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    def _flush():
        text = "\n".join(current_lines).strip()
        if text:
            chunks.append({"heading": current_heading, "content": text})

    for line in lines:
        m = re.match(r"^(#{1,3})\s+(.+)$", line)
        if m:
            _flush()
            current_heading = m.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
    _flush()

    return chunks


def chunk_html(content: str) -> list[dict]:
    """HTMLを<h2>/<h3>セクション単位で分割し、タグを除去したテキストにする。見出しが無い場合は全体を1チャンクにする。"""
    # <h2>または<h3>タグで本文を分割する
    parts = re.split(r"<h[23][^>]*>(.*?)</h[23]>", content, flags=re.DOTALL)
    # parts[0] は最初の見出しより前の部分（無視する）。以降は [heading, body, heading, body, ...] の繰り返し
    chunks: list[dict] = []
    if len(parts) == 1:
        # 見出しが1つも見つからない場合は、全文を1チャンクにする
        text = _strip_tags(parts[0]).strip()
        if text:
            chunks.append({"heading": None, "content": text})
        return chunks
    for i in range(1, len(parts), 2):
        heading = _strip_tags(parts[i]).strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        text = _strip_tags(body).strip()
        if text:
            chunks.append({"heading": heading, "content": text})
    return chunks


def _strip_tags(html_fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_fragment)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
