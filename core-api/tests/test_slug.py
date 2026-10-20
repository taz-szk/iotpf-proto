import pytest
from app.services.slug import validate_tenant_slug, InvalidSlugError


def test_validate_tenant_slug_accepts_lowercase_alnum_hyphen():
    assert validate_tenant_slug("acme-corp") == "acme-corp"


def test_validate_tenant_slug_rejects_uppercase():
    with pytest.raises(InvalidSlugError):
        validate_tenant_slug("Acme-Corp")


def test_validate_tenant_slug_rejects_too_short():
    with pytest.raises(InvalidSlugError):
        validate_tenant_slug("ab")


def test_validate_tenant_slug_rejects_leading_hyphen():
    with pytest.raises(InvalidSlugError):
        validate_tenant_slug("-acme-corp")


def test_validate_tenant_slug_rejects_trailing_hyphen():
    with pytest.raises(InvalidSlugError):
        validate_tenant_slug("acme-corp-")


def test_validate_tenant_slug_rejects_underscore_and_other_symbols():
    with pytest.raises(InvalidSlugError):
        validate_tenant_slug("acme_corp")


def test_validate_tenant_slug_rejects_multibyte():
    with pytest.raises(InvalidSlugError):
        validate_tenant_slug("あくめ-corp")


def test_validate_tenant_slug_accepts_max_length_30():
    assert validate_tenant_slug("a" + "b" * 28 + "c") == "a" + "b" * 28 + "c"


def test_validate_tenant_slug_rejects_over_30_chars():
    with pytest.raises(InvalidSlugError):
        validate_tenant_slug("a" + "b" * 29 + "c")
