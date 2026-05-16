"""
Заполнение БД демо-данными (схема Vehicle).

Запуск внутри контейнера:
  docker compose exec backend python -m app.seed

Идемпотентность: если в БД уже есть автомобиль с госномером DEMO-ACTIVE,
скрипт завершает работу без изменений. Повторный запуск seed без очистки БД
может дублировать данные — используйте `docker compose down -v` перед
повторным полным развёртыванием.
"""

from datetime import date, datetime
from decimal import Decimal

from app import models
from app.database import Base, SessionLocal, engine
from app.security import get_password_hash

MARKER_PLATE = "DEMO-ACTIVE"


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        exists = (
            db.query(models.Vehicle)
            .filter(models.Vehicle.license_plate == MARKER_PLATE)
            .first()
        )
        if exists:
            print("Seed уже выполнен (найден DEMO-ACTIVE). Остановка.")
            return

        v1 = models.Vehicle(
            license_plate=MARKER_PLATE,
            owner_name="Иван Петров",
            registered_at=date(2025, 1, 10),
            phone="+79001001001",
            current_balance=Decimal("3730.00"),
            autopay_enabled=False,
            has_subscription=False,
            subscription_type=None,
            subscription_valid_until=None,
            account_status="active",
        )
        v2 = models.Vehicle(
            license_plate="DEMO-DEBT",
            owner_name="Мария Сидорова",
            registered_at=date(2025, 3, 1),
            phone="+79001001002",
            current_balance=Decimal("-370.50"),
            autopay_enabled=True,
            has_subscription=False,
            subscription_type=None,
            subscription_valid_until=None,
            account_status="debt",
        )
        v3 = models.Vehicle(
            license_plate="DEMO-SUB",
            owner_name="Алексей Козлов",
            registered_at=date(2025, 2, 20),
            phone="+79001001003",
            current_balance=Decimal("11481.00"),
            autopay_enabled=False,
            has_subscription=True,
            subscription_type="monthly_unlimited",
            subscription_valid_until=date(2026, 12, 31),
            account_status="active",
        )
        v_admin = models.Vehicle(
            license_plate="DEMO-ADMIN",
            owner_name="Администратор",
            registered_at=date(2025, 6, 1),
            phone="+79001009999",
            current_balance=Decimal("0.00"),
            autopay_enabled=False,
            has_subscription=False,
            subscription_type=None,
            subscription_valid_until=None,
            account_status="active",
        )
        db.add_all([v1, v2, v3, v_admin])
        db.flush()

        db.add_all(
            [
                models.User(
                    password_hash=get_password_hash("password123"),
                    vehicle_id=v1.vehicle_id,
                    is_admin=False,
                ),
                models.User(
                    password_hash=get_password_hash("password123"),
                    vehicle_id=v2.vehicle_id,
                    is_admin=False,
                ),
                models.User(
                    password_hash=get_password_hash("password123"),
                    vehicle_id=v3.vehicle_id,
                    is_admin=False,
                ),
                models.User(
                    password_hash=get_password_hash("admin123"),
                    vehicle_id=v_admin.vehicle_id,
                    is_admin=True,
                ),
            ]
        )

        trips = [
            models.Trip(
                vehicle_id=v1.vehicle_id,
                entered_at=datetime(2026, 1, 10, 8, 0, 0),
                exited_at=datetime(2026, 1, 10, 9, 30, 0),
                trip_amount=Decimal("450.00"),
                is_paid=True,
                payment_due_at=datetime(2026, 1, 11, 23, 59, 0),
            ),
            models.Trip(
                vehicle_id=v1.vehicle_id,
                entered_at=datetime(2026, 1, 11, 7, 0, 0),
                exited_at=datetime(2026, 1, 11, 7, 45, 0),
                trip_amount=Decimal("320.00"),
                is_paid=True,
                payment_due_at=datetime(2026, 1, 12, 12, 0, 0),
            ),
            models.Trip(
                vehicle_id=v2.vehicle_id,
                entered_at=datetime(2026, 1, 9, 10, 0, 0),
                exited_at=datetime(2026, 1, 9, 11, 0, 0),
                trip_amount=Decimal("390.00"),
                is_paid=False,
                payment_due_at=datetime(2026, 1, 10, 23, 59, 0),
            ),
            models.Trip(
                vehicle_id=v3.vehicle_id,
                entered_at=datetime(2026, 1, 8, 12, 0, 0),
                exited_at=datetime(2026, 1, 8, 14, 0, 0),
                trip_amount=Decimal("520.00"),
                is_paid=True,
                payment_due_at=datetime(2026, 1, 9, 0, 0, 0),
            ),
        ]
        db.add_all(trips)
        db.flush()

        txs = [
            models.AccountTransaction(
                vehicle_id=v1.vehicle_id,
                occurred_at=datetime(2026, 1, 5, 10, 0, 0),
                operation_type="topup_manual",
                direction="credit",
                amount=Decimal("5000.00"),
                balance_after=Decimal("5000.00"),
                trip_id=None,
                recommendation_event_id=None,
            ),
            models.AccountTransaction(
                vehicle_id=v1.vehicle_id,
                occurred_at=datetime(2026, 1, 10, 10, 0, 0),
                operation_type="trip_charge",
                direction="debit",
                amount=Decimal("450.00"),
                balance_after=Decimal("4050.00"),
                trip_id=trips[0].trip_id,
                recommendation_event_id=None,
            ),
            models.AccountTransaction(
                vehicle_id=v1.vehicle_id,
                occurred_at=datetime(2026, 1, 11, 8, 0, 0),
                operation_type="trip_charge",
                direction="debit",
                amount=Decimal("320.00"),
                balance_after=Decimal("3730.00"),
                trip_id=trips[1].trip_id,
                recommendation_event_id=None,
            ),
            models.AccountTransaction(
                vehicle_id=v2.vehicle_id,
                occurred_at=datetime(2026, 1, 8, 9, 0, 0),
                operation_type="topup_manual",
                direction="credit",
                amount=Decimal("100.00"),
                balance_after=Decimal("100.00"),
                trip_id=None,
                recommendation_event_id=None,
            ),
            models.AccountTransaction(
                vehicle_id=v2.vehicle_id,
                occurred_at=datetime(2026, 1, 9, 12, 0, 0),
                operation_type="trip_charge",
                direction="debit",
                amount=Decimal("390.00"),
                balance_after=Decimal("-290.00"),
                trip_id=trips[2].trip_id,
                recommendation_event_id=None,
            ),
            models.AccountTransaction(
                vehicle_id=v2.vehicle_id,
                occurred_at=datetime(2026, 1, 9, 13, 0, 0),
                operation_type="fine_assessed",
                direction="debit",
                amount=Decimal("80.50"),
                balance_after=Decimal("-370.50"),
                trip_id=None,
                recommendation_event_id=None,
            ),
            models.AccountTransaction(
                vehicle_id=v3.vehicle_id,
                occurred_at=datetime(2026, 1, 1, 8, 0, 0),
                operation_type="topup_manual",
                direction="credit",
                amount=Decimal("15000.00"),
                balance_after=Decimal("15000.00"),
                trip_id=None,
                recommendation_event_id=None,
            ),
            models.AccountTransaction(
                vehicle_id=v3.vehicle_id,
                occurred_at=datetime(2026, 1, 1, 9, 0, 0),
                operation_type="subscription_purchase",
                direction="debit",
                amount=Decimal("2999.00"),
                balance_after=Decimal("12001.00"),
                trip_id=None,
                recommendation_event_id=None,
            ),
            models.AccountTransaction(
                vehicle_id=v3.vehicle_id,
                occurred_at=datetime(2026, 1, 8, 15, 0, 0),
                operation_type="trip_charge",
                direction="debit",
                amount=Decimal("520.00"),
                balance_after=Decimal("11481.00"),
                trip_id=trips[3].trip_id,
                recommendation_event_id=None,
            ),
        ]
        db.add_all(txs)
        db.flush()

        db.add_all(
            [
                models.RecommendationEvent(
                    vehicle_id=v1.vehicle_id,
                    shown_at=datetime(2026, 1, 12, 8, 0, 0),
                    recommendation_type="enable_autopay",
                    title="Включите автоплатёж",
                    status="shown",
                    responded_at=None,
                    deep_link="/settings/autopay",
                    related_transaction_id=None,
                ),
                models.RecommendationEvent(
                    vehicle_id=v1.vehicle_id,
                    shown_at=datetime(2026, 1, 13, 9, 0, 0),
                    recommendation_type="topup_balance",
                    title="Пополните счёт для поездок",
                    status="shown",
                    responded_at=None,
                    deep_link="/me/top-up",
                    related_transaction_id=None,
                ),
                models.RecommendationEvent(
                    vehicle_id=v2.vehicle_id,
                    shown_at=datetime(2026, 1, 11, 9, 0, 0),
                    recommendation_type="repay_debt",
                    title="Погасите задолженность",
                    status="shown",
                    responded_at=None,
                    deep_link="/me/top-up",
                    related_transaction_id=None,
                ),
                models.RecommendationEvent(
                    vehicle_id=v3.vehicle_id,
                    shown_at=datetime(2026, 1, 7, 12, 0, 0),
                    recommendation_type="buy_subscription",
                    title="Оформите абонемент",
                    status="dismissed",
                    responded_at=datetime(2026, 1, 7, 12, 5, 0),
                    deep_link="/subscriptions",
                    related_transaction_id=txs[7].transaction_id,
                ),
            ]
        )

        db.add_all(
            [
                models.VehicleBehaviorFeatures(
                    vehicle_id=v1.vehicle_id,
                    updated_at=datetime(2026, 1, 12, 0, 0, 0),
                    trips_7d=5,
                    trips_30d=18,
                    avg_trip_amount=Decimal("410.00"),
                    avg_trip_duration_min=62,
                    weekend_trip_share=Decimal("0.2200"),
                    morning_entry_share=Decimal("0.4500"),
                    topup_count_30d=2,
                    avg_topup_amount=Decimal("2500.00"),
                    debt_episodes_30d=0,
                    fines_count_30d=0,
                    days_since_registration=367,
                    trip_count_total=24,
                    segment_code="commuter",
                    segment_name="Коммьютинг",
                    segment_assigned_at=datetime(2026, 1, 1, 0, 0, 0),
                ),
                models.VehicleBehaviorFeatures(
                    vehicle_id=v2.vehicle_id,
                    updated_at=datetime(2026, 1, 11, 0, 0, 0),
                    trips_7d=2,
                    trips_30d=6,
                    avg_trip_amount=Decimal("390.00"),
                    avg_trip_duration_min=55,
                    weekend_trip_share=Decimal("0.3000"),
                    morning_entry_share=Decimal("0.1000"),
                    topup_count_30d=1,
                    avg_topup_amount=Decimal("100.00"),
                    debt_episodes_30d=1,
                    fines_count_30d=1,
                    days_since_registration=290,
                    trip_count_total=8,
                    segment_code="new_user",
                    segment_name="Новый пользователь",
                    segment_assigned_at=datetime(2026, 1, 2, 0, 0, 0),
                ),
            ]
        )

        db.commit()
        print(
            "Seed выполнен (4 автомобиля + пользователи, в т.ч. admin + поездки + операции)."
        )
        print(
            "Обычный демо-логин: +79001001001..03 / password123"
        )
        print(
            "Admin (для /vehicles/*): телефон +79001009999, пароль admin123"
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()
