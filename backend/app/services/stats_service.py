from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app import models
from app.schemas import StatsRead


def _period_start(period: str) -> datetime | None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if period == "month":
        return now - timedelta(days=30)
    if period == "week":
        return now - timedelta(days=7)
    return None


def calculate_vehicle_stats(
    db: Session, vehicle_id: int, period: str = "month"
) -> StatsRead:
    since = _period_start(period)
    query = db.query(models.Trip).filter(models.Trip.vehicle_id == vehicle_id)
    if since is not None:
        query = query.filter(models.Trip.entered_at >= since)
    trips = query.all()

    if not trips:
        return StatsRead(
            vehicle_id=vehicle_id,
            period=period,
            total_spent=Decimal("0.00"),
            average_trip_amount=Decimal("0.00"),
            trip_count=0,
            paid_trip_count=0,
            unpaid_trip_count=0,
        )

    amounts = [Decimal(str(t.trip_amount)) for t in trips]
    total_spent = sum(amounts)
    trip_count = len(trips)
    paid_trip_count = sum(1 for t in trips if t.is_paid)
    unpaid_trip_count = trip_count - paid_trip_count
    average = (total_spent / trip_count).quantize(Decimal("0.01"))

    return StatsRead(
        vehicle_id=vehicle_id,
        period=period,
        total_spent=total_spent.quantize(Decimal("0.01")),
        average_trip_amount=average,
        trip_count=trip_count,
        paid_trip_count=paid_trip_count,
        unpaid_trip_count=unpaid_trip_count,
    )
