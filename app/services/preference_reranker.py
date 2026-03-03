"""
Bradley-Terry preference reranker.

After the two-tower model ranks candidates by CLIP-space cosine similarity, this
module re-orders them using the user's pairwise preference signals stored in the
``preference_pairs`` table.

## Why Bradley-Terry?

Cosine similarity in CLIP space measures *objective* relevance — how close an
item's text/image embedding is to the user's embedding. Bradley-Terry measures
*personal taste* — which of two items the user actually preferred when shown
both side-by-side. These are complementary signals: a user might find two items
equally relevant to their aesthetic but reliably prefer one brand, colourway, or
silhouette over another.

Bradley-Terry model:

    P(i beats j) = w_i / (w_i + w_j)

where ``w_i`` is the "strength" of item *i*. The strengths are fit by maximum
likelihood estimation using the MM (minorization-maximization) algorithm — no
external dependencies, pure numpy.

## Cold-start behaviour

When a user has no preference_pairs recorded yet, ``get_preference_scores``
returns an empty dict and ``rerank`` returns the two-tower ranked list unchanged.
The function is safe to call on every request; it degrades to a no-op
automatically.

## Score blending

The final score blends the two-tower similarity with the Bradley-Terry preference
score using a tunable ``alpha`` parameter:

    combined = (1 - alpha) * similarity_score + alpha * pref_score_normalised

``alpha=0.3`` is the default. This weights preference data conservatively during
the cold-start period when the signal is sparse and noisy. Once the training
pipeline (Week 8) produces learned two-tower weights, you may wish to reduce
alpha further as the two-tower scores become more informative.
"""

import logging

import numpy as np

from app.models.item import Item
from app.services.db_service import get_connection

logger = logging.getLogger(__name__)

_MM_MAX_ITER = 100
_MM_TOL = 1e-6


def _bradley_terry_mm(
    wins: dict[str, int],
    comparisons: dict[tuple[str, str], int],
    item_ids: list[str],
) -> dict[str, float]:
    """
    Fit a Bradley-Terry model using the MM (minorization-maximization) algorithm.

    The MM update rule is:

        w_i(new) = wins_i / sum_{j != i}( n_{ij} / (w_i + w_j) )

    where ``wins_i`` is the number of times item i was preferred, and ``n_{ij}``
    is the total number of comparisons between items i and j.

    Convergence is declared when the maximum absolute change in any weight drops
    below ``_MM_TOL``, or after ``_MM_MAX_ITER`` iterations — whichever comes
    first.

    Args:
        wins: {item_id: count of times this item was preferred}.
        comparisons: {(item_a_id, item_b_id): total comparisons between them}.
                     Keys are always in canonical order (a < b lexicographically).
        item_ids: Ordered list of all item IDs with at least one comparison.

    Returns:
        {item_id: Bradley-Terry strength} — positive floats, not normalised.
    """
    n = len(item_ids)
    idx = {iid: i for i, iid in enumerate(item_ids)}

    # Build comparison matrix (symmetric): N[i, j] = total comparisons between i and j
    N = np.zeros((n, n), dtype=np.float64)
    for (a, b), cnt in comparisons.items():
        i, j = idx[a], idx[b]
        N[i, j] += cnt
        N[j, i] += cnt

    win_vec = np.array([wins.get(iid, 0) for iid in item_ids], dtype=np.float64)
    w = np.ones(n, dtype=np.float64)  # initialise all strengths to 1

    for iteration in range(_MM_MAX_ITER):
        w_prev = w.copy()
        for i in range(n):
            denominator = np.sum(N[i] / (w[i] + w))
            if denominator > 0 and win_vec[i] > 0:
                w[i] = win_vec[i] / denominator
            # Items with zero wins keep w[i] = 1 (small positive constant)

        # Re-normalise to avoid numerical drift
        w = w / w.sum() * n

        max_change = np.max(np.abs(w - w_prev))
        if max_change < _MM_TOL:
            logger.debug(
                "Bradley-Terry MM converged in %d iterations (max_change=%.2e)",
                iteration + 1,
                max_change,
            )
            break

    return {iid: float(w[idx[iid]]) for iid in item_ids}


