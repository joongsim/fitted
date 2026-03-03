"""Tests for domain.py, domain_factory.py, and domains/fashion.py."""

import numpy as np
import pytest
from unittest.mock import patch

from app.models.item import Item
from app.services.domain import Domain
from app.services.domains.fashion import FashionDomain

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIM = 512
_UNIT_VEC = np.ones(_DIM, dtype=np.float32) / np.sqrt(_DIM)


def _fake_encode_text(text: str) -> np.ndarray:
    """Deterministic unit vector — avoids loading CLIP in tests."""
    return _UNIT_VEC.copy()


def _make_item(**overrides) -> Item:
    defaults = dict(
        item_id="item-1",
        domain="fashion",
        title="Navy blazer",
        price=45.0,
        image_url="https://example.com/img.jpg",
        product_url="https://poshmark.com/listing/abc",
        source="poshmark_seed",
        embedding=None,
        attributes={},
    )
    defaults.update(overrides)
    return Item(**defaults)


# ---------------------------------------------------------------------------
# Domain Protocol — structural subtyping
# ---------------------------------------------------------------------------


def test_fashion_domain_satisfies_domain_protocol():
    """FashionDomain should be recognised as a Domain via isinstance check."""
    assert isinstance(FashionDomain(), Domain)


# ---------------------------------------------------------------------------
# FashionDomain.encode_query
# ---------------------------------------------------------------------------


class TestEncodeQuery:
    def test_returns_unit_vector(self):
        with patch(
            "app.services.domains.fashion.encode_text",
            side_effect=_fake_encode_text,
        ):
            domain = FashionDomain()
            result = domain.encode_query({"query_text": "casual shirt"})
        assert result.shape == (_DIM,)
        assert abs(np.linalg.norm(result) - 1.0) < 1e-5

    def test_composite_includes_weather(self):
        """encode_query should embed a composite that includes weather context."""
        captured = {}

        def _capture(text: str) -> np.ndarray:
            captured["text"] = text
            return _UNIT_VEC.copy()

        with patch("app.services.domains.fashion.encode_text", side_effect=_capture):
            FashionDomain().encode_query(
                {
                    "query_text": "blue chinos",
                    "weather_context": "sunny 22°C",
                }
            )

        assert "weather: sunny 22°C" in captured["text"]
        assert "blue chinos" in captured["text"]

    def test_composite_includes_colors_and_styles(self):
        captured = {}

        def _capture(text: str) -> np.ndarray:
            captured["text"] = text
            return _UNIT_VEC.copy()

        with patch("app.services.domains.fashion.encode_text", side_effect=_capture):
            FashionDomain().encode_query(
                {
                    "query_text": "jacket",
                    "style_preferences": {
                        "colors": ["navy", "white"],
                        "styles": ["smart casual"],
                    },
                }
            )

        assert "colors: navy, white" in captured["text"]
        assert "style: smart casual" in captured["text"]

    def test_empty_inputs_still_calls_encode_text(self):
        """Even with no inputs, encode_query should call encode_text (with empty string)."""
        called = {"n": 0}

        def _track(text: str) -> np.ndarray:
            called["n"] += 1
            return _UNIT_VEC.copy()

        with patch("app.services.domains.fashion.encode_text", side_effect=_track):
            FashionDomain().encode_query({})

        assert called["n"] == 1

    def test_omits_empty_parts_from_composite(self):
        """Parts with no content should not appear in the composite string."""
        captured = {}

        def _capture(text: str) -> np.ndarray:
            captured["text"] = text
            return _UNIT_VEC.copy()

        with patch("app.services.domains.fashion.encode_text", side_effect=_capture):
            FashionDomain().encode_query(
                {
                    "query_text": "shirt",
                    "style_preferences": {"colors": [], "styles": []},
                }
            )

        # No trailing ". colors: " or ". style: " fragments
        assert "colors:" not in captured["text"]
        assert "style:" not in captured["text"]


# ---------------------------------------------------------------------------
# FashionDomain.encode_item
# ---------------------------------------------------------------------------


class TestEncodeItem:
    def test_returns_cached_embedding_when_present(self):
        """If item.embedding is set, encode_item should return it without calling encode_text."""
        cached = np.random.randn(_DIM).astype(np.float32)
        item = _make_item(embedding=cached)

        with patch(
            "app.services.domains.fashion.encode_text",
            side_effect=AssertionError("should not be called"),
        ):
            result = FashionDomain().encode_item(item)

        np.testing.assert_array_equal(result, cached)

    def test_encodes_title_when_no_embedding(self):
        """encode_item should call encode_text when item.embedding is None."""
        called = {"text": None}

        def _capture(text: str) -> np.ndarray:
            called["text"] = text
            return _UNIT_VEC.copy()

        item = _make_item(title="Linen shirt", embedding=None)

        with patch("app.services.domains.fashion.encode_text", side_effect=_capture):
            FashionDomain().encode_item(item)

        assert "Linen shirt" in called["text"]

    def test_includes_brand_category_colors_condition_in_text(self):
        captured = {}

        def _capture(text: str) -> np.ndarray:
            captured["text"] = text
            return _UNIT_VEC.copy()

        item = _make_item(
            title="Oxford shirt",
            embedding=None,
            attributes={
                "brand": "Ralph Lauren",
                "category": "Tops",
                "colors": ["white", "blue"],
                "condition": "like new",
            },
        )

        with patch("app.services.domains.fashion.encode_text", side_effect=_capture):
            FashionDomain().encode_item(item)

        text = captured["text"]
        assert "Ralph Lauren" in text
        assert "Tops" in text
        assert "white, blue" in text
        assert "like new" in text

    def test_skips_missing_attributes_gracefully(self):
        """encode_item should not raise when optional attribute keys are absent."""
        with patch(
            "app.services.domains.fashion.encode_text",
            side_effect=_fake_encode_text,
        ):
            FashionDomain().encode_item(_make_item(attributes={}))


