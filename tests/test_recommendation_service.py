"""Tests for app/services/recommendation_service.py.

Covers:
- UserTower / ItemTower: init shapes, forward returns unit vector, loaded weights
- RecommendationService.rank: descending score order
- RecommendationService._build_user_embedding: wardrobe path, tag cold-start, generic fallback
- RecommendationService.recommend: full pipeline with all dependencies mocked
- Module-level singleton: init and get helpers
"""

from contextlib import ExitStack, asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.models.item import Item
from app.services.recommendation_service import (
    ItemTower,
    RecommendationService,
    UserTower,
    _EMBED_DIM,
    get_recommendation_service,
    init_recommendation_service,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIM = 512
_UNIT_VEC = np.ones(_DIM, dtype=np.float32) / np.sqrt(_DIM)


def _make_item(item_id: str = "item-1", embedding: np.ndarray | None = None) -> Item:
    return Item(
        item_id=item_id,
        domain="fashion",
        title="Navy blazer",
        price=45.0,
        image_url="https://example.com/img.jpg",
        product_url="https://poshmark.com/listing/abc",
        source="poshmark_seed",
        embedding=embedding,
        attributes={"brand": "Zara"},
    )


def _make_mock_conn(fetchall_return=None):
    """Build a mock async psycopg3 connection + cursor."""
    mock_cur = AsyncMock()
    mock_cur.fetchall = AsyncMock(return_value=fetchall_return or [])
    mock_cur.execute = AsyncMock()

    mock_cur_ctx = MagicMock()
    mock_cur_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur_ctx)  # sync call!
    mock_conn.commit = AsyncMock()

    return mock_conn, mock_cur


@asynccontextmanager
async def _mock_get_connection(mock_conn):
    yield mock_conn


def _make_service() -> RecommendationService:
    """Return a RecommendationService with Xavier-init towers (no real S3/torch call)."""
    # _load_towers_from_s3 is patched to return None → Xavier cold start
    with patch(
        "app.services.recommendation_service._load_towers_from_s3", return_value=None
    ):
        svc = RecommendationService(s3_client=MagicMock(), bucket="test-bucket")
    return svc


# ---------------------------------------------------------------------------
# UserTower
# ---------------------------------------------------------------------------


class TestUserTower:
    def test_xavier_init_produces_correct_shape(self):
        tower = UserTower()
        assert tower.W.shape == (_DIM, _DIM)
        assert tower.W.dtype == np.float32

    def test_xavier_init_scale_is_within_bounds(self):
        tower = UserTower()
        scale = np.sqrt(6.0 / (_DIM + _DIM))
        assert tower.W.max() <= scale + 1e-6
        assert tower.W.min() >= -scale - 1e-6

    def test_loaded_weights_stored_as_float32(self):
        weights = np.eye(_DIM, dtype=np.float64)
        tower = UserTower(weights=weights)
        assert tower.W.dtype == np.float32
        assert tower.W.shape == (_DIM, _DIM)

    def test_forward_returns_512_dim_vector(self):
        tower = UserTower()
        result = tower.forward(_UNIT_VEC.copy())
        assert result.shape == (_DIM,)

    def test_forward_returns_unit_vector(self):
        tower = UserTower()
        x = np.random.randn(_DIM).astype(np.float32)
        x /= np.linalg.norm(x)
        result = tower.forward(x)
        assert abs(np.linalg.norm(result) - 1.0) < 1e-5

    def test_forward_identity_weights_preserves_direction(self):
        tower = UserTower(weights=np.eye(_DIM, dtype=np.float32))
        x = _UNIT_VEC.copy()
        result = tower.forward(x)
        np.testing.assert_allclose(result, x, rtol=1e-5)

    def test_forward_handles_near_zero_norm_gracefully(self):
        tower = UserTower(weights=np.zeros((_DIM, _DIM), dtype=np.float32))
        result = tower.forward(
            _UNIT_VEC.copy()
        )  # projected = 0 vector; should not raise
        assert result.shape == (_DIM,)


# ---------------------------------------------------------------------------
# ItemTower
# ---------------------------------------------------------------------------


class TestItemTower:
    def test_xavier_init_produces_correct_shape(self):
        tower = ItemTower()
        assert tower.W.shape == (_DIM, _DIM)
        assert tower.W.dtype == np.float32

    def test_forward_returns_unit_vector(self):
        tower = ItemTower()
        x = np.random.randn(_DIM).astype(np.float32)
        x /= np.linalg.norm(x)
        result = tower.forward(x)
        assert abs(np.linalg.norm(result) - 1.0) < 1e-5

    def test_loaded_weights_stored_as_float32(self):
        weights = np.eye(_DIM, dtype=np.float64)
        tower = ItemTower(weights=weights)
        assert tower.W.dtype == np.float32


# ---------------------------------------------------------------------------
# RecommendationService.rank
# ---------------------------------------------------------------------------


class TestRank:
    def test_returns_sorted_descending(self):
        # Use identity tower weights so cosine ordering is preserved after projection.
        identity = np.eye(_DIM, dtype=np.float32)
        svc = _make_service()
        svc.user_tower = UserTower(weights=identity)
        svc.item_tower = ItemTower(weights=identity)

        # item_a: embedding identical to user → cosine similarity = 1.0
        emb_a = _UNIT_VEC.copy()
        # item_b: first basis vector → cosine similarity = 1/sqrt(512) ≈ 0.044
        emb_b = np.zeros(_DIM, dtype=np.float32)
        emb_b[0] = 1.0

        item_a = _make_item("a", embedding=emb_a)
        item_b = _make_item("b", embedding=emb_b)

        # Pass in reverse order to confirm sort works
        ranked = svc.rank(_UNIT_VEC, [item_b, item_a])

        assert ranked[0][0].item_id == "a"
        assert ranked[0][1] > ranked[1][1]

    def test_scores_are_floats(self):
        svc = _make_service()
        item = _make_item("x", embedding=_UNIT_VEC.copy())
        ranked = svc.rank(_UNIT_VEC, [item])
        assert isinstance(ranked[0][1], float)

    def test_empty_candidates_returns_empty_list(self):
        svc = _make_service()
        ranked = svc.rank(_UNIT_VEC, [])
        assert ranked == []

    def test_items_without_embeddings_are_encoded_on_the_fly(self):
        svc = _make_service()
        item = _make_item("no-emb", embedding=None)

        with patch(
            "app.services.embedding_service.encode_text", return_value=_UNIT_VEC.copy()
        ):
            ranked = svc.rank(_UNIT_VEC, [item])

        assert len(ranked) == 1
        assert isinstance(ranked[0][1], float)

    def test_all_scores_bounded_by_unit_vectors(self):
        """Cosine similarity of unit vectors must be in [-1, 1]."""
        svc = _make_service()
        items = [_make_item(str(i), embedding=_UNIT_VEC.copy()) for i in range(5)]
        ranked = svc.rank(_UNIT_VEC, items)
        for _, score in ranked:
            assert -1.0 - 1e-5 <= score <= 1.0 + 1e-5


# ---------------------------------------------------------------------------
# RecommendationService._build_user_embedding
# ---------------------------------------------------------------------------


