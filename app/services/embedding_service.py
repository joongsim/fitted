"""CLIP ViT-B/32 text and image encoder with lazy import and module-level singleton cache."""

import io
import logging
import os

import numpy as np

logger = logging.getLogger(__name__)

# Module-level singletons — None until first call
_model = None
_tokenizer = None
_transform = None
_CLIP_MODEL = "ViT-B-32"
_CLIP_PRETRAINED = "laion400m_e32"


def _load_model_and_transform():
    """Load CLIP once; cache model, tokenizer, and image transform for the lifetime of the process."""
    global _model, _tokenizer, _transform
    if _model is not None:
        return _model, _tokenizer, _transform

    import open_clip  # lazy import — torch is not loaded until this line
    import torch  # noqa: F401 — imported here to keep cold-start cost deferred

    logger.info("Loading CLIP model %s (first call)", _CLIP_MODEL)
    model, _, preprocess_val = open_clip.create_model_and_transforms(
        _CLIP_MODEL, pretrained=_CLIP_PRETRAINED
    )
    model.eval()
    tokenizer = open_clip.get_tokenizer(_CLIP_MODEL)
    _model, _tokenizer, _transform = model, tokenizer, preprocess_val
    return _model, _tokenizer, _transform


def encode_text(text: str) -> np.ndarray:
    """
    Encode a text string using the CLIP ViT-B/32 text encoder.

    Returns a 512-dim float32 numpy array, L2-normalized (unit vector).
    L2 normalization means cosine similarity == dot product, which is faster
    to compute at ranking time.

    Args:
        text: Input string. Automatically truncated to CLIP's 77-token limit.

    Returns:
        np.ndarray of shape (512,), dtype float32, unit norm.
    """
    if _remote_url():
        return _remote_encode_text(text)

    import torch

    model, tokenizer, _ = _load_model_and_transform()

    tokens = tokenizer([text])
    with torch.no_grad():
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)  # L2 normalize

    embedding: np.ndarray = features.cpu().numpy().astype(np.float32)[0]
    logger.debug(
        "encode_text: text=%r shape=%s norm=%.4f",
        text[:80],
        embedding.shape,
        float(np.linalg.norm(embedding)),
    )
    return embedding


def encode_image(url_or_s3_key: str) -> np.ndarray:
    """
    Encode an image using the CLIP ViT-B/32 image encoder.

    Accepts a public URL (https://...) or an S3 key (wardrobe-images/...).
    Returns a 512-dim float32 numpy array, L2-normalized unit vector — same
    embedding space as encode_text.

    IMPORTANT: Only runs on EC2. Do not import this function in Lambda code.
    Image encoder weights are ~290MB; Lambda's unzipped limit is 250MB.

    Args:
        url_or_s3_key: Public URL or S3 key.

    Returns:
        np.ndarray of shape (512,), dtype float32, unit norm.
    """
    if _remote_url():
        # Fetch image bytes on EC2 (AWS creds stay server-side), send only bytes to remote
        if url_or_s3_key.startswith("http"):
            import requests

            response = requests.get(url_or_s3_key, timeout=10)
            response.raise_for_status()
            image_bytes = response.content
        else:
            import boto3
            import os as _os

            s3 = boto3.client("s3")
            bucket = _os.environ.get("S3_BUCKET", "fitted-wardrobe-images")
            obj = s3.get_object(Bucket=bucket, Key=url_or_s3_key)
            image_bytes = obj["Body"].read()
        return _remote_encode_image(image_bytes)

    import torch
    from PIL import Image

    model, _, transform = _load_model_and_transform()

    if url_or_s3_key.startswith("http"):
        import requests

        logger.debug("encode_image: fetching URL %s", url_or_s3_key[:120])
        response = requests.get(url_or_s3_key, timeout=10)
        response.raise_for_status()
        image_bytes = response.content
    else:
        import boto3

        logger.debug("encode_image: fetching S3 key %s", url_or_s3_key)
        s3 = boto3.client("s3")
        import os

        bucket = os.environ.get("S3_BUCKET", "fitted-wardrobe-images")
        obj = s3.get_object(Bucket=bucket, Key=url_or_s3_key)
        image_bytes = obj["Body"].read()

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    tensor = transform(image).unsqueeze(0)  # (1, 3, 224, 224)

    with torch.no_grad():
        features = model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)  # L2 normalize

    embedding: np.ndarray = features.cpu().numpy().astype(np.float32)[0]
    logger.debug(
        "encode_image: key=%r shape=%s norm=%.4f",
        url_or_s3_key[:80],
        embedding.shape,
        float(np.linalg.norm(embedding)),
    )
    return embedding


def reset_model_for_testing() -> None:
    """Reset cached singletons — call in test teardown to avoid state leakage."""
    global _model, _tokenizer, _transform
    _model = None
    _tokenizer = None
    _transform = None


def _remote_url() -> str | None:
    """Return the remote embedding server base URL, or None if not configured."""
    return os.environ.get("EMBEDDING_SERVICE_URL")


def _remote_encode_text(text: str) -> np.ndarray:
    """POST text to the remote embedding server and return a 512-dim float32 ndarray."""
    import httpx

    url = _remote_url()
    resp = httpx.post(f"{url}/embed/text", json={"text": text}, timeout=30.0)
    resp.raise_for_status()
    embedding = np.array(resp.json()["embedding"], dtype=np.float32)
    logger.debug(
        "_remote_encode_text: %r -> shape %s (remote)", text[:80], embedding.shape
    )
    return embedding


def _remote_encode_image(image_bytes: bytes) -> np.ndarray:
    """POST raw image bytes to the remote embedding server and return a 512-dim float32 ndarray."""
    import httpx

    url = _remote_url()
    resp = httpx.post(
        f"{url}/embed/image",
        files={"file": ("image.jpg", image_bytes, "image/jpeg")},
        timeout=30.0,
    )
    resp.raise_for_status()
    embedding = np.array(resp.json()["embedding"], dtype=np.float32)
    logger.debug(
        "_remote_encode_image: %d bytes -> shape %s (remote)",
        len(image_bytes),
        embedding.shape,
    )
    return embedding
