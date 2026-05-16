from sqlalchemy.orm import Session

from app import models
from app.schemas.common import (
    MeSummaryBalance,
    MeSummaryForecast,
    MeSummaryRecommendations,
    MeSummaryResponse,
    MeSummaryStats,
    MeSummaryVehicle,
)
from app.services.forecast_service import calculate_vehicle_forecast
from app.services.recommendation_service import (
    count_stored_shown_recommendations,
    get_latest_stored_shown_recommendations,
)
from app.services.stats_service import calculate_vehicle_stats


def build_me_summary(db: Session, vehicle: models.Vehicle) -> MeSummaryResponse:
    vid = vehicle.vehicle_id
    forecast = calculate_vehicle_forecast(db, vid)
    stats = calculate_vehicle_stats(db, vid, "month")

    vehicle_part = MeSummaryVehicle(
        vehicle_id=vehicle.vehicle_id,
        license_plate=vehicle.license_plate,
        owner_name=vehicle.owner_name,
        account_status=vehicle.account_status,
        has_subscription=vehicle.has_subscription,
        subscription_type=vehicle.subscription_type,
        subscription_valid_until=vehicle.subscription_valid_until,
    )
    balance_part = MeSummaryBalance(
        current_balance=vehicle.current_balance,
        autopay_enabled=vehicle.autopay_enabled,
    )
    forecast_part = MeSummaryForecast(
        horizon_days=forecast.horizon_days,
        forecast_amount=forecast.forecast_amount,
        average_trip_amount=forecast.average_trip_amount,
        trip_count=forecast.trip_count,
    )
    stats_part = MeSummaryStats(
        total_spent=stats.total_spent,
        average_trip_amount=stats.average_trip_amount,
        trip_count=stats.trip_count,
        paid_trip_count=stats.paid_trip_count,
        unpaid_trip_count=stats.unpaid_trip_count,
    )
    active = count_stored_shown_recommendations(db, vid)
    latest = get_latest_stored_shown_recommendations(db, vid, limit=3)

    return MeSummaryResponse(
        vehicle=vehicle_part,
        balance=balance_part,
        forecast=forecast_part,
        stats=stats_part,
        recommendations=MeSummaryRecommendations(
            active_count=active,
            latest=latest,
        ),
    )
