from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DriverCreate(BaseModel):
    name: str
    profile_type: str = "standard"
    balance: float = 0.0


class DriverProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    profile_type: str
    balance: float


class TripCreate(BaseModel):
    road_name: str
    cost: float


class TripRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    driver_id: int
    road_name: str
    cost: float
    created_at: datetime


class TopUpCreate(BaseModel):
    amount: float


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    driver_id: int
    type: str
    amount: float
    created_at: datetime


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    driver_id: int
    title: str
    message: str
    deeplink: Optional[str] = None
    created_at: datetime


class BalanceRead(BaseModel):
    driver_id: int
    balance: float


class ForecastRead(BaseModel):
    driver_id: int
    average_trip_cost: float
    forecast_30_days: float
    trip_count: int
    horizon_days: int = 30


class StatsRead(BaseModel):
    driver_id: int
    period: str
    total_spent: float
    average_trip_cost: float
    trip_count: int
    top_road_name: Optional[str] = None