# ---------------------------------------------------------------------------
# FashionDomain.parse_item
# ---------------------------------------------------------------------------


class TestParseItem:
    def test_parses_minimal_row(self):
        raw = {
            "item_id": "abc123",
            "title": "White sneakers",
            "price": "55.00",
            "image_url": "https://example.com/img.jpg",
            "product_url": "https://poshmark.com/listing/abc123",
            "source": "poshmark_seed",
        }
        item = FashionDomain().parse_item(raw)
        assert item.item_id == "abc123"
        assert item.title == "White sneakers"
        assert item.price == 55.0
        assert item.domain == "fashion"
        assert item.embedding is None

    def test_parses_embedding_as_list(self):
        """psycopg3 may return the vector column as a Python list."""
        raw_vec = [0.1] * _DIM
        item = FashionDomain().parse_item(
            {
                "item_id": "e1",
                "embedding": raw_vec,
            }
        )
        assert isinstance(item.embedding, np.ndarray)
        assert item.embedding.shape == (_DIM,)
        assert item.embedding.dtype == np.float32

    def test_parses_embedding_as_ndarray(self):
        """pgvector's register_vector returns an np.ndarray directly."""
        raw_vec = np.ones(_DIM, dtype=np.float32)
        item = FashionDomain().parse_item(
            {
                "item_id": "e2",
                "embedding": raw_vec,
            }
        )
        assert isinstance(item.embedding, np.ndarray)

    def test_null_embedding_stays_none(self):
        item = FashionDomain().parse_item({"item_id": "e3", "embedding": None})
        assert item.embedding is None

    def test_defaults_for_missing_fields(self):
        """parse_item should not raise on a sparse row dict."""
        item = FashionDomain().parse_item({"item_id": "sparse"})
        assert item.title == ""
        assert item.price == 0.0
        assert item.image_url == ""
        assert item.product_url == ""
        assert item.source == ""
        assert item.attributes == {}

    def test_uses_domain_from_row_when_present(self):
        item = FashionDomain().parse_item({"item_id": "d1", "domain": "furniture"})
        assert item.domain == "furniture"

    def test_defaults_domain_to_fashion(self):
        item = FashionDomain().parse_item({"item_id": "d2"})
        assert item.domain == "fashion"


# ---------------------------------------------------------------------------
# FashionDomain.preference_context
# ---------------------------------------------------------------------------


class TestPreferenceContext:
    def test_returns_dict_with_required_keys(self):
        item = _make_item(item_id="ctx-item")
        query = {"query_text": "jacket"}
        ctx = FashionDomain().preference_context(query, item)
        assert ctx["item_id"] == "ctx-item"
        assert ctx["domain"] == "fashion"
        assert ctx["query"] == query


# ---------------------------------------------------------------------------
# domain_factory.get_domain
# ---------------------------------------------------------------------------


class TestGetDomain:
    def test_returns_fashion_domain_by_default(self):
        from app.services.domain_factory import get_domain

        domain = get_domain("fashion")
        assert isinstance(domain, FashionDomain)

    def test_reads_env_var_when_name_is_none(self, monkeypatch):
        from app.services.domain_factory import get_domain

        monkeypatch.setenv("DOMAIN", "fashion")
        domain = get_domain(None)
        assert isinstance(domain, FashionDomain)

    def test_defaults_to_fashion_when_env_var_absent(self, monkeypatch):
        from app.services.domain_factory import get_domain

        monkeypatch.delenv("DOMAIN", raising=False)
        domain = get_domain(None)
        assert isinstance(domain, FashionDomain)

    def test_raises_for_unknown_domain(self):
        from app.services.domain_factory import get_domain

        with pytest.raises(ValueError, match="Unknown domain"):
            get_domain("furniture")

    def test_returns_new_instance_each_call(self):
        from app.services.domain_factory import get_domain

        d1 = get_domain("fashion")
        d2 = get_domain("fashion")
        assert d1 is not d2

    def test_returned_domain_satisfies_protocol(self):
        from app.services.domain_factory import get_domain

        assert isinstance(get_domain("fashion"), Domain)
