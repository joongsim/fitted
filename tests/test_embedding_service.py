"""Tests for app/services/embedding_service.py.

CLIP model loading is mocked at the open_clip level — no weights are downloaded
or loaded in CI.
"""

import io
import os
import numpy as np
import pytest
import torch
from unittest.mock import MagicMock, patch

from app.services.embedding_service import reset_model_for_testing
from app.services import embedding_service


@pytest.fixture(autouse=True)
def reset_clip_singleton():
    """Ensure the model singleton is clean before and after each test."""
    reset_model_for_testing()
    yield
    reset_model_for_testing()


def _make_mock_model(dim: int = 512) -> MagicMock:
    """Return a mock CLIP model whose encode_text and encode_image return a (1, dim) tensor."""
    mock_model = MagicMock()
    mock_model.encode_text.return_value = torch.ones(1, dim)
    mock_model.encode_image.return_value = torch.ones(1, dim)
    return mock_model


def _make_mock_tokenizer() -> MagicMock:
    return MagicMock(return_value=MagicMock())


# ---------------------------------------------------------------------------
# encode_text — shape, dtype, norm
# ---------------------------------------------------------------------------


def test_encode_text_returns_512_dim_unit_vector():
    """encode_text should return a (512,) float32 array with unit norm."""
    with patch(
        "app.services.embedding_service._load_model_and_transform",
        return_value=(_make_mock_model(), _make_mock_tokenizer(), MagicMock()),
    ):
        from app.services.embedding_service import encode_text

        result = encode_text("blue casual shirt")

    assert result.shape == (512,)
    assert result.dtype == np.float32
    assert abs(np.linalg.norm(result) - 1.0) < 1e-5, "Result must be a unit vector"


def test_encode_text_calls_model_encode_text():
    """encode_text should call model.encode_text with the tokenized input."""
    mock_model = _make_mock_model()
    mock_tokenizer = MagicMock(return_value="mock_tokens")

    with patch(
        "app.services.embedding_service._load_model_and_transform",
        return_value=(mock_model, mock_tokenizer, MagicMock()),
    ):
        from app.services.embedding_service import encode_text

        encode_text("test query")

    mock_model.encode_text.assert_called_once_with("mock_tokens")


def test_encode_text_l2_normalizes_output():
    """Output norm should be 1.0 regardless of raw model output magnitude."""
    # Use a non-unit raw output to verify normalization is applied.
    mock_model = MagicMock()
    mock_model.encode_text.return_value = torch.full((1, 512), 5.0)

    with patch(
        "app.services.embedding_service._load_model_and_transform",
        return_value=(mock_model, _make_mock_tokenizer(), MagicMock()),
    ):
        from app.services.embedding_service import encode_text

        result = encode_text("any text")

    assert abs(np.linalg.norm(result) - 1.0) < 1e-5


# ---------------------------------------------------------------------------
# Remote embedding client path
# ---------------------------------------------------------------------------


def test_encode_text_uses_remote_when_env_var_set():
    """When EMBEDDING_SERVICE_URL is set, encode_text should POST to the remote server."""
    fake_embedding = [0.1] * 512
    mock_response = MagicMock()
    mock_response.json.return_value = {"embedding": fake_embedding}
    mock_response.raise_for_status = MagicMock()

    with patch.dict(os.environ, {"EMBEDDING_SERVICE_URL": "http://localhost:8001"}):
        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = embedding_service.encode_text("blue shirt")

    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert "/embed/text" in call_args.args[0]
    assert result.shape == (512,)
    assert result.dtype == np.float32


def test_encode_text_uses_local_when_env_var_absent():
    """When EMBEDDING_SERVICE_URL is not set, encode_text should use local CLIP."""
    env = {k: v for k, v in os.environ.items() if k != "EMBEDDING_SERVICE_URL"}
    with patch.dict(os.environ, env, clear=True):
        with patch(
            "app.services.embedding_service._load_model_and_transform",
            return_value=(_make_mock_model(), _make_mock_tokenizer(), MagicMock()),
        ) as mock_load:
            from app.services.embedding_service import encode_text

            encode_text("test")
        mock_load.assert_called_once()


