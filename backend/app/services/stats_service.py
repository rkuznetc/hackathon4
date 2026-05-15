from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import crud, models
from app.schemas import StatsRead


def _period_start(period: str) -> datetime | None:
    now = datetime.utcnow()
    if period == "month":
        return now - timedelta(days=30)
    if period == "week":
        return now - timedelta(days=7)
    return None


def calculate_driver_stats(db: Session, driver_id: int, period: str = "month") -> StatsRead:
    since = _period_start(period)
    query = db.query(models.Trip).filter(models.Trip.driver_id == driver_id)
    if since is not None:
        query = query.filter(models.Trip.created_at >= since)

    trips = query.all()
    if not trips:
        return StatsRead(
            driver_id=driver_id,
            period=period,
            total_spent=0.0,
            average_trip_cost=0.0,
            trip_count=0,
            top_road_name=None,
        )

    total_spent = sum(t.cost for t in trips)
    trip_count = len(trips)
    average = total_spent / trip_count
    top_road = Counter(t.road_name for t in trips).most_common(1)[0][0]

    return StatsRead(
        driver_id=driver_id,
        period=period,
        total_spent=round(total_spent, 2),
        average_trip_cost=round(average, 2),
        trip_count=trip_count,
        top_road_name=top_road,
    )