async def get_preference_scores(user_id: str) -> dict[str, float]:
    """
    Fetch this user's ``preference_pairs`` and fit a Bradley-Terry model.

    Args:
        user_id: UUID of the requesting user.

    Returns:
        ``{item_id: strength}`` dict where higher means the user tends to prefer
        this item in pairwise comparisons. Returns ``{}`` when the user has no
        preference pairs (cold start — caller should treat this as "no reranking").
    """
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT item_a_id, item_b_id, preferred
                FROM preference_pairs
                WHERE user_id = %s
                """,
                (user_id,),
            )
            rows = await cur.fetchall()

    if not rows:
        logger.debug(
            "preference_reranker: no preference pairs for user_id=%s — returning empty scores",
            user_id,
        )
        return {}

    wins: dict[str, int] = {}
    comparisons: dict[tuple[str, str], int] = {}

    for item_a_id, item_b_id, preferred in rows:
        # Track wins
        winner = item_a_id if preferred == "a" else item_b_id
        wins[winner] = wins.get(winner, 0) + 1

        # Track total comparisons in canonical (sorted) order to keep the dict symmetric
        key = (min(item_a_id, item_b_id), max(item_a_id, item_b_id))
        comparisons[key] = comparisons.get(key, 0) + 1

    all_items = list(
        {item_a_id for item_a_id, _, _ in rows}
        | {item_b_id for _, item_b_id, _ in rows}
    )

    scores = _bradley_terry_mm(wins, comparisons, all_items)
    logger.info(
        "preference_reranker: fitted Bradley-Terry on %d pairs for user_id=%s (%d unique items)",
        len(rows),
        user_id,
        len(all_items),
    )
    return scores


def rerank(
    ranked: list[tuple[Item, float]],
    preference_scores: dict[str, float],
    alpha: float = 0.3,
) -> list[tuple[Item, float]]:
    """
    Blend two-tower similarity scores with Bradley-Terry preference scores.

    The combined score is:

        combined = (1 - alpha) * similarity_score + alpha * pref_score_normalised

    where ``pref_score_normalised`` is the Bradley-Terry strength scaled to
    [0, 1] across the current candidate set. Items not present in
    ``preference_scores`` receive a pref_score of 0.5 (the midpoint), so they
    are neither promoted nor demoted relative to each other.

    When ``preference_scores`` is empty (cold start), the function returns
    ``ranked`` unchanged.

    Args:
        ranked: List of (Item, two_tower_score) pairs, sorted descending by score.
        preference_scores: Output of ``get_preference_scores`` — {item_id: strength}.
        alpha: Weight given to the preference score (0 = pure two-tower, 1 = pure preference).

    Returns:
        Re-sorted list of (Item, combined_score) pairs, descending.
    """
    if not preference_scores or alpha == 0.0:
        return ranked

    # Normalise preference strengths to [0, 1]
    strengths = list(preference_scores.values())
    min_s = min(strengths)
    max_s = max(strengths)
    span = max_s - min_s

    def _normalise(s: float) -> float:
        return (s - min_s) / span if span > 1e-9 else 0.5

    combined: list[tuple[Item, float]] = []
    for item, sim_score in ranked:
        raw_strength = preference_scores.get(item.item_id)
        if raw_strength is not None:
            pref_score = _normalise(raw_strength)
        else:
            pref_score = 0.5  # neutral for unseen items

        score = (1.0 - alpha) * sim_score + alpha * pref_score
        combined.append((item, score))

    combined.sort(key=lambda x: x[1], reverse=True)
    logger.debug(
        "preference_reranker.rerank: alpha=%.2f %d items reranked", alpha, len(combined)
    )
    return combined
