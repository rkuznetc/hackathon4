from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import crud, models
from app.config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.database import get_db
from app.schemas import (
    BalanceRead,
    DriverProfile,
    ForecastRead,
    NotificationRead,
    PaginatedResponse,
    StatsRead,
    TopUpCreate,
    TransactionRead,
    TripRead,
)
from app.security import get_current_driver
from app.services.forecast_service import calculate_driver_forecast
from app.services.notification_service import get_driver_notifications
from app.services.stats_service import calculate_driver_stats

router = APIRouter(prefix="/me", tags=["me (mobile)"])


@router.get("/profile", response_model=DriverProfile)
def me_profile(driver: models.Driver = Depends(get_current_driver)):
    return driver


@router.get("/balance", response_model=BalanceRead)
def me_balance(driver: models.Driver = Depends(get_current_driver)):
    return BalanceRead(driver_id=driver.id, balance=driver.balance)


@router.get("/trips", response_model=PaginatedResponse[TripRead])
def me_trips(
    driver: models.Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    items, total = crud.get_trips_paginated(db, driver.id, limit, offset)
    return PaginatedResponse(
        items=[TripRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/transactions", response_model=PaginatedResponse[TransactionRead])
def me_transactions(
    driver: models.Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    items, total = crud.get_transactions_paginated(db, driver.id, limit, offset)
    return PaginatedResponse(
        items=[TransactionRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/forecast", response_model=ForecastRead)
def me_forecast(
    driver: models.Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    return calculate_driver_forecast(db, driver.id)


@router.get("/notifications", response_model=PaginatedResponse[NotificationRead])
def me_notifications(
    driver: models.Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
):
    return get_driver_notifications(db, driver.id, limit, offset)


@router.get("/stats", response_model=StatsRead)
def me_stats(
    driver: models.Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
    period: str = Query(default="month", pattern="^(week|month|all)$"),
):
    return calculate_driver_stats(db, driver.id, period)


@router.post("/top-up", response_model=BalanceRead)
def me_top_up(
    data: TopUpCreate,
    driver: models.Driver = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма пополнения должна быть > 0")
    updated = crud.top_up_balance(db, driver.id, data)
    return BalanceRead(driver_id=updated.id, balance=updated.balance)