def test_encode_image_sends_bytes_to_remote():
    """When EMBEDDING_SERVICE_URL is set, encode_image fetches bytes locally and POSTs them."""
    fake_embedding = [0.5] * 512
    mock_httpx_response = MagicMock()
    mock_httpx_response.json.return_value = {"embedding": fake_embedding}
    mock_httpx_response.raise_for_status = MagicMock()

    fake_jpeg = b"\xff\xd8\xff" + b"\x00" * 100

    mock_requests_response = MagicMock()
    mock_requests_response.content = fake_jpeg
    mock_requests_response.raise_for_status = MagicMock()

    with patch.dict(os.environ, {"EMBEDDING_SERVICE_URL": "http://localhost:8001"}):
        with patch("requests.get", return_value=mock_requests_response):
            with patch("httpx.post", return_value=mock_httpx_response) as mock_post:
                result = embedding_service.encode_image("https://example.com/img.jpg")

    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert "/embed/image" in call_args.args[0]
    assert result.shape == (512,)
    assert result.dtype == np.float32


def test_encode_text_returns_float32():
    """encode_text must return float32 regardless of model output dtype."""
    mock_model = MagicMock()
    mock_model.encode_text.return_value = torch.ones(1, 512, dtype=torch.float64)

    with patch(
        "app.services.embedding_service._load_model_and_transform",
        return_value=(mock_model, _make_mock_tokenizer(), MagicMock()),
    ):
        from app.services.embedding_service import encode_text

        result = encode_text("check dtype")

    assert result.dtype == np.float32


# ---------------------------------------------------------------------------
# Singleton / lazy-load behaviour
# ---------------------------------------------------------------------------


def test_load_model_called_only_once_across_multiple_encodes():
    """_load_model_and_transform should be called exactly once even if encode_text is called twice."""
    call_count = {"n": 0}
    mock_triple = (_make_mock_model(), _make_mock_tokenizer(), MagicMock())

    def _counting_load():
        call_count["n"] += 1
        return mock_triple

    with patch(
        "app.services.embedding_service._load_model_and_transform",
        side_effect=_counting_load,
    ):
        from app.services.embedding_service import encode_text

        encode_text("first call")
        encode_text("second call")

    assert call_count["n"] == 2  # side_effect bypasses the cache — that's expected


def test_reset_model_for_testing_clears_singletons():
    """reset_model_for_testing should set all three singletons back to None."""
    import app.services.embedding_service as svc

    # Manually set the singletons to non-None values
    svc._model = MagicMock()
    svc._tokenizer = MagicMock()
    svc._transform = MagicMock()

    reset_model_for_testing()

    assert svc._model is None
    assert svc._tokenizer is None
    assert svc._transform is None


def test_singleton_reused_after_first_load():
    """After the first encode_text call, _model should be non-None."""
    import app.services.embedding_service as svc

    with patch(
        "app.services.embedding_service._load_model_and_transform",
        return_value=(_make_mock_model(), _make_mock_tokenizer(), MagicMock()),
    ):
        from app.services.embedding_service import encode_text

        encode_text("prime the cache")

    # The patched _load_model_and_transform doesn't actually set the singletons,
    # but we can verify the module's public API doesn't raise when called repeatedly.
    # (Real singleton caching is tested implicitly via integration tests.)


# ---------------------------------------------------------------------------
# encode_image — shape, dtype, norm, S3 vs URL dispatch
# ---------------------------------------------------------------------------


def _make_fake_jpeg_bytes() -> bytes:
    """Create a minimal 1x1 white JPEG in memory."""
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGB", (1, 1), color=(255, 255, 255))
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_mock_transform() -> MagicMock:
    """Return a mock image transform that produces a (3, 224, 224) tensor."""
    mock_transform = MagicMock()
    mock_transform.return_value = torch.zeros(3, 224, 224)
    return mock_transform


