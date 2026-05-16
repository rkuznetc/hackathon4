from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import crud, models
from app.schemas import PaginatedResponse, RecommendationEventRead
from app.services.forecast_service import calculate_vehicle_forecast


def build_dynamic_topup_forecast_if_needed(
    db: Session, vehicle_id: int
) -> RecommendationEventRead | None:
    """
    Динамическая рекомендация (не хранится в recommendation_events).
    Отличается по event_id=0 и is_dynamic=true.
    """
    vehicle = crud.get_vehicle(db, vehicle_id)
    if vehicle is None:
        return None

    forecast = calculate_vehicle_forecast(db, vehicle_id)
    bal = vehicle.current_balance
    if forecast.forecast_amount > 0 and bal < forecast.forecast_amount:
        return RecommendationEventRead(
            event_id=0,
            vehicle_id=vehicle_id,
            shown_at=datetime.now(timezone.utc).replace(tzinfo=None),
            recommendation_type="topup_forecast",
            title=(
                f"Баланс {bal} ₽ ниже прогноза на "
                f"{forecast.horizon_days} дн. ({forecast.forecast_amount} ₽)"
            ),
            status="shown",
            responded_at=None,
            deep_link="/me/top-up",
            related_transaction_id=None,
            is_dynamic=True,
        )
    return None


def get_vehicle_recommendations(
    db: Session, vehicle_id: int, limit: int, offset: int
) -> PaginatedResponse[RecommendationEventRead]:
    dynamic = build_dynamic_topup_forecast_if_needed(db, vehicle_id)

    query = (
        db.query(models.RecommendationEvent)
        .filter(models.RecommendationEvent.vehicle_id == vehicle_id)
        .order_by(models.RecommendationEvent.shown_at.desc())
    )
    stored_total = query.count()
    total = stored_total + (1 if dynamic else 0)

    if offset == 0 and dynamic is not None:
        stored_limit = max(limit - 1, 0)
        stored_items = query.offset(0).limit(stored_limit).all()
        items = [dynamic] + [
            RecommendationEventRead.model_validate(e) for e in stored_items
        ]
    else:
        stored_offset = offset - 1 if dynamic and offset > 0 else offset
        stored_items = query.offset(stored_offset).limit(limit).all()
        items = [RecommendationEventRead.model_validate(e) for e in stored_items]

    return PaginatedResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


def count_stored_shown_recommendations(db: Session, vehicle_id: int) -> int:
    """Только строки в БД со status='shown' (без динамической topup_forecast)."""
    return (
        db.query(models.RecommendationEvent)
        .filter(
            models.RecommendationEvent.vehicle_id == vehicle_id,
            models.RecommendationEvent.status == "shown",
        )
        .count()
    )


def get_latest_stored_shown_recommendations(
    db: Session, vehicle_id: int, limit: int = 3
) -> list[RecommendationEventRead]:
    rows = (
        db.query(models.RecommendationEvent)
        .filter(
            models.RecommendationEvent.vehicle_id == vehicle_id,
            models.RecommendationEvent.status == "shown",
        )
        .order_by(models.RecommendationEvent.shown_at.desc())
        .limit(limit)
        .all()
    )
    return [RecommendationEventRead.model_validate(r) for r in rows]
