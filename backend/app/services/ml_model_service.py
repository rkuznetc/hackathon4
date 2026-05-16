"""Load offline-trained ML artifacts and run inference (no training at runtime)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app import models
from app.services.ml_features import build_runtime_features_for_vehicle

_REQUIRED_RUNTIME = (
    "spend_7d_model.joblib",
    "spend_30d_model.joblib",
    "debt_risk_model.joblib",
    "feature_columns.json",
    "recommendation_priors.json",
    "model_metadata.json",
)


@dataclass
class MLBundle:
    models_dir: Path
    spend_7d: Any
    spend_30d: Any
    debt_risk: Any
    feature_columns: dict[str, Any]
    recommendation_priors: dict[str, float]
    metadata: dict[str, Any]


_bundle_cache: MLBundle | None = None
_bundle_unavailable_reason: str | None = None
_bundle_loaded: bool = False


def _models_dir() -> Path:
    from app.config import ML_MODELS_DIR

    return Path(ML_MODELS_DIR).resolve()


def _missing_reason(models_dir: Path) -> str | None:
    if not models_dir.is_dir():
        return "models_dir_not_found"
    for name in _REQUIRED_RUNTIME:
        if not (models_dir / name).exists():
            return "models_not_found"
    return None


def _load_bundle() -> tuple[MLBundle | None, str | None]:
    global _bundle_cache, _bundle_unavailable_reason, _bundle_loaded
    if _bundle_loaded:
        if _bundle_cache is not None:
            return _bundle_cache, None
        return None, _bundle_unavailable_reason

    _bundle_loaded = True
    models_dir = _models_dir()
    reason = _missing_reason(models_dir)
    if reason:
        _bundle_unavailable_reason = reason
        _bundle_cache = None
        return None, reason

    try:
        bundle = MLBundle(
            models_dir=models_dir,
            spend_7d=joblib.load(models_dir / "spend_7d_model.joblib"),
            spend_30d=joblib.load(models_dir / "spend_30d_model.joblib"),
            debt_risk=joblib.load(models_dir / "debt_risk_model.joblib"),
            feature_columns=json.loads(
                (models_dir / "feature_columns.json").read_text(encoding="utf-8")
            ),
            recommendation_priors=json.loads(
                (models_dir / "recommendation_priors.json").read_text(encoding="utf-8")
            ),
            metadata=json.loads(
                (models_dir / "model_metadata.json").read_text(encoding="utf-8")
            ),
        )
        _bundle_cache = bundle
        _bundle_unavailable_reason = None
        return bundle, None
    except Exception:
        _bundle_unavailable_reason = "models_load_error"
        _bundle_cache = None
        return None, "models_load_error"


def reset_ml_cache() -> None:
    """For tests: clear in-memory model cache."""
    global _bundle_cache, _bundle_unavailable_reason, _bundle_loaded
    _bundle_cache = None
    _bundle_unavailable_reason = None
    _bundle_loaded = False


def get_ml_status() -> dict[str, Any]:
    models_dir = _models_dir()
    bundle, reason = _load_bundle()
    if bundle is None:
        return {
            "available": False,
            "models_dir": str(models_dir),
            "reason": reason or "models_not_found",
        }
    return {
        "available": True,
        "models_dir": str(models_dir),
        "trained_at": bundle.metadata.get("trained_at"),
        "models": bundle.metadata.get(
            "runtime_models",
            ["spend_7d_model", "spend_30d_model", "debt_risk_model"],
        ),
        "metadata": bundle.metadata,
    }


def _predict_row(bundle: MLBundle, row: pd.DataFrame) -> dict[str, float]:
    cols = bundle.feature_columns.get("all_model_features", [])
    missing = [c for c in cols if c not in row.columns]
    if missing:
        for c in missing:
            row[c] = 0
    X = row[cols]
    spend7 = float(np.clip(bundle.spend_7d.predict(X)[0], 0, None))
    spend30 = float(np.clip(bundle.spend_30d.predict(X)[0], 0, None))
    debt = float(bundle.debt_risk.predict_proba(X)[0, 1])
    return {
        "spend_forecast_7d": spend7,
        "spend_forecast_30d": spend30,
        "debt_risk_7d": debt,
    }


def predict_for_vehicle(db: Session, vehicle_id: int) -> dict[str, Any]:
    bundle, reason = _load_bundle()
    if bundle is None:
        return {"available": False, "reason": reason or "models_not_found"}

    vehicle = db.get(models.Vehicle, vehicle_id)
    if vehicle is None:
        return {"available": False, "reason": "vehicle_not_found"}

    row = build_runtime_features_for_vehicle(db, vehicle)
    preds = _predict_row(bundle, row)
    snap_at = row["snapshot_at"].iloc[0]
    return {
        "available": True,
        "vehicle_id": vehicle_id,
        "snapshot_at": pd.Timestamp(snap_at).isoformat(),
        "spend_forecast_7d": preds["spend_forecast_7d"],
        "spend_forecast_30d": preds["spend_forecast_30d"],
        "debt_risk_7d": preds["debt_risk_7d"],
        "model_metadata": {
            "trained_at": bundle.metadata.get("trained_at"),
            "version": bundle.metadata.get("python_version"),
        },
    }


def _parse_ruble_value(title: str) -> float:
    if not title:
        return 0.0
    nums = re.findall(r"(\d[\d\s]*)\s*₽", title)
    if not nums:
        return 0.0
    return float(nums[-1].replace(" ", ""))


def _business_priority(rec_type: str) -> int:
    return {
        "repay_debt": 10,
        "topup_balance": 8,
        "topup_forecast": 7,
        "buy_subscription": 7,
        "enable_autopay": 6,
        "pay_before_deadline": 5,
    }.get(rec_type, 4)


def _estimate_value(rec_type: str, title: str, preds: dict[str, float], balance: float) -> float:
    parsed = _parse_ruble_value(title)
    if parsed > 0:
        return parsed
    if rec_type == "repay_debt":
        return max(0.0, abs(min(0.0, balance)) + 300)
    if rec_type in {"topup_balance", "topup_forecast"}:
        return max(0.0, preds["spend_forecast_7d"] - balance)
    if rec_type == "enable_autopay":
        return 200.0
    if rec_type == "pay_before_deadline":
        return 150.0
    return 0.0


def rank_recommendations_for_vehicle(db: Session, vehicle_id: int) -> dict[str, Any]:
    bundle, reason = _load_bundle()
    events = (
        db.query(models.RecommendationEvent)
        .filter(
            models.RecommendationEvent.vehicle_id == vehicle_id,
            models.RecommendationEvent.status == "shown",
        )
        .order_by(models.RecommendationEvent.shown_at.desc())
        .all()
    )

    if bundle is None:
        return {
            "available": False,
            "vehicle_id": vehicle_id,
            "reason": reason or "models_not_found",
            "items": [
                {
                    "event_id": e.event_id,
                    "recommendation_type": e.recommendation_type,
                    "title": e.title,
                    "deep_link": e.deep_link,
                    "status": e.status,
                    "acceptance_probability": None,
                    "debt_risk_7d": None,
                    "estimated_value": None,
                    "business_priority": _business_priority(e.recommendation_type),
                    "hybrid_score": None,
                }
                for e in events
            ],
        }

    pred = predict_for_vehicle(db, vehicle_id)
    if not pred.get("available"):
        return {
            "available": False,
            "vehicle_id": vehicle_id,
            "reason": pred.get("reason", "models_not_found"),
            "items": [],
        }

    vehicle = db.get(models.Vehicle, vehicle_id)
    balance = float(vehicle.current_balance) if vehicle else 0.0
    priors = bundle.recommendation_priors
    global_mean = float(np.mean(list(priors.values()))) if priors else 0.4
    debt_risk = float(pred["debt_risk_7d"])

    items = []
    values = []
    for e in events:
        acc = float(priors.get(e.recommendation_type, global_mean))
        est = _estimate_value(
            e.recommendation_type,
            e.title,
            pred,
            balance,
        )
        bp = _business_priority(e.recommendation_type)
        values.append(est)
        items.append(
            {
                "event": e,
                "acceptance_probability": acc,
                "estimated_value": est,
                "business_priority": bp,
            }
        )

    max_val = max(values) if values else 1.0
    out = []
    for it in items:
        e = it["event"]
        val_norm = np.log1p(it["estimated_value"]) / np.log1p(max(1.0, max_val))
        pr_norm = it["business_priority"] / 10.0
        hybrid = (
            0.30 * it["acceptance_probability"]
            + 0.40 * val_norm
            + 0.20 * debt_risk
            + 0.10 * pr_norm
        )
        out.append(
            {
                "event_id": e.event_id,
                "recommendation_type": e.recommendation_type,
                "title": e.title,
                "deep_link": e.deep_link,
                "status": e.status,
                "acceptance_probability": round(it["acceptance_probability"], 4),
                "debt_risk_7d": round(debt_risk, 4),
                "estimated_value": str(Decimal(str(round(it["estimated_value"], 2)))),
                "business_priority": it["business_priority"],
                "hybrid_score": round(float(hybrid), 4),
            }
        )

    out.sort(key=lambda x: x["hybrid_score"], reverse=True)
    return {"available": True, "vehicle_id": vehicle_id, "items": out}


def summary_ml_block(db: Session, vehicle_id: int) -> dict[str, Any]:
    pred = predict_for_vehicle(db, vehicle_id)
    if not pred.get("available"):
        return {"available": False, "reason": pred.get("reason", "models_not_found")}

    ranked = rank_recommendations_for_vehicle(db, vehicle_id)
    top_id = None
    if ranked.get("available") and ranked.get("items"):
        top_id = ranked["items"][0]["event_id"]

    return {
        "available": True,
        "spend_forecast_7d": str(Decimal(str(round(pred["spend_forecast_7d"], 2)))),
        "spend_forecast_30d": str(Decimal(str(round(pred["spend_forecast_30d"], 2)))),
        "debt_risk_7d": round(float(pred["debt_risk_7d"]), 4),
        "top_recommendation_event_id": top_id,
    }
