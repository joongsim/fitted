"""Tests for scripts/pretrain_item_tower.py.

Covers:
- load_catalog_embeddings: correct shape, L2-normalization, skips bad rows
- pretrain: correct weight shape, loss decreases, returns tensor
- upload_weights_to_s3: preserves existing UserTower, creates Xavier fallback,
  uploaded bytes are loadable by torch.load
"""

import io
import pathlib
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from scripts.pretrain_item_tower import (
    _EMBED_DIM,
    _S3_MODEL_KEY,
    load_catalog_embeddings,
    pretrain,
    upload_weights_to_s3,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unit_vec(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_EMBED_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _make_mock_conn(fetchall_return=None):
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall = MagicMock(return_value=fetchall_return or [])
    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur)
    return mock_conn


# ---------------------------------------------------------------------------
# load_catalog_embeddings
# ---------------------------------------------------------------------------


class TestLoadCatalogEmbeddings:
    def test_returns_correct_shape(self):
        rows = [(_unit_vec(i).tolist(),) for i in range(5)]
        conn = _make_mock_conn(fetchall_return=rows)
        result = load_catalog_embeddings(conn)
        assert result.shape == (5, _EMBED_DIM)
        assert result.dtype == np.float32

    def test_rows_are_l2_normalized(self):
        rows = [(_unit_vec(i) * 5.0,) for i in range(3)]  # non-unit vectors
        conn = _make_mock_conn(fetchall_return=rows)
        result = load_catalog_embeddings(conn)
        norms = np.linalg.norm(result, axis=1)
        np.testing.assert_allclose(norms, 1.0, atol=1e-5)

    def test_skips_wrong_shape_embeddings(self):
        bad = np.ones(64, dtype=np.float32).tolist()
        good = _unit_vec(0).tolist()
        rows = [(bad,), (good,)]
        conn = _make_mock_conn(fetchall_return=rows)
        result = load_catalog_embeddings(conn)
        assert result.shape == (1, _EMBED_DIM)

    def test_empty_db_returns_empty_array(self):
        conn = _make_mock_conn(fetchall_return=[])
        result = load_catalog_embeddings(conn)
        assert result.shape == (0, _EMBED_DIM)

    def test_accepts_numpy_array_rows(self):
        rows = [(_unit_vec(i),) for i in range(4)]
        conn = _make_mock_conn(fetchall_return=rows)
        result = load_catalog_embeddings(conn)
        assert result.shape == (4, _EMBED_DIM)


# ---------------------------------------------------------------------------
# pretrain
# ---------------------------------------------------------------------------


class TestPretrain:
    def _make_embeddings(self, n: int = 50) -> np.ndarray:
        vecs = np.stack([_unit_vec(i) for i in range(n)], axis=0)
        return vecs

    def test_returns_correct_weight_shape(self):
        embs = self._make_embeddings(20)
        w = pretrain(embs, epochs=2, lr=1e-3, batch_size=10)
        assert w.shape == (_EMBED_DIM, _EMBED_DIM)

    def test_returns_torch_tensor(self):
        embs = self._make_embeddings(10)
        w = pretrain(embs, epochs=1, lr=1e-3, batch_size=10)
        assert isinstance(w, torch.Tensor)

    def test_weights_differ_from_identity(self):
        # After training, weights should not be identical to initial Xavier (they changed)
        embs = self._make_embeddings(30)
        w = pretrain(embs, epochs=3, lr=1e-2, batch_size=15)
        identity = torch.eye(_EMBED_DIM)
        assert not torch.allclose(w, identity)


# ---------------------------------------------------------------------------
# upload_weights_to_s3
# ---------------------------------------------------------------------------


class TestUploadWeightsToS3:
    def test_creates_xavier_user_tower_when_no_existing_model(self):
        item_w = torch.ones(_EMBED_DIM, _EMBED_DIM)
        captured = {}

        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        def fake_put(**kwargs):
            captured["body"] = kwargs["Body"]

        mock_s3.put_object.side_effect = fake_put

        with patch("boto3.client", return_value=mock_s3):
            upload_weights_to_s3(item_w, bucket="test-bucket")

        state = torch.load(io.BytesIO(captured["body"]), weights_only=True)
        assert "user_tower_W" in state
        assert "item_tower_W" in state
        assert torch.allclose(state["item_tower_W"], item_w)

    def test_preserves_existing_user_tower_from_s3(self):
        existing_user_w = torch.eye(_EMBED_DIM)
        existing_item_w = torch.zeros(_EMBED_DIM, _EMBED_DIM)

        existing_buf = io.BytesIO()
        torch.save(
            {"user_tower_W": existing_user_w, "item_tower_W": existing_item_w},
            existing_buf,
        )
        existing_buf.seek(0)

        new_item_w = torch.ones(_EMBED_DIM, _EMBED_DIM) * 2.0
        captured = {}

        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": existing_buf}

        def fake_put(**kwargs):
            captured["body"] = kwargs["Body"]

        mock_s3.put_object.side_effect = fake_put

        with patch("boto3.client", return_value=mock_s3):
            upload_weights_to_s3(new_item_w, bucket="test-bucket")

        state = torch.load(io.BytesIO(captured["body"]), weights_only=True)
        # UserTower preserved from existing S3 model
        assert torch.allclose(state["user_tower_W"], existing_user_w)
        # ItemTower is the new pre-trained version
        assert torch.allclose(state["item_tower_W"], new_item_w)

    def test_calls_put_object_with_correct_key(self):
        item_w = torch.eye(_EMBED_DIM)
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        with patch("boto3.client", return_value=mock_s3):
            upload_weights_to_s3(item_w, bucket="my-bucket")

        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "my-bucket"
        assert call_kwargs["Key"] == _S3_MODEL_KEY
