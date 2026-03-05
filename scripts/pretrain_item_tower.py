"""
Pre-train the ItemTower on catalog CLIP embeddings using a reconstruction loss.

Motivation
----------
The two-tower training script (train_two_towers.py) requires user interaction
data (clicks, saves, dismisses) to learn.  Before enough interactions
accumulate, the ItemTower runs with Xavier-initialized weights — effectively
random linear projections that add noise to the already-good CLIP embeddings.

This script gives the ItemTower a meaningful warm start by training it to
reconstruct the input CLIP embedding (i.e. learn approximately the identity
mapping):

    loss = MSE(item_tower(clip_embedding), clip_embedding)

After pre-training:
- The ItemTower acts as a smooth regularized identity transform on CLIP space
- Cosine similarity scores from the UserTower / ItemTower pipeline are
  semantically meaningful immediately, even before interaction data arrives
- train_two_towers.py can fine-tune from this warm start rather than Xavier

The UserTower is NOT pre-trained here — it will be updated jointly once
interaction data arrives.  The pre-trained ItemTower is saved alongside the
placeholder Xavier UserTower so the inference code loads both.

Usage
-----
    # Open SSH tunnel to RDS, then:
    PYTHONPATH=. \\
        DATABASE_URL=postgresql://fitted:password@localhost:5432/fitted \\
        AWS_S3_BUCKET=fitted-wardrobe-dev \\
        python scripts/pretrain_item_tower.py

    # Dry run:
    python scripts/pretrain_item_tower.py --dry-run

Options
-------
    --epochs N    Training epochs (default: 100)
    --lr FLOAT    Adam learning rate (default: 1e-3)
    --batch-size N  Items per gradient step (default: 256)
    --dry-run     Report item count; skip training and upload
"""

import argparse
import io
import logging
import pathlib
import sys

import numpy as np
import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.core.config import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("pretrain_item_tower")

_S3_MODEL_KEY = "models/two-towers/latest.pt"
_EMBED_DIM = 512


