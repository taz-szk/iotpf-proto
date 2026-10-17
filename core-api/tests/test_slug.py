from app.services.slug import slugify


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Acme  Corp!!", is_taken=lambda s: False) == "acme-corp"


def test_slugify_strips_non_alnum_and_leading_trailing_hyphens():
    assert slugify("  -- 日本語Test株式会社 --  ", is_taken=lambda s: False) == "test"


def test_slugify_empty_after_cleanup_falls_back_to_tenant():
    assert slugify("日本語のみ", is_taken=lambda s: False) == "tenant"


def test_slugify_appends_suffix_on_collision():
    taken = {"acme-corp", "acme-corp-2"}
    assert slugify("Acme Corp", is_taken=lambda s: s in taken) == "acme-corp-3"


def test_slugify_no_collision_no_suffix():
    assert slugify("Acme Corp", is_taken=lambda s: False) == "acme-corp"
