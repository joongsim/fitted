"""CLIP ViT-B/32 text encoder with lazy import and module-level singleton cache."""

import logging

import numpy as np

logger = logging.getLogger(__name__)

# Module-level singletons — None until first call
_model = None
_tokenizer = None
_CLIP_MODEL = "ViT-B-32"
_CLIP_PRETRAINED = "laion400m_e32"


def _load_model():
    """Load CLIP once; cache for the lifetime of the warm Lambda instance."""
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer

    import open_clip  # lazy import — torch is not loaded until this line
    import torch  # noqa: F401 — imported here to keep cold-start cost deferred

    logger.info("Loading CLIP model %s (first call)", _CLIP_MODEL)
    model, _, _ = open_clip.create_model_and_transforms(
        _CLIP_MODEL, pretrained=_CLIP_PRETRAINED
    )
    model.eval()
    tokenizer = open_clip.get_tokenizer(_CLIP_MODEL)
    _model, _tokenizer = model, tokenizer
    return _model, _tokenizer


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
    import torch

    model, tokenizer = _load_model()

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


def reset_model_for_testing() -> None:
    """Reset cached singletons — call in test teardown to avoid state leakage."""
    global _model, _tokenizer
    _model = None
    _tokenizer = None
