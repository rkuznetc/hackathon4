"""Runtime ML feature rows from PostgreSQL (past-only)."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app import models
from app.ml.snapshot_features import build_runtime_snapshot_row


def _vehicle_to_frame(vehicle: models.Vehicle) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "vehicle_id": vehicle.vehicle_id,
                "license_plate": vehicle.license_plate,
                "owner_name": vehicle.owner_name,
                "registered_at": vehicle.registered_at,
                "phone": vehicle.phone,
                "current_balance": float(vehicle.current_balance),
                "autopay_enabled": vehicle.autopay_enabled,
                "has_subscription": vehicle.has_subscription,
                "subscription_type": vehicle.subscription_type,
                "subscription_valid_until": vehicle.subscription_valid_until,
                "account_status": vehicle.account_status,
            }
        ]
    )


def _trips_to_frame(trips: list[models.Trip]) -> pd.DataFrame:
    if not trips:
        return pd.DataFrame(
            columns=[
                "trip_id",
                "vehicle_id",
                "entered_at",
                "exited_at",
                "trip_amount",
                "is_paid",
                "payment_due_at",
            ]
        )
    rows = []
    for t in trips:
        rows.append(
            {
                "trip_id": t.trip_id,
                "vehicle_id": t.vehicle_id,
                "entered_at": t.entered_at,
                "exited_at": t.exited_at,
                "trip_amount": float(t.trip_amount),
                "is_paid": t.is_paid,
                "payment_due_at": t.payment_due_at,
            }
        )
    return pd.DataFrame(rows)


def _transactions_to_frame(txs: list[models.AccountTransaction]) -> pd.DataFrame:
    if not txs:
        return pd.DataFrame(
            columns=[
                "transaction_id",
                "vehicle_id",
                "occurred_at",
                "operation_type",
                "direction",
                "amount",
                "balance_after",
            ]
        )
    rows = []
    for tx in txs:
        rows.append(
            {
                "transaction_id": tx.transaction_id,
                "vehicle_id": tx.vehicle_id,
                "occurred_at": tx.occurred_at,
                "operation_type": tx.operation_type,
                "direction": tx.direction,
                "amount": float(tx.amount),
                "balance_after": float(tx.balance_after),
            }
        )
    return pd.DataFrame(rows)


def build_runtime_features_for_vehicle(
    db: Session,
    vehicle: models.Vehicle,
    *,
    snapshot_at: datetime | None = None,
) -> pd.DataFrame:
    """One-row feature frame aligned with training feature_columns.json."""
    if snapshot_at is None:
        snapshot_at = datetime.now(timezone.utc).replace(tzinfo=None)

    trips = (
        db.query(models.Trip)
        .filter(models.Trip.vehicle_id == vehicle.vehicle_id)
        .filter(models.Trip.entered_at <= snapshot_at)
        .all()
    )
    txs = (
        db.query(models.AccountTransaction)
        .filter(models.AccountTransaction.vehicle_id == vehicle.vehicle_id)
        .filter(models.AccountTransaction.occurred_at <= snapshot_at)
        .all()
    )

    return build_runtime_snapshot_row(
        _vehicle_to_frame(vehicle),
        _trips_to_frame(trips),
        _transactions_to_frame(txs),
        vehicle.vehicle_id,
        pd.Timestamp(snapshot_at),
    )
