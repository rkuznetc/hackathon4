from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Vehicle(Base):
    __tablename__ = "vehicles"

    vehicle_id = Column(Integer, primary_key=True, index=True)
    license_plate = Column(String(16), unique=True, nullable=False)
    owner_name = Column(String(100), nullable=False)
    registered_at = Column(Date, nullable=False)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    current_balance = Column(Numeric(12, 2), nullable=False)
    autopay_enabled = Column(Boolean, nullable=False)
    has_subscription = Column(Boolean, nullable=False)
    subscription_type = Column(String(30), nullable=True)
    subscription_valid_until = Column(Date, nullable=True)
    account_status = Column(String(20), nullable=False)

    user = relationship(
        "User",
        back_populates="vehicle",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    trips = relationship(
        "Trip",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    account_transactions = relationship(
        "AccountTransaction",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    recommendation_events = relationship(
        "RecommendationEvent",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    behavior_features = relationship(
        "VehicleBehaviorFeatures",
        back_populates="vehicle",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    password_hash = Column(String, nullable=False)
    vehicle_id = Column(
        Integer,
        ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    vehicle = relationship("Vehicle", back_populates="user")


class Trip(Base):
    __tablename__ = "trips"

    trip_id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(
        Integer,
        ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entered_at = Column(DateTime(timezone=False), nullable=False)
    exited_at = Column(DateTime(timezone=False), nullable=False)
    trip_amount = Column(Numeric(10, 2), nullable=False)
    is_paid = Column(Boolean, nullable=False)
    payment_due_at = Column(DateTime(timezone=False), nullable=False)

    vehicle = relationship("Vehicle", back_populates="trips")
    account_transactions = relationship(
        "AccountTransaction",
        back_populates="trip",
        passive_deletes=True,
    )


class AccountTransaction(Base):
    __tablename__ = "account_transactions"

    transaction_id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(
        Integer,
        ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    occurred_at = Column(DateTime(timezone=False), nullable=False)
    operation_type = Column(String(30), nullable=False)
    direction = Column(String(10), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    balance_after = Column(Numeric(12, 2), nullable=False)
    trip_id = Column(
        Integer,
        ForeignKey("trips.trip_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Циклическая связь с recommendation_events: FK создаётся отдельным ALTER (use_alter).
    recommendation_event_id = Column(
        Integer,
        ForeignKey(
            "recommendation_events.event_id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_account_transactions_recommendation_event_id",
        ),
        nullable=True,
        index=True,
    )

    vehicle = relationship("Vehicle", back_populates="account_transactions")
    trip = relationship("Trip", back_populates="account_transactions")
    recommendation_event = relationship(
        "RecommendationEvent",
        back_populates="account_transactions_for_event",
        foreign_keys=[recommendation_event_id],
    )
    recommendation_events_linked = relationship(
        "RecommendationEvent",
        back_populates="related_transaction",
        foreign_keys="RecommendationEvent.related_transaction_id",
    )


class RecommendationEvent(Base):
    __tablename__ = "recommendation_events"

    event_id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(
        Integer,
        ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    shown_at = Column(DateTime(timezone=False), nullable=False)
    recommendation_type = Column(String(30), nullable=False)
    title = Column(String(200), nullable=False)
    status = Column(String(20), nullable=False)
    responded_at = Column(DateTime(timezone=False), nullable=True)
    deep_link = Column(String(255), nullable=True)
    related_transaction_id = Column(
        Integer,
        ForeignKey(
            "account_transactions.transaction_id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_recommendation_events_related_transaction_id",
        ),
        nullable=True,
        index=True,
    )

    vehicle = relationship("Vehicle", back_populates="recommendation_events")
    related_transaction = relationship(
        "AccountTransaction",
        foreign_keys=[related_transaction_id],
        back_populates="recommendation_events_linked",
    )
    account_transactions_for_event = relationship(
        "AccountTransaction",
        back_populates="recommendation_event",
        foreign_keys="AccountTransaction.recommendation_event_id",
    )


class VehicleBehaviorFeatures(Base):
    __tablename__ = "vehicle_behavior_features"

    vehicle_id = Column(
        Integer,
        ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"),
        primary_key=True,
    )
    updated_at = Column(DateTime(timezone=False), nullable=False)
    trips_7d = Column(Integer, nullable=False)
    trips_30d = Column(Integer, nullable=False)
    avg_trip_amount = Column(Numeric(10, 2), nullable=True)
    avg_trip_duration_min = Column(Integer, nullable=True)
    weekend_trip_share = Column(Numeric(5, 4), nullable=True)
    morning_entry_share = Column(Numeric(5, 4), nullable=True)
    topup_count_30d = Column(Integer, nullable=False)
    avg_topup_amount = Column(Numeric(10, 2), nullable=True)
    debt_episodes_30d = Column(Integer, nullable=False)
    fines_count_30d = Column(Integer, nullable=False)
    days_since_registration = Column(Integer, nullable=False)
    trip_count_total = Column(Integer, nullable=False)
    segment_code = Column(String(30), nullable=False)
    segment_name = Column(String(100), nullable=False)
    segment_assigned_at = Column(DateTime(timezone=False), nullable=False)

    vehicle = relationship("Vehicle", back_populates="behavior_features")
