"""
Train two-tower recommendation model on user interaction data.

Reads ``user_interactions`` from RDS, builds triplets, trains UserTower and
ItemTower with ``TripletMarginLoss(margin=0.2)``, saves weights to S3, and
logs the run to MLflow.

Triplet construction
--------------------
For each user with at least one positive interaction (click or save):
  - Anchor   : user's mean-pooled wardrobe embedding (512-dim CLIP)
  - Positive : embedding of a clicked/saved catalog item
  - Negative : embedding of a dismissed item; falls back to a random
               catalog item if no dismissals exist for the user

Only items with embeddings in ``catalog_items`` are used.  Users whose
wardrobe has no CLIP embeddings yet are skipped (cold start handled by
Xavier init at inference time).

Usage
-----
    # Open SSH tunnel to RDS first, then:
    PYTHONPATH=. \\
        DATABASE_URL=postgresql://...@localhost:5432/fitted \\
        AWS_S3_BUCKET=fitted-wardrobe-dev \\
        MLFLOW_TRACKING_URI=http://localhost:5000 \\
        python scripts/train_two_towers.py

    # Dry run (no DB writes, no S3 upload, no MLflow logging):
    python scripts/train_two_towers.py --dry-run

Options
-------
    --epochs N      Training epochs (default: 50)
    --lr FLOAT      Learning rate (default: 1e-3)
    --margin FLOAT  Triplet loss margin (default: 0.2)
    --dry-run       Build triplets and report count; skip training + upload
"""

import argparse
import io
import logging
import pathlib
import random
import sys
from typing import Optional

import numpy as np
import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.core.config import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("train_two_towers")

_S3_MODEL_KEY = "models/two-towers/latest.pt"
_EMBED_DIM = 512


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_interactions(conn: psycopg.Connection) -> list[dict]:
    """
    Return all rows from ``user_interactions`` that have an item_id present
    in ``catalog_items`` with a non-null embedding.

    Returns:
        List of dicts with keys: user_id, item_id, interaction_type.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ui.user_id, ui.item_id, ui.interaction_type
            FROM user_interactions ui
            JOIN catalog_items ci ON ci.item_id = ui.item_id
            WHERE ci.embedding IS NOT NULL
              AND ui.interaction_type IN ('click', 'save', 'dismiss')
            """
        )
        rows = cur.fetchall()
    logger.info("Loaded %d interactions with embeddings", len(rows))
    return [
        {"user_id": str(r[0]), "item_id": r[1], "interaction_type": r[2]} for r in rows
    ]


