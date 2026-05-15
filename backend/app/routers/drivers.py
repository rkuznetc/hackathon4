from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import crud
from app.config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.database import get_db
from app.schemas import (
    BalanceRead,
    DriverCreate,
    DriverProfile,
    ForecastRead,
    NotificationRead,
    PaginatedResponse,
    StatsRead,
    TopUpCreate,
    TransactionRead,
    TripCreate,
    TripRead,
)
from app.services.forecast_service import calculate_driver_forecast
from app.services.notification_service import get_driver_notifications
from app.services.stats_service import calculate_driver_stats

router = APIRouter(prefix="/drivers", tags=["drivers (dev/admin)"])


def _ensure_driver(db: Session, driver_id: int) -> None:
    if not crud.get_driver(db, driver_id):
        raise HTTPException(status_code=404, detail="Водитель не найден")


@router.post("", response_model=DriverProfile, status_code=201)
def create_driver(data: DriverCreate, db: Session = Depends(get_db)):
    return crud.create_driver(db, data)


@router.delete("/{driver_id}", status_code=204)
def delete_driver(driver_id: int, db: Session = Depends(get_db)):
    if not crud.delete_driver(db, driver_id):
        raise HTTPException(status_code=404, detail="Водитель не найден")


@router.get("/{driver_id}/profile", response_model=DriverProfile)
def get_profile(driver_id: int, db: Session = Depends(get_db)):
    driver = crud.get_driver(db, driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Водитель не найден")
    return driver


@router.get("/{driver_id}/balance", response_model=BalanceRead)
def get_balance(driver_id: int, db: Session = Depends(get_db)):
    driver = crud.get_driver(db, driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Водитель не найден")
    return BalanceRead(driver_id=driver.id, balance=driver.balance)


@router.get("/{driver_id}/trips", response_model=PaginatedResponse[TripRead])
def list_trips(
    driver_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    _ensure_driver(db, driver_id)
    items, total = crud.get_trips_paginated(db, driver_id, limit, offset)
    return PaginatedResponse(
        items=[TripRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/{driver_id}/trips", response_model=TripRead, status_code=201)
def add_trip(
    driver_id: int, data: TripCreate, db: Session = Depends(get_db)
):
    driver = crud.get_driver(db, driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Водитель не найден")
    if driver.balance < data.cost:
        raise HTTPException(status_code=400, detail="Недостаточно средств на балансе")
    return crud.create_trip(db, driver_id, data)


@router.post("/{driver_id}/top-up", response_model=BalanceRead)
def top_up(
    driver_id: int, data: TopUpCreate, db: Session = Depends(get_db)
):
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма пополнения должна быть > 0")
    driver = crud.get_driver(db, driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Водитель не найден")
    updated = crud.top_up_balance(db, driver_id, data)
    return BalanceRead(driver_id=updated.id, balance=updated.balance)


@router.get("/{driver_id}/transactions", response_model=PaginatedResponse[TransactionRead])
def list_transactions(
    driver_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    _ensure_driver(db, driver_id)
    items, total = crud.get_transactions_paginated(db, driver_id, limit, offset)
    return PaginatedResponse(
        items=[TransactionRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{driver_id}/forecast", response_model=ForecastRead)
def get_forecast(driver_id: int, db: Session = Depends(get_db)):
    _ensure_driver(db, driver_id)
    return calculate_driver_forecast(db, driver_id)


@router.get(
    "/{driver_id}/notifications",
    response_model=PaginatedResponse[NotificationRead],
)
def list_notifications(
    driver_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    _ensure_driver(db, driver_id)
    return get_driver_notifications(db, driver_id, limit, offset)


@router.get("/{driver_id}/stats", response_model=StatsRead)
def get_stats(
    driver_id: int,
    period: str = Query(default="month", pattern="^(week|month|all)$"),
    db: Session = Depends(get_db),
):
    _ensure_driver(db, driver_id)
    return calculate_driver_stats(db, driver_id, period)
