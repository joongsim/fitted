"""Local CLIP embedding server — run on your GPU machine, tunnel to EC2.

Usage:
    python scripts/embedding_server.py           # port 8001
    python scripts/embedding_server.py --port 9000

SSH tunnel (on local machine):
    ssh -R 8001:localhost:8001 ec2-user@<EC2_IP>

Then set on EC2 systemd service:
    Environment="EMBEDDING_SERVICE_URL=http://localhost:8001"
"""

import argparse
import io
import logging
import threading

import numpy as np
import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Fitted Embedding Server")

# Module-level singletons
_model = None
_tokenizer = None
_transform = None
_CLIP_MODEL = "ViT-B-32"
_CLIP_PRETRAINED = "laion400m_e32"
_load_lock = threading.Lock()


def _load_model():
    global _model, _tokenizer, _transform
    if _model is not None:
        return _model, _tokenizer, _transform
    with _load_lock:
        if _model is not None:
            return _model, _tokenizer, _transform
        import open_clip

        logger.info("Loading CLIP model %s...", _CLIP_MODEL)
        model, _, preprocess_val = open_clip.create_model_and_transforms(
            _CLIP_MODEL, pretrained=_CLIP_PRETRAINED
        )
        model.eval()
        tokenizer = open_clip.get_tokenizer(_CLIP_MODEL)
        _model, _tokenizer, _transform = model, tokenizer, preprocess_val
        logger.info("CLIP model loaded.")
    return _model, _tokenizer, _transform


class TextRequest(BaseModel):
    text: str


@app.post("/embed/text")
def embed_text(req: TextRequest) -> JSONResponse:
    """Encode a text string and return a 512-dim L2-normalized embedding."""
    import torch

    model, tokenizer, _ = _load_model()
    tokens = tokenizer([req.text])
    with torch.no_grad():
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)
    embedding: list[float] = features.cpu().numpy().astype(np.float32)[0].tolist()
    logger.debug("embed_text: %r -> shape (512,)", req.text[:80])
    return JSONResponse({"embedding": embedding})


@app.post("/embed/image")
def embed_image(file: UploadFile = File(...)) -> JSONResponse:
    """Encode raw image bytes and return a 512-dim L2-normalized embedding."""
    import torch
    from PIL import Image

    model, _, transform = _load_model()
    image_bytes = file.file.read()
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        logger.warning(
            "embed_image: failed to decode image (%d bytes): %s", len(image_bytes), exc
        )
        return JSONResponse({"error": "Could not decode image bytes"}, status_code=422)
    tensor = transform(image).unsqueeze(0)
    with torch.no_grad():
        features = model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
    embedding: list[float] = features.cpu().numpy().astype(np.float32)[0].tolist()
    logger.debug("embed_image: %d bytes -> shape (512,)", len(image_bytes))
    return JSONResponse({"embedding": embedding})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_loaded": _model is not None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        logger.warning(
            "SECURITY: Binding to %s with no authentication. "
            "Only do this inside a trusted network.",
            args.host,
        )
    uvicorn.run(app, host=args.host, port=args.port)
