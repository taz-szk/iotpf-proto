"""管理画面(admin-ui / platform-ui)は Alpine.js を defer で読み込む。初期化が終わるまでの間、x-show などの
条件表示が効かず、全タブ・モーダル・エラー枠が素のまま一瞬見えてしまう。これを防ぐため、Alpineの最上位の
要素に x-cloak を付け、CSSで [x-cloak] を非表示にする。"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI_DIRS = [ROOT / "admin-ui", ROOT / "platform-ui"]

pytestmark = pytest.mark.skipif(not all(d.exists() for d in UI_DIRS), reason="UIディレクトリが無い環境(コンテナ内など)")

_TAG = re.compile(r"<[a-zA-Z][^<>]*?\bx-data\b[^<>]*>", re.S)


def _alpine_root_tags():
    for d in UI_DIRS:
        for f in sorted(d.glob("*.html")):
            text = f.read_text(encoding="utf-8")
            for m in _TAG.finditer(text):
                tag = m.group(0)
                # 名前付きのアプリ(x-data="fooApp()")か、x-data と x-show を併せ持つ要素(モーダルなど)が対象
                if re.search(r'x-data="\w+\(\)"', tag) or (" x-show=" in tag):
                    yield f, tag


def test_css_source_hides_cloaked_elements():
    css = (ROOT / "admin-ui" / "src" / "input.css").read_text(encoding="utf-8-sig")
    assert re.search(r"\[x-cloak\]\s*\{\s*display:\s*none\s*!important", css)


def test_built_css_contains_the_cloak_rule():
    built = (ROOT / "admin-ui" / "static" / "tailwind.css").read_text(encoding="utf-8")
    assert "[x-cloak]" in built, "npm run build:css を実行して、ビルド済みCSSを更新してください"


def test_every_alpine_root_is_cloaked():
    tags = list(_alpine_root_tags())
    assert len(tags) >= 9   # ページを増やしたときに検査対象が消えていないことの確認
    missing = [f"{f.name}: {t[:80]}" for f, t in tags if "x-cloak" not in t]
    assert not missing, "x-cloak が無い最上位要素: " + "; ".join(missing)


def test_login_page_stays_hidden_while_redirecting_a_logged_in_user():
    """ログイン済みでログイン画面を開くと、フォームが見えてから遷移する。遷移が決まったら表示しない"""
    html = (ROOT / "platform-ui" / "index.html").read_text(encoding="utf-8")
    assert "redirecting" in html
    root = _TAG.search(html).group(0)
    assert 'x-show="!redirecting"' in root
