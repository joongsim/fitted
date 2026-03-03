"""Tests for app/services/preference_reranker.py.

Covers:
- Bradley-Terry MM algorithm (_bradley_terry_mm): convergence, win ordering
- get_preference_scores: cold start (no pairs), pairs → scores
- rerank: empty scores pass-through, alpha blending, ordering correctness
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.models.item import Item
from app.services.preference_reranker import (
    _bradley_terry_mm,
    get_preference_scores,
    rerank,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_ID = "00000000-0000-0000-0000-000000000001"
_DIM = 512


def _make_item(item_id: str) -> Item:
    return Item(
        item_id=item_id,
        domain="fashion",
        title=f"Item {item_id}",
        price=50.0,
        image_url="",
        product_url="",
        source="poshmark_seed",
        embedding=np.ones(_DIM, dtype=np.float32) / np.sqrt(_DIM),
        attributes={},
    )


def _make_mock_conn(fetchall_return=None):
    mock_cur = AsyncMock()
    mock_cur.fetchall = AsyncMock(return_value=fetchall_return or [])
    mock_cur.execute = AsyncMock()

    mock_cur_ctx = MagicMock()
    mock_cur_ctx.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur_ctx)
    mock_conn.commit = AsyncMock()

    return mock_conn, mock_cur


@asynccontextmanager
async def _mock_get_connection(mock_conn):
    yield mock_conn


_PATCH_CONN = "app.services.preference_reranker.get_connection"


# ---------------------------------------------------------------------------
# _bradley_terry_mm (unit — no I/O)
# ---------------------------------------------------------------------------


class TestBradleyTerryMM:
    def test_item_with_more_wins_gets_higher_strength(self):
        # item_a wins 3 comparisons, item_b wins 1
        wins = {"a": 3, "b": 1}
        comparisons = {("a", "b"): 4}
        scores = _bradley_terry_mm(wins, comparisons, ["a", "b"])

        assert scores["a"] > scores["b"]

    def test_all_items_have_positive_strengths(self):
        wins = {"x": 2, "y": 1, "z": 3}
        comparisons = {("x", "y"): 3, ("x", "z"): 5, ("y", "z"): 5}
        scores = _bradley_terry_mm(wins, comparisons, ["x", "y", "z"])

        assert all(v > 0 for v in scores.values())

    def test_symmetric_wins_produce_similar_strengths(self):
        # Equal wins → strengths should be close
        wins = {"a": 2, "b": 2}
        comparisons = {("a", "b"): 4}
        scores = _bradley_terry_mm(wins, comparisons, ["a", "b"])

        assert abs(scores["a"] - scores["b"]) < 0.1

    def test_returns_score_for_every_input_item(self):
        wins = {"a": 1}
        comparisons = {("a", "b"): 1}
        scores = _bradley_terry_mm(wins, comparisons, ["a", "b"])

        assert set(scores.keys()) == {"a", "b"}

    def test_dominant_item_wins_all_comparisons(self):
        # item_a beats b, b beats c — transitive preference chain
        wins = {"a": 2, "b": 1, "c": 0}
        comparisons = {("a", "b"): 2, ("b", "c"): 1}
        scores = _bradley_terry_mm(wins, comparisons, ["a", "b", "c"])

        assert scores["a"] > scores["b"]


# ---------------------------------------------------------------------------
# get_preference_scores
# ---------------------------------------------------------------------------


class TestGetPreferenceScores:
    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_pairs(self):
        mock_conn, _ = _make_mock_conn(fetchall_return=[])

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await get_preference_scores(_USER_ID)

        assert result == {}

    @pytest.mark.asyncio
    async def test_item_with_all_wins_gets_highest_score(self):
        # item_a wins both comparisons
        rows = [
            ("item_a", "item_b", "a"),
            ("item_a", "item_c", "a"),
        ]
        mock_conn, _ = _make_mock_conn(fetchall_return=rows)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await get_preference_scores(_USER_ID)

        assert result["item_a"] > result.get("item_b", 0)
        assert result["item_a"] > result.get("item_c", 0)

    @pytest.mark.asyncio
    async def test_returns_score_for_all_items_in_pairs(self):
        rows = [("item_a", "item_b", "a")]
        mock_conn, _ = _make_mock_conn(fetchall_return=rows)

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            result = await get_preference_scores(_USER_ID)

        assert "item_a" in result
        assert "item_b" in result

    @pytest.mark.asyncio
    async def test_queries_by_user_id(self):
        mock_conn, mock_cur = _make_mock_conn(fetchall_return=[])

        with patch(_PATCH_CONN, return_value=_mock_get_connection(mock_conn)):
            await get_preference_scores(_USER_ID)

        mock_cur.execute.assert_awaited_once()
        params = mock_cur.execute.call_args[0][1]
        assert _USER_ID in params


# ---------------------------------------------------------------------------
# rerank
# ---------------------------------------------------------------------------


class TestRerank:
    def test_empty_scores_returns_original_order(self):
        item_a = _make_item("a")
        item_b = _make_item("b")
        ranked = [(item_a, 0.9), (item_b, 0.5)]

        result = rerank(ranked, preference_scores={})

        assert result[0][0].item_id == "a"
        assert result[1][0].item_id == "b"

    def test_preferred_item_promoted_when_alpha_high(self):
        item_a = _make_item("a")
        item_b = _make_item("b")
        # item_b has lower two-tower score but strong preference
        ranked = [(item_a, 0.9), (item_b, 0.1)]
        # item_b has much higher preference score
        pref_scores = {"a": 1.0, "b": 10.0}

        result = rerank(ranked, pref_scores, alpha=0.9)

        assert result[0][0].item_id == "b"

    def test_alpha_zero_preserves_two_tower_order(self):
        item_a = _make_item("a")
        item_b = _make_item("b")
        ranked = [(item_a, 0.9), (item_b, 0.1)]
        pref_scores = {"a": 1.0, "b": 100.0}

        result = rerank(ranked, pref_scores, alpha=0.0)

        # alpha=0 → pure two-tower order
        assert result[0][0].item_id == "a"

    def test_unseen_items_get_neutral_pref_score(self):
        item_a = _make_item("a")  # has preference score
        item_b = _make_item("b")  # NOT in preference_scores — should get 0.5
        ranked = [(item_a, 0.5), (item_b, 0.5)]
        pref_scores = {"a": 2.0}  # item_b not present

        # With equal two-tower scores, item_a (pref=1.0 normalised) should beat
        # item_b (pref=0.5 neutral)
        result = rerank(ranked, pref_scores, alpha=0.5)

        assert result[0][0].item_id == "a"

    def test_returns_list_of_same_length(self):
        items = [_make_item(str(i)) for i in range(10)]
        ranked = [(item, float(i) / 10) for i, item in enumerate(items)]
        pref_scores = {str(i): float(i) for i in range(10)}

        result = rerank(ranked, pref_scores, alpha=0.3)

        assert len(result) == 10

    def test_combined_scores_are_floats(self):
        item_a = _make_item("a")
        ranked = [(item_a, 0.8)]
        pref_scores = {"a": 5.0}

        result = rerank(ranked, pref_scores)

        _, score = result[0]
        assert isinstance(score, float)

    def test_all_same_preference_score_preserves_two_tower_order(self):
        items = [_make_item(str(i)) for i in range(3)]
        ranked = [(items[0], 0.9), (items[1], 0.6), (items[2], 0.3)]
        # Equal preference scores → normalised all become 0.5 → two-tower order preserved
        pref_scores = {"0": 1.0, "1": 1.0, "2": 1.0}

        result = rerank(ranked, pref_scores, alpha=0.5)

        ids = [item.item_id for item, _ in result]
        assert ids == ["0", "1", "2"]