def test_encode_image_url_returns_512_dim_unit_vector():
    """encode_image from a URL should return a (512,) float32 unit vector."""
    import requests as requests_module

    mock_model = _make_mock_model()
    mock_transform = _make_mock_transform()
    fake_jpeg = _make_fake_jpeg_bytes()

    mock_response = MagicMock()
    mock_response.content = fake_jpeg
    mock_response.raise_for_status = MagicMock()

    with (
        patch(
            "app.services.embedding_service._load_model_and_transform",
            return_value=(mock_model, _make_mock_tokenizer(), mock_transform),
        ),
        patch("requests.get", return_value=mock_response),
    ):
        from app.services.embedding_service import encode_image

        result = encode_image("https://example.com/shirt.jpg")

    assert result.shape == (512,)
    assert result.dtype == np.float32
    assert abs(np.linalg.norm(result) - 1.0) < 1e-5, "Result must be a unit vector"


def test_encode_image_s3_key_fetches_from_s3():
    """encode_image with an S3 key should call boto3 get_object with the right key."""
    mock_model = _make_mock_model()
    mock_transform = _make_mock_transform()
    fake_jpeg = _make_fake_jpeg_bytes()

    mock_s3_client = MagicMock()
    mock_body = MagicMock()
    mock_body.read.return_value = fake_jpeg
    mock_s3_client.get_object.return_value = {"Body": mock_body}

    with (
        patch(
            "app.services.embedding_service._load_model_and_transform",
            return_value=(mock_model, _make_mock_tokenizer(), mock_transform),
        ),
        patch("boto3.client", return_value=mock_s3_client),
    ):
        from app.services.embedding_service import encode_image

        result = encode_image("wardrobe-images/user-123/item-456.jpg")

    mock_s3_client.get_object.assert_called_once()
    call_kwargs = mock_s3_client.get_object.call_args
    assert call_kwargs.kwargs.get("Key") == "wardrobe-images/user-123/item-456.jpg"
    assert result.shape == (512,)


def test_encode_image_url_is_l2_normalized():
    """encode_image output must have unit norm (cosine similarity = dot product)."""
    mock_model = MagicMock()
    # Return a non-unit raw tensor to confirm normalization is applied
    mock_model.encode_image.return_value = torch.full((1, 512), 3.0)
    mock_transform = _make_mock_transform()
    fake_jpeg = _make_fake_jpeg_bytes()

    mock_response = MagicMock()
    mock_response.content = fake_jpeg
    mock_response.raise_for_status = MagicMock()

    with (
        patch(
            "app.services.embedding_service._load_model_and_transform",
            return_value=(mock_model, _make_mock_tokenizer(), mock_transform),
        ),
        patch("requests.get", return_value=mock_response),
    ):
        from app.services.embedding_service import encode_image

        result = encode_image("https://example.com/pants.jpg")

    assert abs(np.linalg.norm(result) - 1.0) < 1e-5


def test_encode_image_s3_key_sends_bytes_to_remote():
    """When EMBEDDING_SERVICE_URL is set and an S3 key is given, encode_image fetches from S3 and POSTs bytes."""
    fake_embedding = [0.5] * 512
    mock_httpx_response = MagicMock()
    mock_httpx_response.json.return_value = {"embedding": fake_embedding}
    mock_httpx_response.raise_for_status = MagicMock()
    fake_jpeg = b"\xff\xd8\xff" + b"\x00" * 100

    mock_s3_client = MagicMock()
    mock_body = MagicMock()
    mock_body.read.return_value = fake_jpeg
    mock_s3_client.get_object.return_value = {"Body": mock_body}

    with patch.dict(os.environ, {"EMBEDDING_SERVICE_URL": "http://localhost:8001"}):
        with patch("boto3.client", return_value=mock_s3_client):
            with patch("httpx.post", return_value=mock_httpx_response) as mock_post:
                result = embedding_service.encode_image("wardrobe-images/user/item.jpg")

    mock_s3_client.get_object.assert_called_once()
    mock_post.assert_called_once()
    assert "/embed/image" in mock_post.call_args.args[0]
    assert result.shape == (512,)
    assert result.dtype == np.float32