class TestBuildUserEmbedding:
    async def test_uses_wardrobe_embeddings_when_present(self):
        svc = _make_service()
        wardrobe_emb = _UNIT_VEC.tolist()
        mock_conn, _ = _make_mock_conn(fetchall_return=[(wardrobe_emb,)])

        with patch(
            "app.services.recommendation_service.get_connection",
            return_value=_mock_get_connection(mock_conn),
        ):
            result = await svc._build_user_embedding("user-1", {})

        assert result.shape == (_DIM,)
        assert abs(np.linalg.norm(result) - 1.0) < 1e-5

    async def test_cold_start_encodes_style_tags(self):
        svc = _make_service()
        mock_conn, _ = _make_mock_conn(fetchall_return=[])  # no wardrobe embeddings

        with patch(
            "app.services.recommendation_service.get_connection",
            return_value=_mock_get_connection(mock_conn),
        ):
            with patch(
                "app.services.embedding_service.encode_text",
                return_value=_UNIT_VEC.copy(),
            ) as mock_encode:
                prefs = {"styles": ["streetwear"], "colors": ["black"]}
                result = await svc._build_user_embedding("user-1", prefs)

        # encode_text should be called once per tag (2 tags)
        assert mock_encode.call_count == 2
        assert result.shape == (_DIM,)

    async def test_generic_fallback_when_no_wardrobe_and_no_tags(self):
        svc = _make_service()
        mock_conn, _ = _make_mock_conn(fetchall_return=[])

        with patch(
            "app.services.recommendation_service.get_connection",
            return_value=_mock_get_connection(mock_conn),
        ):
            with patch(
                "app.services.embedding_service.encode_text",
                return_value=_UNIT_VEC.copy(),
            ) as mock_encode:
                result = await svc._build_user_embedding("user-1", {})

        mock_encode.assert_called_once_with("casual everyday clothing")
        assert result.shape == (_DIM,)

    async def test_result_is_always_passed_through_user_tower(self):
        """The returned vector must be the UserTower projection, not the raw mean."""
        svc = _make_service()
        wardrobe_emb = _UNIT_VEC.tolist()
        mock_conn, _ = _make_mock_conn(fetchall_return=[(wardrobe_emb,)])

        tower_output = np.zeros(_DIM, dtype=np.float32)
        tower_output[0] = 1.0  # distinctive marker

        with patch(
            "app.services.recommendation_service.get_connection",
            return_value=_mock_get_connection(mock_conn),
        ):
            with patch.object(svc.user_tower, "forward", return_value=tower_output):
                result = await svc._build_user_embedding("user-1", {})

        np.testing.assert_array_equal(result, tower_output)

    async def test_skips_wrong_shape_wardrobe_embeddings(self):
        """Embeddings with shape != (512,) should be silently dropped."""
        svc = _make_service()
        bad_emb = np.ones(128, dtype=np.float32).tolist()  # wrong dim
        mock_conn, _ = _make_mock_conn(fetchall_return=[(bad_emb,)])

        with patch(
            "app.services.recommendation_service.get_connection",
            return_value=_mock_get_connection(mock_conn),
        ):
            with patch(
                "app.services.embedding_service.encode_text",
                return_value=_UNIT_VEC.copy(),
            ):
                # Should fall through to tag/generic fallback without raising
                result = await svc._build_user_embedding("user-1", {})

        assert result.shape == (_DIM,)


# ---------------------------------------------------------------------------
# RecommendationService.recommend — full pipeline
#
# recommend() uses lazy imports inside the method body, so all service patches
# must target the source modules (not recommendation_service.<name>).
# ---------------------------------------------------------------------------

# Shared patch targets for the recommend() pipeline
_PATCH_LLM = "app.services.llm_service.generate_search_query"
_PATCH_ENCODE = "app.services.embedding_service.encode_text"
_PATCH_CACHE_LOOKUP = "app.services.vector_cache.lookup"
_PATCH_CACHE_STORE = "app.services.vector_cache.store"
_PATCH_CATALOG = "app.services.dev_catalog_service.search"
_PATCH_CONN = "app.services.recommendation_service.get_connection"
_PATCH_EXPLAIN = "app.services.llm_service.generate_explanation"



