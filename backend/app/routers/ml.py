from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.schemas import (
    MlPredictionsResponse,
    MlRecommendationsResponse,
    MlRankedRecommendationItem,
    MlStatusResponse,
    MlModelMetadataBrief,
)
from app.security import get_current_vehicle
from app.services import ml_model_service

router = APIRouter(prefix="/me/ml", tags=["me (ml)"])


@router.get("/status", response_model=MlStatusResponse)
def me_ml_status(
    vehicle: models.Vehicle = Depends(get_current_vehicle),
):
    _ = vehicle
    data = ml_model_service.get_ml_status()
    return MlStatusResponse(**data)


@router.get("/predictions", response_model=MlPredictionsResponse)
def me_ml_predictions(
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
):
    raw = ml_model_service.predict_for_vehicle(db, vehicle.vehicle_id)
    if not raw.get("available"):
        return MlPredictionsResponse(
            available=False,
            reason=raw.get("reason", "models_not_found"),
        )
    meta = raw.get("model_metadata") or {}
    return MlPredictionsResponse(
        available=True,
        vehicle_id=raw["vehicle_id"],
        snapshot_at=raw.get("snapshot_at"),
        spend_forecast_7d=Decimal(str(round(raw["spend_forecast_7d"], 2))),
        spend_forecast_30d=Decimal(str(round(raw["spend_forecast_30d"], 2))),
        debt_risk_7d=raw["debt_risk_7d"],
        model_metadata=MlModelMetadataBrief(
            trained_at=meta.get("trained_at"),
            version=meta.get("version"),
        ),
    )


@router.get("/recommendations", response_model=MlRecommendationsResponse)
def me_ml_recommendations(
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
):
    raw = ml_model_service.rank_recommendations_for_vehicle(db, vehicle.vehicle_id)
    items = [
        MlRankedRecommendationItem(
            event_id=i["event_id"],
            recommendation_type=i["recommendation_type"],
            title=i["title"],
            deep_link=i.get("deep_link"),
            status=i["status"],
            acceptance_probability=i.get("acceptance_probability"),
            debt_risk_7d=i.get("debt_risk_7d"),
            estimated_value=(
                Decimal(i["estimated_value"]) if i.get("estimated_value") is not None else None
            ),
            business_priority=i["business_priority"],
            hybrid_score=i.get("hybrid_score"),
        )
        for i in raw.get("items", [])
    ]
    return MlRecommendationsResponse(
        available=raw.get("available", False),
        reason=raw.get("reason"),
        vehicle_id=raw.get("vehicle_id"),
        items=items,
    )
