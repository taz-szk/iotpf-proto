from pathlib import Path

from sqlalchemy.orm import Session

from app.models.rag import DocChunk
from app.services.doc_chunking import chunk_html, chunk_markdown
from app.services.ollama_client import embed

REPO_ROOT = Path(__file__).resolve().parents[3]

DOCUMENT_GLOBS = [
    "docs/design.html",
    "docs/device-guide.html",
    "docs/install-guide.html",
    "docs/superpowers/specs/*.md",
    "CLAUDE.md",
]


def _iter_target_files() -> list[Path]:
    files: list[Path] = []
    for pattern in DOCUMENT_GLOBS:
        files.extend(sorted(REPO_ROOT.glob(pattern)))
    return files


def reindex_all_documents(db: Session) -> int:
    """対象ドキュメント全てを読み直し、doc_chunksを全削除→再構築する。
    戻り値は生成したチャンク数。"""
    db.query(DocChunk).delete()

    count = 0
    for path in _iter_target_files():
        with open(path, encoding="utf-8") as f:
            content = f.read()

        if path.suffix == ".md":
            chunks = chunk_markdown(content)
        else:
            chunks = chunk_html(content)

        source_path = str(path.relative_to(REPO_ROOT))
        for chunk in chunks:
            vector = embed(chunk["content"])
            db.add(DocChunk(
                source_path=source_path,
                heading=chunk["heading"],
                content=chunk["content"],
                embedding=vector,
            ))
            count += 1

    db.commit()
    return count
