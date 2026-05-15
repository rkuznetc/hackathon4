from sqlalchemy.orm import Session

from app import crud
from app.schemas import ForecastRead


def calculate_driver_forecast(
    db: Session, driver_id: int, horizon_days: int = 30
) -> ForecastRead:
    """Простой прогноз: средняя стоимость поездки × horizon_days."""
    trips = crud.get_all_trips(db, driver_id)
    if not trips:
        return ForecastRead(
            driver_id=driver_id,
            average_trip_cost=0.0,
            forecast_30_days=0.0,
            trip_count=0,
            horizon_days=horizon_days,
        )

    total = sum(t.cost for t in trips)
    avg = total / len(trips)
    return ForecastRead(
        driver_id=driver_id,
        average_trip_cost=round(avg, 2),
        forecast_30_days=round(avg * horizon_days, 2),
        trip_count=len(trips),
        horizon_days=horizon_days,
    )
