from decimal import Decimal

from sqlalchemy.orm import Session

from app import crud
from app.schemas import ForecastRead


def calculate_vehicle_forecast(
    db: Session, vehicle_id: int, horizon_days: int = 30
) -> ForecastRead:
    """MVP: средняя сумма поездки × horizon_days (по истории поездок)."""
    trips = crud.get_all_trips(db, vehicle_id)
    if not trips:
        return ForecastRead(
            vehicle_id=vehicle_id,
            horizon_days=horizon_days,
            average_trip_amount=Decimal("0.00"),
            trip_count=0,
            forecast_amount=Decimal("0.00"),
        )

    total = sum(Decimal(str(t.trip_amount)) for t in trips)
    n = len(trips)
    avg = (total / n).quantize(Decimal("0.01"))
    forecast = (avg * Decimal(horizon_days)).quantize(Decimal("0.01"))
    return ForecastRead(
        vehicle_id=vehicle_id,
        horizon_days=horizon_days,
        average_trip_amount=avg,
        trip_count=n,
        forecast_amount=forecast,
    )
