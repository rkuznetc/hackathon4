from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import crud, models
from app.config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.database import get_db
from app.schemas import (
    AccountTransactionRead,
    AutopayUpdateRequest,
    AutopayUpdateResponse,
    ForecastRead,
    MeSummaryResponse,
    PaginatedResponse,
    RecommendationEventRead,
    RecommendationRespondRequest,
    StatsRead,
    TopUpRequest,
    TopUpResponse,
    TripRead,
    VehicleBalance,
    VehicleBehaviorFeaturesRead,
    VehicleProfile,
)
from app.security import get_current_vehicle
from app.services.forecast_service import calculate_vehicle_forecast
from app.services.recommendation_service import get_vehicle_recommendations
from app.services.stats_service import calculate_vehicle_stats
from app.services.summary_service import build_me_summary

router = APIRouter(
    prefix="/me", tags=["me (mobile)"]
)


@router.get("/profile", response_model=VehicleProfile)
def me_profile(vehicle: models.Vehicle = Depends(get_current_vehicle)):
    return vehicle


@router.get("/summary", response_model=MeSummaryResponse)
def me_summary(
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
):
    return build_me_summary(db, vehicle)


@router.patch("/autopay", response_model=AutopayUpdateResponse)
def me_autopay(
    data: AutopayUpdateRequest,
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
):
    vehicle.autopay_enabled = data.autopay_enabled
    db.commit()
    db.refresh(vehicle)
    return AutopayUpdateResponse(
        vehicle_id=vehicle.vehicle_id,
        autopay_enabled=vehicle.autopay_enabled,
    )


@router.get("/balance", response_model=VehicleBalance)
def me_balance(vehicle: models.Vehicle = Depends(get_current_vehicle)):
    return VehicleBalance(
        vehicle_id=vehicle.vehicle_id,
        current_balance=vehicle.current_balance,
        account_status=vehicle.account_status,
        autopay_enabled=vehicle.autopay_enabled,
    )


@router.get("/trips", response_model=PaginatedResponse[TripRead])
def me_trips(
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    items, total = crud.get_trips_paginated(
        db, vehicle.vehicle_id, limit, offset
    )
    return PaginatedResponse(
        items=[TripRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/transactions", response_model=PaginatedResponse[AccountTransactionRead])
def me_transactions(
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    items, total = crud.get_transactions_paginated(
        db, vehicle.vehicle_id, limit, offset
    )
    return PaginatedResponse(
        items=[AccountTransactionRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/recommendations", response_model=PaginatedResponse[RecommendationEventRead])
def me_recommendations(
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    return get_vehicle_recommendations(db, vehicle.vehicle_id, limit, offset)


@router.post(
    "/recommendations/{event_id}/respond",
    response_model=RecommendationEventRead,
)
def me_recommendation_respond(
    event_id: int,
    body: RecommendationRespondRequest,
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
):
    ev = (
        db.query(models.RecommendationEvent)
        .filter(models.RecommendationEvent.event_id == event_id)
        .first()
    )
    if ev is None or ev.vehicle_id != vehicle.vehicle_id:
        raise HTTPException(
            status_code=404,
            detail="Рекомендация не найдена",
        )
    if ev.status != "shown":
        raise HTTPException(
            status_code=400,
            detail="Рекомендация уже обработана",
        )
    ev.status = body.status
    ev.responded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(ev)
    return RecommendationEventRead.model_validate(ev)


@router.get("/behavior", response_model=VehicleBehaviorFeaturesRead)
def me_behavior(
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
):
    row = crud.get_behavior_features(db, vehicle.vehicle_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Поведенческие признаки для автомобиля ещё не рассчитаны",
        )
    return row


@router.get("/forecast", response_model=ForecastRead)
def me_forecast(
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
):
    return calculate_vehicle_forecast(db, vehicle.vehicle_id)


@router.get("/stats", response_model=StatsRead)
def me_stats(
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
    period: str = Query(default="month", pattern="^(week|month|all)$"),
):
    return calculate_vehicle_stats(db, vehicle.vehicle_id, period)


@router.post("/top-up", response_model=TopUpResponse)
def me_top_up(
    data: TopUpRequest,
    vehicle: models.Vehicle = Depends(get_current_vehicle),
    db: Session = Depends(get_db),
):
    updated, tx = crud.top_up_balance(db, vehicle.vehicle_id, data)
    return TopUpResponse(
        vehicle_id=updated.vehicle_id,
        current_balance=updated.current_balance,
        account_status=updated.account_status,
        transaction=AccountTransactionRead.model_validate(tx),
    )
