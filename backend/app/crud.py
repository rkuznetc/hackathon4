from datetime import datetime

from sqlalchemy.orm import Session

from app import models, schemas


def get_driver(db: Session, driver_id: int) -> models.Driver | None:
    return db.query(models.Driver).filter(models.Driver.id == driver_id).first()


def create_driver(db: Session, data: schemas.DriverCreate) -> models.Driver:
    driver = models.Driver(
        name=data.name,
        profile_type=data.profile_type,
        balance=data.balance,
    )
    db.add(driver)
    db.commit()
    db.refresh(driver)
    return driver


def create_trip(db: Session, driver_id: int, data: schemas.TripCreate) -> models.Trip:
    trip = models.Trip(
        driver_id=driver_id,
        road_name=data.road_name,
        cost=data.cost,
    )
    db.add(trip)

    driver = get_driver(db, driver_id)
    driver.balance -= data.cost

    tx = models.Transaction(
        driver_id=driver_id,
        type="trip",
        amount=-data.cost,
    )
    db.add(tx)
    db.commit()
    db.refresh(trip)
    return trip


def get_trips(db: Session, driver_id: int) -> list[models.Trip]:
    return (
        db.query(models.Trip)
        .filter(models.Trip.driver_id == driver_id)
        .order_by(models.Trip.created_at.desc())
        .all()
    )


def top_up_balance(
    db: Session, driver_id: int, data: schemas.TopUpCreate
) -> models.Driver:
    driver = get_driver(db, driver_id)
    driver.balance += data.amount

    tx = models.Transaction(
        driver_id=driver_id,
        type="top_up",
        amount=data.amount,
    )
    db.add(tx)
    db.commit()
    db.refresh(driver)
    return driver


def get_forecast(db: Session, driver_id: int) -> schemas.ForecastRead:
    trips = get_trips(db, driver_id)
    if not trips:
        return schemas.ForecastRead(
            driver_id=driver_id,
            average_trip_cost=0.0,
            forecast_30_days=0.0,
            trip_count=0,
        )

    total = sum(t.cost for t in trips)
    avg = total / len(trips)
    return schemas.ForecastRead(
        driver_id=driver_id,
        average_trip_cost=round(avg, 2),
        forecast_30_days=round(avg * 30, 2),
        trip_count=len(trips),
    )


def get_notifications(db: Session, driver_id: int) -> list[schemas.NotificationRead]:
    """Уведомления из БД + динамическое, если баланс ниже прогноза."""
    stored = (
        db.query(models.Notification)
        .filter(models.Notification.driver_id == driver_id)
        .order_by(models.Notification.created_at.desc())
        .all()
    )
    result = [schemas.NotificationRead.model_validate(n) for n in stored]

    driver = get_driver(db, driver_id)
    if not driver:
        return result

    forecast = get_forecast(db, driver_id)
    if forecast.forecast_30_days > 0 and driver.balance < forecast.forecast_30_days:
        result.insert(
            0,
            schemas.NotificationRead(
                id=0,
                driver_id=driver_id,
                title="Низкий баланс",
                message=(
                    f"Баланс {driver.balance:.2f} ₽ ниже прогноза "
                    f"на 30 дней ({forecast.forecast_30_days:.2f} ₽). Пополните счёт."
                ),
                deeplink=f"/drivers/{driver_id}/top-up",
                created_at=datetime.utcnow(),
            ),
        )

    return result
