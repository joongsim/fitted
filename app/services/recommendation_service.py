"""Two-tower recommendation model and orchestration service."""

import io
import logging
from typing import Optional

import numpy as np

from app.models.item import Item
from app.models.product import ProductRecommendation
from app.services.db_service import get_connection

logger = logging.getLogger(__name__)

_S3_MODEL_KEY = "models/two-towers/latest.pt"
_EMBED_DIM = 512


class UserTower:
    """
    Linear projection from 512-dim user embedding to 512-dim ranking space.
    Initialized with Xavier uniform weights when no pre-trained model is available.
    """

    def __init__(self, weights: Optional[np.ndarray] = None) -> None:
        if weights is not None:
            self.W: np.ndarray = weights.astype(np.float32)
            logger.info("UserTower: loaded pre-trained weights, shape=%s", self.W.shape)
        else:
            scale = np.sqrt(6.0 / (_EMBED_DIM + _EMBED_DIM))
            self.W = np.random.uniform(-scale, scale, (_EMBED_DIM, _EMBED_DIM)).astype(
                np.float32
            )
            logger.info("UserTower: Xavier-initialized (no pre-trained model found).")

    def forward(self, user_embedding: np.ndarray) -> np.ndarray:
        """
        Project user_embedding through the tower.

        Args:
            user_embedding: (512,) float32 input vector.

        Returns:
            (512,) L2-normalized float32 output vector.
        """
        projected = self.W @ user_embedding
        norm = np.linalg.norm(projected)
        return projected / norm if norm > 1e-8 else projected


class ItemTower:
    """
    Linear projection from 512-dim item embedding to 512-dim ranking space.
    Xavier-initialized when no pre-trained model is available.
    """

    def __init__(self, weights: Optional[np.ndarray] = None) -> None:
        if weights is not None:
            self.W: np.ndarray = weights.astype(np.float32)
            logger.info("ItemTower: loaded pre-trained weights, shape=%s", self.W.shape)
        else:
            scale = np.sqrt(6.0 / (_EMBED_DIM + _EMBED_DIM))
            self.W = np.random.uniform(-scale, scale, (_EMBED_DIM, _EMBED_DIM)).astype(
                np.float32
            )
            logger.info("ItemTower: Xavier-initialized (no pre-trained model found).")

    def forward(self, item_embedding: np.ndarray) -> np.ndarray:
        """
        Project item_embedding through the tower.

        Args:
            item_embedding: (512,) float32 input vector.

        Returns:
            (512,) L2-normalized float32 output vector.
        """
        projected = self.W @ item_embedding
        norm = np.linalg.norm(projected)
        return projected / norm if norm > 1e-8 else projected


def _load_towers_from_s3(s3_client, bucket: str) -> Optional[dict]:
    """
    Attempt to load two-tower weights from S3.

    The training script (Week 8) saves weights as:
        torch.save({'user_tower_W': tensor, 'item_tower_W': tensor}, path)

    torch.load returns tensors; we convert to numpy for inference.

    Returns:
        Dict with 'user_tower_W' and 'item_tower_W' as np.ndarray, or None if
        the model file doesn't exist yet (Xavier cold start).
    """
    try:
        import torch

        response = s3_client.get_object(Bucket=bucket, Key=_S3_MODEL_KEY)
        buffer = io.BytesIO(response["Body"].read())
        # weights_only=True is required to prevent arbitrary code execution.
        # torch.load deserializes a pickle stream; without this flag a
        # maliciously crafted .pt file in S3 can run arbitrary Python at
        # load time. weights_only restricts deserialization to tensors only.
        state = torch.load(buffer, map_location="cpu", weights_only=True)
        logger.info("Two-tower weights loaded from s3://%s/%s", bucket, _S3_MODEL_KEY)
        return {
            "user_tower_W": state["user_tower_W"].numpy(),
            "item_tower_W": state["item_tower_W"].numpy(),
        }
    except Exception as exc:
        from botocore.exceptions import ClientError

        if isinstance(exc, ClientError) and exc.response["Error"]["Code"] in (
            "NoSuchKey",
            "404",
        ):
            logger.info(
                "No weights at s3://%s/%s — Xavier cold start.", bucket, _S3_MODEL_KEY
            )
        else:
            logger.warning(
                "Unexpected error loading two-tower weights from s3://%s/%s",
                bucket,
                _S3_MODEL_KEY,
                exc_info=True,
            )
        return None