class TestRecommend:
    def _svc(self) -> RecommendationService:
        return _make_service()

    async def test_returns_empty_list_when_no_candidates(self):
        svc = self._svc()
        mock_conn, _ = _make_mock_conn(fetchall_return=[])

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(
                patch(_PATCH_CACHE_LOOKUP, new=AsyncMock(return_value=None))
            )
            stack.enter_context(patch(_PATCH_CATALOG, new=AsyncMock(return_value=[])))
            stack.enter_context(
                patch(_PATCH_CACHE_STORE, new=AsyncMock(return_value=None))
            )
            stack.enter_context(
                patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn))
            )
            result = await svc.recommend(
                user_id="u1",
                location="London",
                weather_context={"temp_c": 18.0, "condition": "Sunny"},
                style_preferences={},
            )

        assert result == []

    async def test_returns_top_k_results(self):
        svc = self._svc()
        candidates = [_make_item(str(i), embedding=_UNIT_VEC.copy()) for i in range(20)]
        mock_conn, _ = _make_mock_conn(fetchall_return=[])

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(
                patch(_PATCH_CACHE_LOOKUP, new=AsyncMock(return_value=None))
            )
            stack.enter_context(
                patch(_PATCH_CATALOG, new=AsyncMock(return_value=candidates))
            )
            stack.enter_context(
                patch(_PATCH_CACHE_STORE, new=AsyncMock(return_value="id"))
            )
            stack.enter_context(
                patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn))
            )
            result = await svc.recommend(
                user_id="u1",
                location="London",
                weather_context={"temp_c": 22.0, "condition": "Clear"},
                style_preferences={},
                top_k=5,
            )

        assert len(result) == 5

    async def test_results_are_product_recommendation_objects(self):
        from app.models.product import ProductRecommendation

        svc = self._svc()
        candidates = [_make_item("i1", embedding=_UNIT_VEC.copy())]
        mock_conn, _ = _make_mock_conn(fetchall_return=[])

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(
                patch(_PATCH_CACHE_LOOKUP, new=AsyncMock(return_value=None))
            )
            stack.enter_context(
                patch(_PATCH_CATALOG, new=AsyncMock(return_value=candidates))
            )
            stack.enter_context(
                patch(_PATCH_CACHE_STORE, new=AsyncMock(return_value="id"))
            )
            stack.enter_context(
                patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn))
            )
            result = await svc.recommend(
                user_id="u1",
                location="NYC",
                weather_context={"temp_c": 15.0, "condition": "Cloudy"},
                style_preferences={},
            )

        assert all(isinstance(r, ProductRecommendation) for r in result)

    async def test_uses_cached_candidates_on_hit(self):
        svc = self._svc()
        cached_items = [_make_item("cached", embedding=_UNIT_VEC.copy())]
        mock_conn, _ = _make_mock_conn(fetchall_return=[])
        mock_catalog = AsyncMock(return_value=[])

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(
                patch(
                    _PATCH_CACHE_LOOKUP,
                    new=AsyncMock(return_value=(cached_items, "cache-id-123")),
                )
            )
            stack.enter_context(patch(_PATCH_CATALOG, new=mock_catalog))
            stack.enter_context(
                patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn))
            )
            result = await svc.recommend(
                user_id="u1",
                location="LA",
                weather_context={"temp_c": 25.0, "condition": "Sunny"},
                style_preferences={},
            )

        # Catalog search should NOT be called on cache hit
        mock_catalog.assert_not_called()
        assert result[0].item_id == "cached"

    async def test_calls_store_on_cache_miss(self):
        svc = self._svc()
        candidates = [_make_item("x", embedding=_UNIT_VEC.copy())]
        mock_conn, _ = _make_mock_conn(fetchall_return=[])
        mock_store = AsyncMock(return_value="new-cache-id")

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(
                patch(_PATCH_CACHE_LOOKUP, new=AsyncMock(return_value=None))
            )
            stack.enter_context(
                patch(_PATCH_CATALOG, new=AsyncMock(return_value=candidates))
            )
            stack.enter_context(patch(_PATCH_CACHE_STORE, new=mock_store))
            stack.enter_context(
                patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn))
            )
            await svc.recommend(
                user_id="u1",
                location="SF",
                weather_context={"temp_c": 16.0, "condition": "Fog"},
                style_preferences={},
            )

        mock_store.assert_called_once()

    async def test_explanation_populated_when_requested(self):
        svc = self._svc()
        candidates = [_make_item("i1", embedding=_UNIT_VEC.copy())]
        mock_conn, _ = _make_mock_conn(fetchall_return=[])

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(
                patch(_PATCH_CACHE_LOOKUP, new=AsyncMock(return_value=None))
            )
            stack.enter_context(
                patch(_PATCH_CATALOG, new=AsyncMock(return_value=candidates))
            )
            stack.enter_context(
                patch(_PATCH_CACHE_STORE, new=AsyncMock(return_value="id"))
            )
            stack.enter_context(
                patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn))
            )
            stack.enter_context(
                patch(
                    _PATCH_EXPLAIN,
                    new=AsyncMock(return_value="Great picks for today!"),
                )
            )
            result = await svc.recommend(
                user_id="u1",
                location="Paris",
                weather_context={"temp_c": 20.0, "condition": "Clear"},
                style_preferences={},
                include_explanation=True,
            )

        assert result[0].llm_explanation == "Great picks for today!"

    async def test_explanation_none_when_not_requested(self):
        svc = self._svc()
        candidates = [_make_item("i1", embedding=_UNIT_VEC.copy())]
        mock_conn, _ = _make_mock_conn(fetchall_return=[])

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(
                patch(_PATCH_CACHE_LOOKUP, new=AsyncMock(return_value=None))
            )
            stack.enter_context(
                patch(_PATCH_CATALOG, new=AsyncMock(return_value=candidates))
            )
            stack.enter_context(
                patch(_PATCH_CACHE_STORE, new=AsyncMock(return_value="id"))
            )
            stack.enter_context(
                patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn))
            )
            result = await svc.recommend(
                user_id="u1",
                location="Paris",
                weather_context={"temp_c": 20.0, "condition": "Clear"},
                style_preferences={},
                include_explanation=False,
            )

        assert result[0].llm_explanation is None

    async def test_similarity_scores_are_rounded_to_4dp(self):
        svc = self._svc()
        candidates = [_make_item("i1", embedding=_UNIT_VEC.copy())]
        mock_conn, _ = _make_mock_conn(fetchall_return=[])

        with ExitStack() as stack:
            stack.enter_context(patch(_PATCH_LLM, new=AsyncMock(return_value="query")))
            stack.enter_context(patch(_PATCH_ENCODE, return_value=_UNIT_VEC.copy()))
            stack.enter_context(
                patch(_PATCH_CACHE_LOOKUP, new=AsyncMock(return_value=None))
            )
            stack.enter_context(
                patch(_PATCH_CATALOG, new=AsyncMock(return_value=candidates))
            )
            stack.enter_context(
                patch(_PATCH_CACHE_STORE, new=AsyncMock(return_value="id"))
            )
            stack.enter_context(
                patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn))
            )
            result = await svc.recommend(
                user_id="u1",
                location="Tokyo",
                weather_context={"temp_c": 22.0, "condition": "Clear"},
                style_preferences={},
            )

        score = result[0].similarity_score
        assert score == round(score, 4)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def setup_method(self):
        """Reset the singleton before each test."""
        import app.services.recommendation_service as mod

        mod._recommendation_service = None

    def teardown_method(self):
        """Clean up singleton after each test."""
        import app.services.recommendation_service as mod

        mod._recommendation_service = None

    def test_get_recommendation_service_raises_before_init(self):
        import app.services.recommendation_service as mod

        mod._recommendation_service = None
        with pytest.raises(RuntimeError, match="not initialized"):
            get_recommendation_service()

    def test_init_recommendation_service_creates_singleton(self):
        with patch(
            "app.services.recommendation_service._load_towers_from_s3",
            return_value=None,
        ):
            with patch("boto3.client", return_value=MagicMock()):
                with patch("app.core.config.config") as mock_cfg:
                    mock_cfg.weather_bucket_name = "test-bucket"
                    init_recommendation_service()

        svc = get_recommendation_service()
        assert svc is not None

    def test_init_recommendation_service_is_idempotent(self):
        """Calling init twice should not replace the existing singleton."""
        with patch(
            "app.services.recommendation_service._load_towers_from_s3",
            return_value=None,
        ):
            with patch("boto3.client", return_value=MagicMock()):
                with patch("app.core.config.config") as mock_cfg:
                    mock_cfg.weather_bucket_name = "test-bucket"
                    init_recommendation_service()
                    first = get_recommendation_service()
                    init_recommendation_service()  # second call — must be a no-op
                    second = get_recommendation_service()

        assert first is second