def load_catalog_embeddings(conn: psycopg.Connection) -> np.ndarray:
    """
    Load all non-null catalog_items embeddings as a float32 matrix.

    Returns:
        np.ndarray of shape (N, 512), L2-normalized rows.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT embedding FROM catalog_items WHERE embedding IS NOT NULL")
        rows = cur.fetchall()

    vecs = []
    for (emb_raw,) in rows:
        vec = (
            emb_raw
            if isinstance(emb_raw, np.ndarray)
            else np.array(emb_raw, dtype=np.float32)
        )
        if vec.shape == (_EMBED_DIM,):
            vecs.append(vec.astype(np.float32))

    if not vecs:
        return np.empty((0, _EMBED_DIM), dtype=np.float32)

    matrix = np.stack(vecs, axis=0)  # (N, 512)
    # Normalize rows — CLIP embeddings should already be unit norm, but enforce it
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms > 1e-8, norms, 1.0)
    matrix = matrix / norms
    logger.info("Loaded %d catalog embeddings, shape=%s", len(vecs), matrix.shape)
    return matrix


def pretrain(
    embeddings: np.ndarray,
    epochs: int,
    lr: float,
    batch_size: int,
) -> "torch.Tensor":
    """
    Train ItemTower to reconstruct input CLIP embeddings (MSE loss).

    Uses Xavier initialization and Adam optimizer with cosine LR schedule.

    Args:
        embeddings: (N, 512) float32 CLIP embedding matrix.
        epochs:     Training epochs.
        lr:         Adam learning rate.
        batch_size: Mini-batch size.

    Returns:
        item_tower_W: torch.Tensor of shape (512, 512) — trained weight.
    """
    import torch
    import torch.nn as nn

    item_tower = nn.Linear(_EMBED_DIM, _EMBED_DIM, bias=False)
    nn.init.xavier_uniform_(item_tower.weight)

    optimizer = torch.optim.Adam(item_tower.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    loss_fn = nn.MSELoss()

    data = torch.from_numpy(embeddings)
    n = len(data)

    for epoch in range(1, epochs + 1):
        # Shuffle mini-batches
        perm = torch.randperm(n)
        epoch_loss = 0.0
        n_batches = 0

        for start in range(0, n, batch_size):
            idx = perm[start : start + batch_size]
            batch = data[idx]

            item_tower.train()
            optimizer.zero_grad()
            projected = item_tower(batch)
            # L2-normalize projected vectors before computing loss so we train
            # the direction rather than the scale
            projected_norm = projected / (projected.norm(dim=-1, keepdim=True) + 1e-8)
            loss = loss_fn(projected_norm, batch)
            loss.backward()
            optimizer.step()

            epoch_loss += loss.item()
            n_batches += 1

        scheduler.step()

        if epoch % 20 == 0 or epoch == 1:
            logger.info(
                "Epoch %d/%d  avg_loss=%.6f  lr=%.2e",
                epoch,
                epochs,
                epoch_loss / max(n_batches, 1),
                scheduler.get_last_lr()[0],
            )

    return item_tower.weight.data


def upload_weights_to_s3(
    item_tower_W: "torch.Tensor",
    bucket: str,
) -> None:
    """
    Save pre-trained ItemTower alongside a Xavier-initialized UserTower to S3.

    If a model already exists at the S3 key (from a previous train run), load
    the existing UserTower weights so they are preserved.  Otherwise, create a
    fresh Xavier-initialized UserTower.

    The inference code expects both keys in the state dict.

    Args:
        item_tower_W: Pre-trained ItemTower weight tensor, shape (512, 512).
        bucket:       S3 bucket name.
    """
    import boto3
    import torch
    import torch.nn as nn

    # Try to load existing UserTower weights to avoid overwriting trained weights
    user_tower_W: "torch.Tensor"
    s3 = boto3.client("s3")
    try:
        response = s3.get_object(Bucket=bucket, Key=_S3_MODEL_KEY)
        buf = io.BytesIO(response["Body"].read())
        existing = torch.load(buf, map_location="cpu", weights_only=True)
        user_tower_W = existing["user_tower_W"]
        logger.info(
            "Preserving existing UserTower weights from s3://%s/%s",
            bucket,
            _S3_MODEL_KEY,
        )
    except Exception:
        logger.info("No existing model found — initializing UserTower with Xavier")
        user_linear = nn.Linear(_EMBED_DIM, _EMBED_DIM, bias=False)
        nn.init.xavier_uniform_(user_linear.weight)
        user_tower_W = user_linear.weight.data

    state = {"user_tower_W": user_tower_W, "item_tower_W": item_tower_W}
    buf = io.BytesIO()
    torch.save(state, buf)
    buf.seek(0)

    s3.put_object(Bucket=bucket, Key=_S3_MODEL_KEY, Body=buf.getvalue())
    logger.info("Pre-trained ItemTower uploaded to s3://%s/%s", bucket, _S3_MODEL_KEY)


def run(args: argparse.Namespace) -> None:
    """Main: load embeddings → pre-train → upload."""
    conn = psycopg.connect(config.database_url)
    try:
        embeddings = load_catalog_embeddings(conn)
    finally:
        conn.close()

    if len(embeddings) == 0:
        logger.warning(
            "No catalog embeddings found. Run backfill_catalog_embeddings.py first."
        )
        return

    if args.dry_run:
        logger.info(
            "[DRY RUN] Would pre-train ItemTower on %d catalog embeddings "
            "for %d epochs. Exiting.",
            len(embeddings),
            args.epochs,
        )
        return

    item_tower_W = pretrain(embeddings, args.epochs, args.lr, args.batch_size)
    upload_weights_to_s3(item_tower_W, config.s3_bucket)
    logger.info("Pre-training complete.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-train ItemTower on catalog CLIP embeddings (MSE reconstruction)"
    )
    parser.add_argument(
        "--epochs", type=int, default=100, help="Training epochs (default: 100)"
    )
    parser.add_argument(
        "--lr", type=float, default=1e-3, help="Adam learning rate (default: 1e-3)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=256,
        help="Mini-batch size (default: 256)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report item count; skip training and upload",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run(_parse_args())
