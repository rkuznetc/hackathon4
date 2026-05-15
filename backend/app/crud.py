from sqlalchemy.orm import Session

from app import models
from app.schemas import DriverCreate, TopUpCreate, TripCreate


def get_driver(db: Session, driver_id: int) -> models.Driver | None:
    return db.query(models.Driver).filter(models.Driver.id == driver_id).first()


def get_user_by_email(db: Session, email: str) -> models.User | None:
    return db.query(models.User).filter(models.User.email == email).first()


def create_user_with_driver(
    db: Session, *, email: str, password_hash: str, name: str
) -> models.User:
    driver = models.Driver(name=name, profile_type="standard", balance=0.0)
    db.add(driver)
    db.flush()

    user = models.User(
        email=email,
        password_hash=password_hash,
        driver_id=driver.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.refresh(driver)
    return user


def delete_driver(db: Session, driver_id: int) -> bool:
    driver = get_driver(db, driver_id)
    if not driver:
        return False
    db.delete(driver)
    db.commit()
    return True


def create_driver(db: Session, data: DriverCreate) -> models.Driver:
    driver = models.Driver(
        name=data.name,
        profile_type=data.profile_type,
        balance=data.balance,
    )
    db.add(driver)
    db.commit()
    db.refresh(driver)
    return driver


def create_trip(db: Session, driver_id: int, data: TripCreate) -> models.Trip:
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


def get_all_trips(db: Session, driver_id: int) -> list[models.Trip]:
    return (
        db.query(models.Trip)
        .filter(models.Trip.driver_id == driver_id)
        .order_by(models.Trip.created_at.desc())
        .all()
    )


def get_trips_paginated(
    db: Session, driver_id: int, limit: int, offset: int
) -> tuple[list[models.Trip], int]:
    query = (
        db.query(models.Trip)
        .filter(models.Trip.driver_id == driver_id)
        .order_by(models.Trip.created_at.desc())
    )
    total = query.count()
    items = query.offset(offset).limit(limit).all()
    return items, total


def get_transactions_paginated(
    db: Session, driver_id: int, limit: int, offset: int
) -> tuple[list[models.Transaction], int]:
    query = (
        db.query(models.Transaction)
        .filter(models.Transaction.driver_id == driver_id)
        .order_by(models.Transaction.created_at.desc())
    )
    total = query.count()
    items = query.offset(offset).limit(limit).all()
    return items, total


def top_up_balance(db: Session, driver_id: int, data: TopUpCreate) -> models.Driver:
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
