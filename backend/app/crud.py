from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app import models
from app.schemas import TopUpRequest, TripCreate, VehicleCreate


def get_vehicle(db: Session, vehicle_id: int) -> models.Vehicle | None:
    return (
        db.query(models.Vehicle)
        .filter(models.Vehicle.vehicle_id == vehicle_id)
        .first()
    )


def get_vehicle_by_phone(db: Session, phone: str) -> models.Vehicle | None:
    return (
        db.query(models.Vehicle).filter(models.Vehicle.phone == phone).first()
    )


def get_vehicle_by_license_plate(
    db: Session, license_plate: str
) -> models.Vehicle | None:
    return (
        db.query(models.Vehicle)
        .filter(models.Vehicle.license_plate == license_plate)
        .first()
    )


def get_user_by_phone(db: Session, phone: str) -> models.User | None:
    vehicle = get_vehicle_by_phone(db, phone)
    if vehicle is None:
        return None
    return (
        db.query(models.User)
        .filter(models.User.vehicle_id == vehicle.vehicle_id)
        .first()
    )


def get_user(db: Session, user_id: int) -> models.User | None:
    return db.query(models.User).filter(models.User.id == user_id).first()


def create_user_with_vehicle(
    db: Session,
    *,
    phone: str,
    password_hash: str,
    license_plate: str,
    owner_name: str,
) -> tuple[models.User, models.Vehicle]:
    vehicle = models.Vehicle(
        license_plate=license_plate.strip(),
        owner_name=owner_name.strip(),
        registered_at=date.today(),
        phone=phone.strip(),
        current_balance=Decimal("0.00"),
        autopay_enabled=False,
        has_subscription=False,
        subscription_type=None,
        subscription_valid_until=None,
        account_status="active",
    )
    db.add(vehicle)
    db.flush()

    user = models.User(
        password_hash=password_hash,
        vehicle_id=vehicle.vehicle_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.refresh(vehicle)
    return user, vehicle


def create_vehicle(db: Session, data: VehicleCreate) -> models.Vehicle:
    vehicle = models.Vehicle(
        license_plate=data.license_plate.strip(),
        owner_name=data.owner_name.strip(),
        registered_at=data.registered_at,
        phone=data.phone.strip(),
        current_balance=data.current_balance,
        autopay_enabled=data.autopay_enabled,
        has_subscription=data.has_subscription,
        subscription_type=data.subscription_type,
        subscription_valid_until=data.subscription_valid_until,
        account_status=data.account_status,
    )
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


def delete_vehicle(db: Session, vehicle_id: int) -> bool:
    vehicle = get_vehicle(db, vehicle_id)
    if vehicle is None:
        return False
    db.delete(vehicle)
    db.commit()
    return True


def create_trip_for_vehicle(
    db: Session, vehicle_id: int, data: TripCreate
) -> tuple[models.Trip, models.AccountTransaction]:
    vehicle = get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise ValueError("vehicle_not_found")

    trip = models.Trip(
        vehicle_id=vehicle_id,
        entered_at=data.entered_at,
        exited_at=data.exited_at,
        trip_amount=data.trip_amount,
        is_paid=data.is_paid,
        payment_due_at=data.payment_due_at,
    )
    db.add(trip)
    db.flush()

    prev_balance = Decimal(vehicle.current_balance)
    new_balance = prev_balance - data.trip_amount

    tx = models.AccountTransaction(
        vehicle_id=vehicle_id,
        occurred_at=datetime.now(timezone.utc).replace(tzinfo=None),
        operation_type="trip_charge",
        direction="debit",
        amount=data.trip_amount,
        balance_after=new_balance,
        trip_id=trip.trip_id,
        recommendation_event_id=None,
    )
    db.add(tx)

    vehicle.current_balance = new_balance
    if new_balance < 0 or not data.is_paid:
        vehicle.account_status = "debt"
    elif new_balance >= 0 and data.is_paid:
        vehicle.account_status = "active"

    db.commit()
    db.refresh(trip)
    db.refresh(tx)
    db.refresh(vehicle)
    return trip, tx


def get_all_trips(db: Session, vehicle_id: int) -> list[models.Trip]:
    return (
        db.query(models.Trip)
        .filter(models.Trip.vehicle_id == vehicle_id)
        .order_by(models.Trip.entered_at.desc())
        .all()
    )


def get_trips_paginated(
    db: Session, vehicle_id: int, limit: int, offset: int
) -> tuple[list[models.Trip], int]:
    query = (
        db.query(models.Trip)
        .filter(models.Trip.vehicle_id == vehicle_id)
        .order_by(models.Trip.entered_at.desc())
    )
    total = query.count()
    items = query.offset(offset).limit(limit).all()
    return items, total


def get_transactions_paginated(
    db: Session, vehicle_id: int, limit: int, offset: int
) -> tuple[list[models.AccountTransaction], int]:
    query = (
        db.query(models.AccountTransaction)
        .filter(models.AccountTransaction.vehicle_id == vehicle_id)
        .order_by(models.AccountTransaction.occurred_at.desc())
    )
    total = query.count()
    items = query.offset(offset).limit(limit).all()
    return items, total


def get_recommendations_paginated(
    db: Session, vehicle_id: int, limit: int, offset: int
) -> tuple[list[models.RecommendationEvent], int]:
    query = (
        db.query(models.RecommendationEvent)
        .filter(models.RecommendationEvent.vehicle_id == vehicle_id)
        .order_by(models.RecommendationEvent.shown_at.desc())
    )
    total = query.count()
    items = query.offset(offset).limit(limit).all()
    return items, total


def get_behavior_features(
    db: Session, vehicle_id: int
) -> models.VehicleBehaviorFeatures | None:
    return (
        db.query(models.VehicleBehaviorFeatures)
        .filter(models.VehicleBehaviorFeatures.vehicle_id == vehicle_id)
        .first()
    )


def top_up_balance(
    db: Session, vehicle_id: int, data: TopUpRequest
) -> tuple[models.Vehicle, models.AccountTransaction]:
    vehicle = get_vehicle(db, vehicle_id)
    if vehicle is None:
        raise ValueError("vehicle_not_found")

    prev = Decimal(vehicle.current_balance)
    new_balance = prev + data.amount

    tx = models.AccountTransaction(
        vehicle_id=vehicle_id,
        occurred_at=datetime.now(timezone.utc).replace(tzinfo=None),
        operation_type="topup_manual",
        direction="credit",
        amount=data.amount,
        balance_after=new_balance,
        trip_id=None,
        recommendation_event_id=None,
    )
    db.add(tx)

    vehicle.current_balance = new_balance
    if new_balance >= 0:
        vehicle.account_status = "active"

    db.commit()
    db.refresh(vehicle)
    db.refresh(tx)
    return vehicle, tx
