from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- Enum-like (строковая валидация) ---
AccountStatus = Literal["active", "debt", "blocked"]
SubscriptionType = Literal[
    "daily_unlimited",
    "monthly_unlimited",
    "weekend_pack",
    "trip_pack_10",
    "trip_pack_30",
]
OperationType = Literal[
    "trip_charge",
    "topup_manual",
    "topup_autopay",
    "fine_assessed",
    "fine_paid",
    "subscription_purchase",
]
Direction = Literal["credit", "debit"]
RecommendationType = Literal[
    "enable_autopay",
    "buy_subscription",
    "repay_debt",
    "topup_balance",
    "topup_forecast",
    "pay_before_deadline",
]
RecommendationStatus = Literal["shown", "accepted", "dismissed", "expired"]
SegmentCode = Literal[
    "commuter",
    "weekend_guest",
    "taxi_driver",
    "tourist",
    "new_user",
]


class VehicleCreate(BaseModel):
    license_plate: str = Field(min_length=1, max_length=16)
    owner_name: str = Field(min_length=1, max_length=100)
    registered_at: date
    phone: str = Field(min_length=5, max_length=20)
    current_balance: Decimal = Field(default=Decimal("0.00"), decimal_places=2)
    autopay_enabled: bool = False
    has_subscription: bool = False
    subscription_type: Optional[SubscriptionType] = None
    subscription_valid_until: Optional[date] = None
    account_status: AccountStatus = "active"


class VehicleProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_id: int
    license_plate: str
    owner_name: str
    registered_at: date
    phone: str
    current_balance: Decimal
    autopay_enabled: bool
    has_subscription: bool
    subscription_type: Optional[SubscriptionType] = None
    subscription_valid_until: Optional[date] = None
    account_status: AccountStatus


class VehicleBalance(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_id: int
    current_balance: Decimal
    account_status: AccountStatus
    autopay_enabled: bool


class TripCreate(BaseModel):
    entered_at: datetime
    exited_at: datetime
    trip_amount: Decimal = Field(..., ge=Decimal("0"), decimal_places=2)
    is_paid: bool
    payment_due_at: datetime

    @model_validator(mode="after")
    def exited_not_before_entered(self):
        if self.exited_at < self.entered_at:
            raise ValueError("exited_at must be greater than or equal to entered_at")
        return self


class TripRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trip_id: int
    vehicle_id: int
    entered_at: datetime
    exited_at: datetime
    trip_amount: Decimal
    is_paid: bool
    payment_due_at: datetime


class AccountTransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: int
    vehicle_id: int
    occurred_at: datetime
    operation_type: OperationType
    direction: Direction
    amount: Annotated[Decimal, Field(ge=Decimal("0"), decimal_places=2)]
    balance_after: Decimal
    trip_id: Optional[int] = None
    recommendation_event_id: Optional[int] = None


class TopUpRequest(BaseModel):
    amount: Decimal = Field(..., gt=Decimal("0"), decimal_places=2)


class TopUpResponse(BaseModel):
    vehicle_id: int
    current_balance: Decimal
    account_status: AccountStatus
    transaction: AccountTransactionRead


class RecommendationEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: int
    vehicle_id: int
    shown_at: datetime
    recommendation_type: RecommendationType
    title: str
    status: RecommendationStatus
    responded_at: Optional[datetime] = None
    deep_link: Optional[str] = None
    related_transaction_id: Optional[int] = None
    is_dynamic: bool = False


class VehicleBehaviorFeaturesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_id: int
    updated_at: datetime
    trips_7d: int
    trips_30d: int
    avg_trip_amount: Optional[Decimal] = None
    avg_trip_duration_min: Optional[int] = None
    weekend_trip_share: Optional[Decimal] = None
    morning_entry_share: Optional[Decimal] = None
    topup_count_30d: int
    avg_topup_amount: Optional[Decimal] = None
    debt_episodes_30d: int
    fines_count_30d: int
    days_since_registration: int
    trip_count_total: int
    segment_code: SegmentCode
    segment_name: str
    segment_assigned_at: datetime


class ForecastRead(BaseModel):
    vehicle_id: int
    horizon_days: int = 30
    average_trip_amount: Decimal
    trip_count: int
    forecast_amount: Decimal


class StatsRead(BaseModel):
    vehicle_id: int
    period: str
    total_spent: Decimal
    average_trip_amount: Decimal
    trip_count: int
    paid_trip_count: int
    unpaid_trip_count: int


# --- Recommendation respond ---
RecommendationRespondAction = Literal["accepted", "dismissed"]


class RecommendationRespondRequest(BaseModel):
    status: RecommendationRespondAction


# --- Me summary (home screen) ---
class MeSummaryVehicle(BaseModel):
    vehicle_id: int
    license_plate: str
    owner_name: str
    account_status: AccountStatus
    has_subscription: bool
    subscription_type: Optional[SubscriptionType] = None
    subscription_valid_until: Optional[date] = None


class MeSummaryBalance(BaseModel):
    current_balance: Decimal
    autopay_enabled: bool


class MeSummaryForecast(BaseModel):
    horizon_days: int
    forecast_amount: Decimal
    average_trip_amount: Decimal
    trip_count: int


class MeSummaryStats(BaseModel):
    total_spent: Decimal
    average_trip_amount: Decimal
    trip_count: int
    paid_trip_count: int
    unpaid_trip_count: int


class MeSummaryRecommendations(BaseModel):
    active_count: int
    latest: list[RecommendationEventRead]


class MeSummaryMl(BaseModel):
    available: bool
    reason: Optional[str] = None
    spend_forecast_7d: Optional[Decimal] = None
    spend_forecast_30d: Optional[Decimal] = None
    debt_risk_7d: Optional[float] = None
    top_recommendation_event_id: Optional[int] = None


class MeSummaryResponse(BaseModel):
    vehicle: MeSummaryVehicle
    balance: MeSummaryBalance
    forecast: MeSummaryForecast
    stats: MeSummaryStats
    recommendations: MeSummaryRecommendations
    ml: MeSummaryMl


# --- Autopay toggle ---
class AutopayUpdateRequest(BaseModel):
    autopay_enabled: bool


class AutopayUpdateResponse(BaseModel):
    vehicle_id: int
    autopay_enabled: bool


# --- Health ---
class HealthLiveResponse(BaseModel):
    status: str
    service: str


class HealthReadyResponse(BaseModel):
    status: str
    database: str


# --- ML inference (/me/ml) ---
class MlStatusResponse(BaseModel):
    available: bool
    models_dir: str
    reason: Optional[str] = None
    trained_at: Optional[str] = None
    models: Optional[list[str]] = None
    metadata: Optional[dict] = None


class MlModelMetadataBrief(BaseModel):
    trained_at: Optional[str] = None
    version: Optional[str] = None


class MlPredictionsResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    vehicle_id: Optional[int] = None
    snapshot_at: Optional[str] = None
    spend_forecast_7d: Optional[Decimal] = None
    spend_forecast_30d: Optional[Decimal] = None
    debt_risk_7d: Optional[float] = None
    model_metadata: Optional[MlModelMetadataBrief] = None


class MlRankedRecommendationItem(BaseModel):
    event_id: int
    recommendation_type: str
    title: str
    deep_link: Optional[str] = None
    status: str
    acceptance_probability: Optional[float] = None
    debt_risk_7d: Optional[float] = None
    estimated_value: Optional[Decimal] = None
    business_priority: int
    hybrid_score: Optional[float] = None


class MlRecommendationsResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    vehicle_id: Optional[int] = None
    items: list[MlRankedRecommendationItem] = []
