"""ML inference endpoints and graceful fallback without artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import subprocess
import sys

import joblib
import pytest
from sklearn.dummy import DummyClassifier, DummyRegressor

from app import models
from app.services import ml_model_service


@pytest.fixture(autouse=True)
def _reset_ml_cache():
    ml_model_service.reset_ml_cache()
    yield
    ml_model_service.reset_ml_cache()


@pytest.fixture
def empty_models_dir(tmp_path, monkeypatch):
    d = tmp_path / "empty_models"
    d.mkdir()
    monkeypatch.setattr("app.config.ML_MODELS_DIR", str(d))
    ml_model_service.reset_ml_cache()
    return d


def test_ml_status_without_models_returns_available_false(
    client, auth_headers, empty_models_dir
):
    r = client.get("/me/ml/status", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert data["reason"] in ("models_not_found", "models_dir_not_found")


def test_ml_predictions_without_models_returns_available_false(client, auth_headers, empty_models_dir):
    r = client.get("/me/ml/predictions", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_ml_endpoints_without_token_return_401(client, empty_models_dir):
    for path in ("/me/ml/status", "/me/ml/predictions", "/me/ml/recommendations"):
        assert client.get(path).status_code == 401


def test_me_summary_contains_ml_block(client, auth_headers, empty_models_dir):
    r = client.get("/me/summary", headers=auth_headers)
    assert r.status_code == 200
    assert "ml" in r.json()
    assert r.json()["ml"]["available"] is False


def test_ml_model_loader_with_dummy_artifacts(client, auth_headers, tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    # Minimal pipelines (status check only; real predict needs ColumnTransformer from training)
    reg = DummyRegressor(strategy="constant", constant=100.0)
    clf = DummyClassifier(strategy="constant", constant=0)
    joblib.dump(reg, models_dir / "spend_7d_model.joblib")
    joblib.dump(reg, models_dir / "spend_30d_model.joblib")
    joblib.dump(clf, models_dir / "debt_risk_model.joblib")

    manifest = {
        "numeric_features": ["w7_spend"],
        "categorical_features": [],
        "all_model_features": ["w7_spend"],
        "target_columns": ["spend_next_7d"],
        "excluded_from_inference": ["spend_next_7d"],
    }
    (models_dir / "feature_columns.json").write_text(json.dumps(manifest), encoding="utf-8")
    (models_dir / "recommendation_priors.json").write_text(
        json.dumps({"topup_balance": 0.5}), encoding="utf-8"
    )
    (models_dir / "model_metadata.json").write_text(
        json.dumps({"trained_at": "2026-01-01T00:00:00Z", "runtime_models": ["spend_7d_model"]}),
        encoding="utf-8",
    )

    monkeypatch.setattr("app.config.ML_MODELS_DIR", str(models_dir))
    ml_model_service.reset_ml_cache()

    status = client.get("/me/ml/status", headers=auth_headers)
    assert status.status_code == 200
    assert status.json()["available"] is True


def test_ml_recommendations_ranking_no_side_effects(
    client, auth_headers, db_session, empty_models_dir
):
    from datetime import datetime

    profile = client.get("/me/profile", headers=auth_headers).json()
    vid = profile["vehicle_id"]
    ev = models.RecommendationEvent(
        vehicle_id=vid,
        shown_at=datetime(2026, 2, 1, 12, 0, 0),
        recommendation_type="topup_balance",
        title="Пополните 500 ₽",
        status="shown",
    )
    db_session.add(ev)
    db_session.commit()
    event_id = ev.event_id

    r = client.get("/me/ml/recommendations", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert len(body["items"]) >= 1
    assert body["items"][0]["status"] == "shown"

    db_session.refresh(ev)
    assert ev.status == "shown"
    assert ev.event_id == event_id


def test_ml_pipeline_help():
    root = Path(__file__).resolve().parents[2]
    script = root / "ml" / "ml_pipeline_final.py"
    if not script.exists():
        pytest.skip("ml/ml_pipeline_final.py not in workspace")
    r = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    assert r.returncode == 0
    assert "--save-models" in r.stdout
