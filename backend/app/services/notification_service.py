from datetime import datetime

from sqlalchemy.orm import Session

from app import crud, models
from app.schemas import NotificationRead, PaginatedResponse
from app.services.forecast_service import calculate_driver_forecast


def build_low_balance_notification_if_needed(
    db: Session, driver_id: int
) -> NotificationRead | None:
    driver = crud.get_driver(db, driver_id)
    if not driver:
        return None

    forecast = calculate_driver_forecast(db, driver_id)
    if forecast.forecast_30_days > 0 and driver.balance < forecast.forecast_30_days:
        return NotificationRead(
            id=0,
            driver_id=driver_id,
            title="Низкий баланс",
            message=(
                f"Баланс {driver.balance:.2f} ₽ ниже прогноза "
                f"на {forecast.horizon_days} дней ({forecast.forecast_30_days:.2f} ₽). "
                "Пополните счёт."
            ),
            deeplink="/me/top-up",
            created_at=datetime.utcnow(),
        )
    return None


def get_driver_notifications(
    db: Session, driver_id: int, limit: int, offset: int
) -> PaginatedResponse[NotificationRead]:
    dynamic = build_low_balance_notification_if_needed(db, driver_id)

    query = (
        db.query(models.Notification)
        .filter(models.Notification.driver_id == driver_id)
        .order_by(models.Notification.created_at.desc())
    )
    stored_total = query.count()

    total = stored_total + (1 if dynamic else 0)

    if offset == 0 and dynamic is not None:
        stored_limit = max(limit - 1, 0)
        stored_items = query.offset(0).limit(stored_limit).all()
        items = [dynamic] + [
            NotificationRead.model_validate(n) for n in stored_items
        ]
    else:
        stored_offset = offset - 1 if dynamic and offset > 0 else offset
        stored_items = query.offset(stored_offset).limit(limit).all()
        items = [NotificationRead.model_validate(n) for n in stored_items]

    return PaginatedResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )
