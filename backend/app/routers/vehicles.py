from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import crud
from app.config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.database import get_db
from app.schemas import (
    AccountTransactionRead,
    ForecastRead,
    PaginatedResponse,
    RecommendationEventRead,
    StatsRead,
    TopUpRequest,
    TopUpResponse,
    TripCreate,
    TripRead,
    VehicleBalance,
    VehicleBehaviorFeaturesRead,
    VehicleCreate,
    VehicleProfile,
)
from app.security import get_current_admin_user
from app.services.forecast_service import calculate_vehicle_forecast
from app.services.recommendation_service import get_vehicle_recommendations
from app.services.stats_service import calculate_vehicle_stats

_REQUIRE_ADMIN = [Depends(get_current_admin_user)]

router = APIRouter(prefix="/vehicles", tags=["vehicles (admin)"])


def _ensure_vehicle(db: Session, vehicle_id: int) -> None:
    if crud.get_vehicle(db, vehicle_id) is None:
        raise HTTPException(status_code=404, detail="Автомобиль не найден")


@router.post(
    "",
    response_model=VehicleProfile,
    status_code=201,
    dependencies=_REQUIRE_ADMIN,
)
def create_vehicle(data: VehicleCreate, db: Session = Depends(get_db)):
    if crud.get_vehicle_by_phone(db, data.phone.strip()):
        raise HTTPException(
            status_code=400, detail="Телефон уже зарегистрирован"
        )
    if crud.get_vehicle_by_license_plate(db, data.license_plate.strip()):
        raise HTTPException(status_code=400, detail="Госномер уже занят")
    return crud.create_vehicle(db, data)


@router.delete("/{vehicle_id}", status_code=204, dependencies=_REQUIRE_ADMIN)
def delete_vehicle(vehicle_id: int, db: Session = Depends(get_db)):
    if not crud.delete_vehicle(db, vehicle_id):
        raise HTTPException(status_code=404, detail="Автомобиль не найден")


@router.get(
    "/{vehicle_id}/profile",
    response_model=VehicleProfile,
    dependencies=_REQUIRE_ADMIN,
)
def get_profile(vehicle_id: int, db: Session = Depends(get_db)):
    vehicle = crud.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Автомобиль не найден")
    return vehicle


@router.get(
    "/{vehicle_id}/balance",
    response_model=VehicleBalance,
    dependencies=_REQUIRE_ADMIN,
)
def get_balance(vehicle_id: int, db: Session = Depends(get_db)):
    vehicle = crud.get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Автомобиль не найден")
    return VehicleBalance(
        vehicle_id=vehicle.vehicle_id,
        current_balance=vehicle.current_balance,
        account_status=vehicle.account_status,
        autopay_enabled=vehicle.autopay_enabled,
    )


@router.get(
    "/{vehicle_id}/trips",
    response_model=PaginatedResponse[TripRead],
    dependencies=_REQUIRE_ADMIN,
)
def list_trips(
    vehicle_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    _ensure_vehicle(db, vehicle_id)
    items, total = crud.get_trips_paginated(db, vehicle_id, limit, offset)
    return PaginatedResponse(
        items=[TripRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{vehicle_id}/trips",
    response_model=TripRead,
    status_code=201,
    dependencies=_REQUIRE_ADMIN,
)
def add_trip(
    vehicle_id: int, data: TripCreate, db: Session = Depends(get_db)
):
    if crud.get_vehicle(db, vehicle_id) is None:
        raise HTTPException(status_code=404, detail="Автомобиль не найден")
    trip, _tx = crud.create_trip_for_vehicle(db, vehicle_id, data)
    return trip


@router.post(
    "/{vehicle_id}/top-up",
    response_model=TopUpResponse,
    dependencies=_REQUIRE_ADMIN,
)
def top_up(
    vehicle_id: int, data: TopUpRequest, db: Session = Depends(get_db)
):
    if crud.get_vehicle(db, vehicle_id) is None:
        raise HTTPException(status_code=404, detail="Автомобиль не найден")
    updated, tx = crud.top_up_balance(db, vehicle_id, data)
    return TopUpResponse(
        vehicle_id=updated.vehicle_id,
        current_balance=updated.current_balance,
        account_status=updated.account_status,
        transaction=AccountTransactionRead.model_validate(tx),
    )


@router.get(
    "/{vehicle_id}/transactions",
    response_model=PaginatedResponse[AccountTransactionRead],
    dependencies=_REQUIRE_ADMIN,
)
def list_transactions(
    vehicle_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    _ensure_vehicle(db, vehicle_id)
    items, total = crud.get_transactions_paginated(
        db, vehicle_id, limit, offset
    )
    return PaginatedResponse(
        items=[AccountTransactionRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{vehicle_id}/recommendations",
    response_model=PaginatedResponse[RecommendationEventRead],
    dependencies=_REQUIRE_ADMIN,
)
def list_recommendations(
    vehicle_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    _ensure_vehicle(db, vehicle_id)
    return get_vehicle_recommendations(db, vehicle_id, limit, offset)


@router.get(
    "/{vehicle_id}/behavior",
    response_model=VehicleBehaviorFeaturesRead,
    dependencies=_REQUIRE_ADMIN,
)
def get_behavior(vehicle_id: int, db: Session = Depends(get_db)):
    _ensure_vehicle(db, vehicle_id)
    row = crud.get_behavior_features(db, vehicle_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Поведенческие признаки для автомобиля ещё не рассчитаны",
        )
    return row


@router.get(
    "/{vehicle_id}/forecast",
    response_model=ForecastRead,
    dependencies=_REQUIRE_ADMIN,
)
def get_forecast(vehicle_id: int, db: Session = Depends(get_db)):
    _ensure_vehicle(db, vehicle_id)
    return calculate_vehicle_forecast(db, vehicle_id)


@router.get(
    "/{vehicle_id}/stats",
    response_model=StatsRead,
    dependencies=_REQUIRE_ADMIN,
)
def get_stats(
    vehicle_id: int,
    db: Session = Depends(get_db),
    period: str = Query(default="month", pattern="^(week|month|all)$"),
):
    _ensure_vehicle(db, vehicle_id)
    return calculate_vehicle_stats(db, vehicle_id, period)