def load_item_embeddings(conn: psycopg.Connection) -> dict[str, np.ndarray]:
    """
    Return a dict mapping item_id → 512-dim float32 embedding for all
    catalog_items that have a non-null embedding.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT item_id, embedding FROM catalog_items WHERE embedding IS NOT NULL"
        )
        rows = cur.fetchall()
    result = {}
    for item_id, emb_raw in rows:
        vec = (
            np.array(emb_raw, dtype=np.float32)
            if not isinstance(emb_raw, np.ndarray)
            else emb_raw.astype(np.float32)
        )
        if vec.shape == (_EMBED_DIM,):
            result[item_id] = vec
    logger.info("Loaded embeddings for %d catalog items", len(result))
    return result


def load_wardrobe_embeddings(conn: psycopg.Connection) -> dict[str, np.ndarray]:
    """
    Return per-user mean-pooled wardrobe embeddings.

    Only users with at least one non-null 512-dim wardrobe embedding are
    included.  The mean vector is L2-normalized before storing.

    Returns:
        Dict mapping user_id (str) → 512-dim float32 unit vector.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id, embedding
            FROM wardrobe_items
            WHERE embedding IS NOT NULL
            """
        )
        rows = cur.fetchall()

    user_vecs: dict[str, list[np.ndarray]] = {}
    for user_id, emb_raw in rows:
        uid = str(user_id)
        vec = (
            emb_raw
            if isinstance(emb_raw, np.ndarray)
            else np.array(emb_raw, dtype=np.float32)
        )
        if vec.shape == (_EMBED_DIM,):
            user_vecs.setdefault(uid, []).append(vec.astype(np.float32))

    result = {}
    for uid, vecs in user_vecs.items():
        mean_vec = np.stack(vecs, axis=0).mean(axis=0)
        norm = np.linalg.norm(mean_vec)
        result[uid] = mean_vec / norm if norm > 1e-8 else mean_vec

    logger.info(
        "Loaded wardrobe embeddings for %d users (%d total wardrobe items)",
        len(result),
        len(rows),
    )
    return result


# ---------------------------------------------------------------------------
# Triplet construction
# ---------------------------------------------------------------------------


def build_triplets(
    interactions: list[dict],
    item_embeddings: dict[str, np.ndarray],
    user_embeddings: dict[str, np.ndarray],
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """
    Build (anchor, positive, negative) triplets.

    Strategy:
      - anchor   = user's mean-pooled wardrobe embedding
      - positive = embedding of a clicked/saved item
      - negative = embedding of a dismissed item (preferred) or random item

    Skips users with no wardrobe embeddings (cold start).

    Returns:
        List of (anchor, positive, negative) numpy triplets, each 512-dim.
    """
    all_item_ids = list(item_embeddings.keys())

    # Group interactions by user
    user_positives: dict[str, list[str]] = {}
    user_negatives: dict[str, list[str]] = {}
    for row in interactions:
        uid = row["user_id"]
        iid = row["item_id"]
        if row["interaction_type"] in ("click", "save"):
            user_positives.setdefault(uid, []).append(iid)
        elif row["interaction_type"] == "dismiss":
            user_negatives.setdefault(uid, []).append(iid)

    triplets = []
    skipped_no_wardrobe = 0
    skipped_no_positives = 0

    for uid, pos_items in user_positives.items():
        if uid not in user_embeddings:
            skipped_no_wardrobe += 1
            continue

        anchor = user_embeddings[uid]
        neg_pool = user_negatives.get(uid) or all_item_ids

        for pos_id in pos_items:
            if pos_id not in item_embeddings:
                continue
            # Pick a negative that is not the positive item
            neg_candidates = [
                n for n in neg_pool if n != pos_id and n in item_embeddings
            ]
            if not neg_candidates:
                neg_candidates = [
                    n for n in all_item_ids if n != pos_id and n in item_embeddings
                ]
            if not neg_candidates:
                continue
            neg_id = random.choice(neg_candidates)
            triplets.append((anchor, item_embeddings[pos_id], item_embeddings[neg_id]))

    if skipped_no_wardrobe:
        logger.info(
            "Skipped %d users with no wardrobe embeddings (cold start)",
            skipped_no_wardrobe,
        )
    if skipped_no_positives:
        logger.info(
            "Skipped %d users with no positive interactions", skipped_no_positives
        )

    logger.info("Built %d triplets from %d users", len(triplets), len(user_positives))
    return triplets


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train(
    triplets: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
    epochs: int,
    lr: float,
    margin: float,
) -> tuple["torch.Tensor", "torch.Tensor"]:
    """
    Train UserTower and ItemTower on the given triplets.

    Both towers are nn.Linear(512, 512, bias=False), Xavier-initialized.
    Uses TripletMarginLoss with cosine distance.

    Args:
        triplets: List of (anchor, positive, negative) numpy arrays.
        epochs:   Number of training epochs.
        lr:       Adam learning rate.
        margin:   Triplet loss margin.

    Returns:
        (user_tower_W, item_tower_W) as torch.Tensor, shape (512, 512).
    """
    import torch
    import torch.nn as nn

    user_tower = nn.Linear(_EMBED_DIM, _EMBED_DIM, bias=False)
    item_tower = nn.Linear(_EMBED_DIM, _EMBED_DIM, bias=False)
    nn.init.xavier_uniform_(user_tower.weight)
    nn.init.xavier_uniform_(item_tower.weight)

    optimizer = torch.optim.Adam(
        list(user_tower.parameters()) + list(item_tower.parameters()), lr=lr
    )
    loss_fn = nn.TripletMarginWithDistanceLoss(
        distance_function=lambda a, b: 1.0
        - torch.nn.functional.cosine_similarity(a, b),
        margin=margin,
    )

    anchors_np = np.stack([t[0] for t in triplets], axis=0)
    positives_np = np.stack([t[1] for t in triplets], axis=0)
    negatives_np = np.stack([t[2] for t in triplets], axis=0)

    anchors = torch.from_numpy(anchors_np)
    positives = torch.from_numpy(positives_np)
    negatives = torch.from_numpy(negatives_np)

    for epoch in range(1, epochs + 1):
        user_tower.train()
        item_tower.train()
        optimizer.zero_grad()

        a_proj = user_tower(anchors)
        p_proj = item_tower(positives)
        n_proj = item_tower(negatives)

        loss = loss_fn(a_proj, p_proj, n_proj)
        loss.backward()
        optimizer.step()

        if epoch % 10 == 0 or epoch == 1:
            logger.info("Epoch %d/%d  loss=%.6f", epoch, epochs, loss.item())

    return user_tower.weight.data, item_tower.weight.data


# ---------------------------------------------------------------------------
# S3 upload
# ---------------------------------------------------------------------------


def upload_weights_to_s3(
    user_w: "torch.Tensor",
    item_w: "torch.Tensor",
    bucket: str,
) -> None:
    """
    Serialize weights with torch.save and upload to S3.

    The inference code (recommendation_service.py) loads:
        state = torch.load(buffer, weights_only=True)
        user_tower_W = state["user_tower_W"].numpy()   # (512, 512)
        item_tower_W = state["item_tower_W"].numpy()   # (512, 512)

    Then applies: projected = W @ embedding  (matrix-vector multiply)

    For nn.Linear weight (out, in) = (512, 512):
        nn.Linear forward: output = input @ weight.T
        Numpy inference:   output = weight @ input
    Both are equivalent for 1-D inputs when weight is square, so saving
    the raw weight tensor is correct.
    """
    import boto3
    import torch

    state = {"user_tower_W": user_w, "item_tower_W": item_w}
    buf = io.BytesIO()
    torch.save(state, buf)
    buf.seek(0)

    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=_S3_MODEL_KEY, Body=buf.getvalue())
    logger.info("Weights uploaded to s3://%s/%s", bucket, _S3_MODEL_KEY)


# ---------------------------------------------------------------------------
# MLflow logging
# ---------------------------------------------------------------------------


def log_to_mlflow(
    params: dict,
    metrics: dict,
    user_w: "torch.Tensor",
    item_w: "torch.Tensor",
    mlflow_uri: Optional[str],
) -> None:
    """
    Log hyperparameters, final loss, and weight artifacts to MLflow.

    Silently skips if MLflow is unavailable or tracking URI is not set.
    """
    if not mlflow_uri:
        logger.info("MLFLOW_TRACKING_URI not set — skipping MLflow logging")
        return

    try:
        import mlflow
        import torch

        mlflow.set_tracking_uri(mlflow_uri)
        with mlflow.start_run(run_name="two-towers-training"):
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)

            # Save weight tensors as artifacts
            buf_u = io.BytesIO()
            buf_i = io.BytesIO()
            torch.save(user_w, buf_u)
            torch.save(item_w, buf_i)
            buf_u.seek(0)
            buf_i.seek(0)
            mlflow.log_artifact(buf_u, artifact_path="user_tower_W.pt")
            mlflow.log_artifact(buf_i, artifact_path="item_tower_W.pt")

        logger.info("MLflow run logged to %s", mlflow_uri)
    except Exception:
        logger.warning("MLflow logging failed", exc_info=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> None:
    """Load data, build triplets, train, upload weights."""
    conn = psycopg.connect(config.database_url)
    try:
        interactions = load_interactions(conn)
        item_embeddings = load_item_embeddings(conn)
        user_embeddings = load_wardrobe_embeddings(conn)
    finally:
        conn.close()

    triplets = build_triplets(interactions, item_embeddings, user_embeddings)

    if not triplets:
        logger.warning(
            "No triplets could be built. "
            "Ensure users have wardrobe embeddings and interaction history."
        )
        return

    if args.dry_run:
        logger.info(
            "[DRY RUN] Would train on %d triplets for %d epochs. Exiting.",
            len(triplets),
            args.epochs,
        )
        return

    user_w, item_w = train(triplets, args.epochs, args.lr, args.margin)

    bucket = config.s3_bucket
    upload_weights_to_s3(user_w, item_w, bucket)

    import os

    log_to_mlflow(
        params={
            "epochs": args.epochs,
            "lr": args.lr,
            "margin": args.margin,
            "n_triplets": len(triplets),
        },
        metrics={"n_triplets": len(triplets)},
        user_w=user_w,
        item_w=item_w,
        mlflow_uri=os.environ.get("MLFLOW_TRACKING_URI"),
    )

    logger.info("Training complete. Weights at s3://%s/%s", bucket, _S3_MODEL_KEY)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train two-tower recommendation model on interaction data"
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=50,
        help="Training epochs (default: 50)",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=1e-3,
        help="Adam learning rate (default: 1e-3)",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=0.2,
        help="TripletMarginLoss margin (default: 0.2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build triplets and report count; skip training and upload",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run(_parse_args())
