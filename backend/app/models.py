from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base


class Driver(Base):
    __tablename__ = "drivers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    profile_type = Column(String, nullable=False)  # например: "standard", "premium"
    balance = Column(Float, default=0.0)

    trips = relationship("Trip", back_populates="driver")
    transactions = relationship("Transaction", back_populates="driver")
    notifications = relationship("Notification", back_populates="driver")


class Trip(Base):
    __tablename__ = "trips"

    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    road_name = Column(String, nullable=False)
    cost = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    driver = relationship("Driver", back_populates="trips")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    type = Column(String, nullable=False)  # "top_up", "trip"
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    driver = relationship("Driver", back_populates="transactions")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=False)
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    deeplink = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    driver = relationship("Driver", back_populates="notifications")
