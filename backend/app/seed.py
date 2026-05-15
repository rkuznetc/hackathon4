"""
Заполнение БД тестовыми данными.
Запуск внутри контейнера:
  docker compose exec backend python -m app.seed
"""

from app.database import Base, SessionLocal, engine
from app.models import Driver, Notification, Trip


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        if db.query(Driver).count() > 0:
            print("Данные уже есть, seed пропущен.")
            return

        drivers = [
            Driver(name="Иван Петров", profile_type="standard", balance=5000.0),
            Driver(name="Мария Сидорова", profile_type="premium", balance=800.0),
            Driver(name="Алексей Козлов", profile_type="standard", balance=15000.0),
        ]
        db.add_all(drivers)
        db.commit()

        for d in drivers:
            db.refresh(d)

        trips = [
            Trip(driver_id=drivers[0].id, road_name="М-11", cost=450.0),
            Trip(driver_id=drivers[0].id, road_name="ЦКАД", cost=320.0),
            Trip(driver_id=drivers[0].id, road_name="М-4", cost=280.0),
            Trip(driver_id=drivers[1].id, road_name="М-11", cost=450.0),
            Trip(driver_id=drivers[1].id, road_name="М-12", cost=390.0),
            Trip(driver_id=drivers[2].id, road_name="ЦКАД", cost=520.0),
        ]
        db.add_all(trips)

        notifications = [
            Notification(
                driver_id=drivers[1].id,
                title="Добро пожаловать",
                message="Вы подключили профиль Premium.",
                deeplink="/drivers/{}/profile".format(drivers[1].id),
            ),
            Notification(
                driver_id=drivers[0].id,
                title="Новая платная дорога",
                message="Добавлен участок М-12 на карте.",
                deeplink="/map",
            ),
        ]
        db.add_all(notifications)
        db.commit()

        print("Seed выполнен:")
        for d in drivers:
            print(f"  driver id={d.id}, name={d.name}, balance={d.balance}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