class RecommendationService:
    """
    Orchestrates the full recommendation pipeline:
      1. Build user embedding from wardrobe or style preferences
      2. Retrieve candidates from vector cache or dev catalog
      3. Rank candidates via UserTower + ItemTower cosine similarity
      4. Optionally generate LLM explanation
      5. Return ranked ProductRecommendation list
    """

    def __init__(self, s3_client, bucket: str, domain_name: str = "fashion") -> None:
        self._s3_client = s3_client
        self._bucket = bucket

        from app.services.domain_factory import get_domain

        self._domain = get_domain(domain_name)

        weights = _load_towers_from_s3(s3_client, bucket)
        if weights:
            self.user_tower = UserTower(weights["user_tower_W"])
            self.item_tower = ItemTower(weights["item_tower_W"])
        else:
            self.user_tower = UserTower()  # Xavier init
            self.item_tower = ItemTower()  # Xavier init

    async def _build_user_embedding(
        self,
        user_id: str,
        style_preferences: dict,
    ) -> np.ndarray:
        """
        Construct a 512-dim user vector.

        Strategy (in priority order):
          1. Query wardrobe_items for this user's CLIP embeddings.
          2. If any non-null 512-dim embeddings exist: mean-pool them, L2-normalize.
          3. Otherwise: encode each style/color tag individually then mean-pool.
          4. Absolute fallback: encode a generic clothing query.

        The mean-pool approach gives the user vector the "center of mass" of their
        wardrobe in CLIP space, naturally representing their aesthetic.

        Returns:
            512-dim float32 unit vector, already projected through UserTower.forward.
        """
        from app.services.embedding_service import encode_text

        wardrobe_embeddings = []
        async with get_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT embedding FROM wardrobe_items
                    WHERE user_id = %s AND embedding IS NOT NULL
                    """,
                    (user_id,),
                )
                rows = await cur.fetchall()

        for (emb_raw,) in rows:
            if emb_raw is not None:
                vec = (
                    emb_raw
                    if isinstance(emb_raw, np.ndarray)
                    else np.array(emb_raw, dtype=np.float32)
                )
                if vec.shape == (_EMBED_DIM,):
                    wardrobe_embeddings.append(vec)

        if wardrobe_embeddings:
            stacked = np.stack(wardrobe_embeddings, axis=0)  # (N, 512)
            mean_vec = stacked.mean(axis=0)  # (512,)
            norm = np.linalg.norm(mean_vec)
            user_raw = mean_vec / norm if norm > 1e-8 else mean_vec
            logger.info(
                "Built user embedding from %d wardrobe items: user_id=%s",
                len(wardrobe_embeddings),
                user_id,
            )
        else:
            # Cold start — encode each style/color tag individually then mean-pool.
            #
            # Why not json.dumps(style_preferences)?
            # Encoding the raw JSON dict as a string is semantically noisy: the
            # JSON syntax (braces, quotes, colons, list brackets) contributes to
            # the CLIP embedding alongside the actual content. "streetwear" and
            # '{"styles": ["streetwear"]}' produce meaningfully different vectors.
            #
            # Encoding each tag independently and averaging keeps every tag in
            # the same region of CLIP space as the item vocabulary — which is
            # where items with those style attributes live.
            style_tags = style_preferences.get("styles", []) + style_preferences.get(
                "colors", []
            )
            if style_tags:
                tag_vecs = np.stack([encode_text(tag) for tag in style_tags], axis=0)
                mean_vec = tag_vecs.mean(axis=0)
                norm = np.linalg.norm(mean_vec)
                user_raw = mean_vec / norm if norm > 1e-8 else mean_vec
                logger.info(
                    "No wardrobe embeddings — cold-start from %d style/color tags: user_id=%s",
                    len(style_tags),
                    user_id,
                )
            else:
                # Absolute fallback: user has no wardrobe and no style tags.
                # Use a broad fashion query rather than an empty/zero vector.
                user_raw = encode_text("casual everyday clothing")
                logger.info(
                    "No wardrobe embeddings and no style tags — using generic fallback: user_id=%s",
                    user_id,
                )

        return self.user_tower.forward(user_raw)

    def rank(
        self,
        user_embedding: np.ndarray,
        items: list[Item],
    ) -> list[tuple[Item, float]]:
        """
        Score each item and return (item, score) pairs sorted descending.

        For each item:
          1. Get or compute its embedding via FashionDomain.encode_item
          2. Project through ItemTower
          3. Score = dot(user_embedding, item_embedding)
             (equivalent to cosine similarity since both are unit vectors)

        Args:
            user_embedding: (512,) user vector, already projected through UserTower.
            items: Candidate items (may have None embeddings).

        Returns:
            List of (Item, float) sorted by score descending.
        """
        scored: list[tuple[Item, float]] = []
        for item in items:
            item_vec = self._domain.encode_item(item)
            projected = self.item_tower.forward(item_vec)
            score = float(np.dot(user_embedding, projected))
            scored.append((item, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    async def recommend(
        self,
        user_id: str,
        location: str,
        weather_context: dict,
        style_preferences: dict,
        top_k: int = 10,
        include_explanation: bool = False,
    ) -> list[ProductRecommendation]:
        """
        Full recommendation pipeline.

        Args:
            user_id: UUID string of the requesting user.
            location: Location string (for weather context in LLM prompt).
            weather_context: Dict with temp_c, condition, etc.
            style_preferences: User's style_preferences JSONB dict.
            top_k: Number of recommendations to return.
            include_explanation: Call LLM for natural-language explanation.

        Returns:
            Ranked list of ProductRecommendation objects.
        """
        from app.services import llm_service, vector_cache, dev_catalog_service
        from app.services.embedding_service import encode_text

        # Step 1: LLM generates a contextual search query
        query_text = await llm_service.generate_search_query(
            preferences=style_preferences,
            weather=weather_context,
        )

        # Step 2: Embed the search query
        query_embedding = encode_text(query_text)

        # Step 3: Check the vector cache
        cache_result = await vector_cache.lookup(query_embedding)
        if cache_result is not None:
            candidates, _ = cache_result
            logger.info("Using cached candidates: count=%d", len(candidates))
        else:
            # Step 4 (MISS): Fetch candidates from the Poshmark catalog
            candidates = await dev_catalog_service.search(
                query_embedding=query_embedding,
                limit=50,
            )
            if not candidates:
                logger.warning(
                    "No candidates found for user_id=%s — skipping cache store",
                    user_id,
                )
                return []
            # Step 5: Populate the cache for future requests
            await vector_cache.store(
                query_text=query_text,
                query_embedding=query_embedding,
                items=candidates,
                s3_client=self._s3_client,
                bucket=self._bucket,
            )

        if not candidates:
            logger.warning(
                "No candidates found for user_id=%s — returning empty list", user_id
            )
            return []

        # Step 6: Build the user embedding
        user_embedding = await self._build_user_embedding(user_id, style_preferences)

        # Step 7: Rank candidates via two-tower cosine similarity
        ranked = self.rank(user_embedding, candidates)

        # Step 7.5: Preference reranking (no-op when user has no preference pairs)
        from app.services import preference_reranker

        pref_scores = await preference_reranker.get_preference_scores(user_id)
        ranked = preference_reranker.rerank(ranked, pref_scores)[:top_k]

        # Step 8: Optional LLM explanation
        explanation = ""
        if include_explanation:
            top_dicts = [
                {
                    "title": item.title,
                    "price": item.price,
                    "attributes": item.attributes,
                }
                for item, _ in ranked[:3]
            ]
            explanation = await llm_service.generate_explanation(
                top_items=top_dicts,
                weather_context=weather_context,
                style_preferences=style_preferences,
            )

        # Step 9: Map to response model
        results = []
        for item, score in ranked:
            results.append(
                ProductRecommendation(
                    item_id=item.item_id,
                    title=item.title,
                    price=item.price,
                    product_url=item.product_url,
                    image_url=item.image_url or None,
                    similarity_score=round(score, 4),
                    attributes=item.attributes,
                    llm_explanation=explanation if include_explanation else None,
                )
            )

        logger.info(
            "Recommendation complete: user_id=%s top_k=%d query=%r",
            user_id,
            top_k,
            query_text,
        )
        return results


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_recommendation_service: "RecommendationService | None" = None


def init_recommendation_service() -> None:
    """
    Create the module-level RecommendationService singleton.

    Call this from the FastAPI lifespan context manager (app startup),
    after the DB pool is initialized. Safe to call multiple times — only
    creates the service on the first call.
    """
    global _recommendation_service
    if _recommendation_service is not None:
        return
    import boto3

    from app.core.config import config

    s3_client = boto3.client("s3")
    bucket = config.weather_bucket_name
    _recommendation_service = RecommendationService(s3_client=s3_client, bucket=bucket)
    logger.info("RecommendationService initialized (bucket=%s)", bucket)


def get_recommendation_service() -> RecommendationService:
    """Return the singleton, raising if not yet initialized."""
    if _recommendation_service is None:
        raise RuntimeError(
            "RecommendationService not initialized — "
            "call init_recommendation_service() at startup"
        )
    return _recommendation_service
