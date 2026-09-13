import re

_MAX_CHUNK_CHARS = 1200


def chunk_markdown(content: str) -> list[dict]:
    """Markdownを見出し(#〜###)単位で分割する。見出しが無ければ全体を1チャンクにする。
    1チャンクが_MAX_CHUNK_CHARSを超える場合はさらに分割する。"""
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

    return _split_oversized(chunks)


def chunk_html(content: str) -> list[dict]:
    """HTMLを<h2>/<h3>セクション単位で分割し、タグを除去したテキストにする。見出しが無い場合は全体を1チャンクにする。
    1チャンクが_MAX_CHUNK_CHARSを超える場合はさらに分割する。"""
    parts = re.split(r"<h[23][^>]*>(.*?)</h[23]>", content, flags=re.DOTALL)
    chunks: list[dict] = []
    if len(parts) == 1:
        text = _strip_tags(parts[0]).strip()
        if text:
            chunks.append({"heading": None, "content": text})
        return _split_oversized(chunks)
    for i in range(1, len(parts), 2):
        heading = _strip_tags(parts[i]).strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        text = _strip_tags(body).strip()
        if text:
            chunks.append({"heading": heading, "content": text})
    return _split_oversized(chunks)


def _split_oversized(chunks: list[dict]) -> list[dict]:
    """_MAX_CHUNK_CHARSを超えるチャンクを分割する。可能なら段落(空行)境界で、
    それが無理なら文字数で強制分割する。見出しは継承し、分割時は"(i/N)"を付与する。"""
    result: list[dict] = []
    for chunk in chunks:
        content = chunk["content"]
        if len(content) <= _MAX_CHUNK_CHARS:
            result.append(chunk)
            continue

        paragraphs = re.split(r"\n\s*\n", content)
        parts: list[str] = []
        buf = ""
        for para in paragraphs:
            if buf and len(buf) + len(para) + 2 > _MAX_CHUNK_CHARS:
                parts.append(buf)
                buf = para
            else:
                buf = f"{buf}\n\n{para}" if buf else para
        if buf:
            parts.append(buf)
        if len(parts) <= 1:
            parts = [content[i:i + _MAX_CHUNK_CHARS] for i in range(0, len(content), _MAX_CHUNK_CHARS)]

        total = len(parts)
        for i, part in enumerate(parts, start=1):
            heading = chunk["heading"]
            if total > 1:
                heading = f"{heading} ({i}/{total})" if heading else f"({i}/{total})"
            result.append({"heading": heading, "content": part.strip()})
    return result


def _strip_tags(html_fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_fragment)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
