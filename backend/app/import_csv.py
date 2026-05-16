"""
Импорт данных из CSV в правильном порядке (vehicles → trips → …).

Запуск:
  python -m app.import_csv data
  python -m app.import_csv /path/to/project/data

Ожидаемые файлы в каталоге:
  sample_vehicles.csv
  sample_trips.csv
  sample_recommendation_events.csv
  sample_account_transactions.csv
  sample_vehicle_behavior_features.csv

Сквозные FK recommendation_events ↔ account_transactions в CSV можно оставить
пустыми: при непустых значениях выполняется вторая фаза обновления событий.
"""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app import models


def _strip(s: Any) -> str:
    if s is None:
        return ""
    return str(s).strip()


def _parse_optional_int(raw: str) -> int | None:
    if raw == "":
        return None
    return int(raw)


def _parse_optional_date(raw: str) -> date | None:
    if raw == "":
        return None
    return date.fromisoformat(raw)


def _parse_dt(raw: str) -> datetime:
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1]
    return datetime.fromisoformat(s)


def _parse_decimal(raw: str) -> Decimal:
    return Decimal(raw.strip())


def _parse_bool(raw: str) -> bool:
    v = raw.strip().lower()
    if v in ("1", "true", "yes", "y"):
        return True
    if v in ("0", "false", "no", "n"):
        return False
    raise ValueError(f"ожидался boolean, получено: {raw!r}")


def import_vehicles(db: Session, path: Path) -> dict[str, Any]:
    imported = skipped = 0
    errors: list[str] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {
            "license_plate",
            "owner_name",
            "registered_at",
            "phone",
            "current_balance",
            "autopay_enabled",
            "has_subscription",
            "account_status",
        }
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(
                f"{path.name}: нужны колонки {sorted(required)}"
            )

        for row_num, row in enumerate(reader, start=2):
            try:
                plate = _strip(row["license_plate"])
                if (
                    db.query(models.Vehicle)
                    .filter_by(license_plate=plate)
                    .first()
                ):
                    skipped += 1
                    continue

                sub_type = _strip(row.get("subscription_type", "")) or None
                sub_until = _parse_optional_date(
                    _strip(row.get("subscription_valid_until", ""))
                )

                v = models.Vehicle(
                    license_plate=plate,
                    owner_name=_strip(row["owner_name"]),
                    registered_at=date.fromisoformat(_strip(row["registered_at"])),
                    phone=_strip(row["phone"]),
                    current_balance=_parse_decimal(row["current_balance"]),
                    autopay_enabled=_parse_bool(row["autopay_enabled"]),
                    has_subscription=_parse_bool(row["has_subscription"]),
                    subscription_type=sub_type,
                    subscription_valid_until=sub_until,
                    account_status=_strip(row["account_status"]),
                )
                db.add(v)
                db.commit()
                imported += 1
            except (ValueError, KeyError, IntegrityError, InvalidOperation) as exc:
                skipped += 1
                errors.append(f"{path.name}:{row_num}: {exc}")
                db.rollback()

    return {"imported": imported, "skipped": skipped, "errors": errors}


def import_trips(db: Session, path: Path) -> dict[str, Any]:
    imported = skipped = 0
    errors: list[str] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {
            "vehicle_id",
            "entered_at",
            "exited_at",
            "trip_amount",
            "is_paid",
            "payment_due_at",
        }
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"{path.name}: нужны колонки {sorted(required)}")

        for row_num, row in enumerate(reader, start=2):
            try:
                vid = int(_strip(row["vehicle_id"]))
                if not db.get(models.Vehicle, vid):
                    skipped += 1
                    errors.append(
                        f"{path.name}:{row_num}: vehicle_id={vid} не найден (FK)"
                    )
                    continue

                entered = _parse_dt(row["entered_at"])
                exited = _parse_dt(row["exited_at"])
                if exited < entered:
                    skipped += 1
                    errors.append(
                        f"{path.name}:{row_num}: exited_at раньше entered_at"
                    )
                    continue

                trip = models.Trip(
                    vehicle_id=vid,
                    entered_at=entered,
                    exited_at=exited,
                    trip_amount=_parse_decimal(row["trip_amount"]),
                    is_paid=_parse_bool(row["is_paid"]),
                    payment_due_at=_parse_dt(row["payment_due_at"]),
                )
                db.add(trip)
                db.commit()
                imported += 1
            except (ValueError, KeyError, IntegrityError, InvalidOperation) as exc:
                skipped += 1
                errors.append(f"{path.name}:{row_num}: {exc}")
                db.rollback()

    return {"imported": imported, "skipped": skipped, "errors": errors}


def import_recommendation_events_phase1(
    db: Session, path: Path
) -> dict[str, Any]:
    """Вставка событий; related_transaction_id сохраняем для фазы 2."""
    imported = skipped = 0
    errors: list[str] = []
    update_queue: list[tuple[int, int]] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {
            "vehicle_id",
            "shown_at",
            "recommendation_type",
            "title",
            "status",
        }
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"{path.name}: нужны колонки {sorted(required)}")

        for row_num, row in enumerate(reader, start=2):
            try:
                vid = int(_strip(row["vehicle_id"]))
                if not db.get(models.Vehicle, vid):
                    skipped += 1
                    errors.append(
                        f"{path.name}:{row_num}: vehicle_id={vid} не найден (FK)"
                    )
                    continue

                rel_tx_raw = _strip(row.get("related_transaction_id", ""))
                rel_tx = _parse_optional_int(rel_tx_raw) if rel_tx_raw else None

                responded = _strip(row.get("responded_at", ""))
                deep_link = _strip(row.get("deep_link", "")) or None

                ev = models.RecommendationEvent(
                    vehicle_id=vid,
                    shown_at=_parse_dt(row["shown_at"]),
                    recommendation_type=_strip(row["recommendation_type"]),
                    title=_strip(row["title"]),
                    status=_strip(row["status"]),
                    responded_at=_parse_dt(responded) if responded else None,
                    deep_link=deep_link,
                    related_transaction_id=None,
                )
                db.add(ev)
                db.flush()
                if rel_tx is not None:
                    update_queue.append((ev.event_id, rel_tx))
                db.commit()
                imported += 1
            except (ValueError, KeyError, IntegrityError, InvalidOperation) as exc:
                skipped += 1
                errors.append(f"{path.name}:{row_num}: {exc}")
                db.rollback()

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "update_queue": update_queue,
    }


def import_account_transactions(
    db: Session, path: Path
) -> dict[str, Any]:
    imported = skipped = 0
    errors: list[str] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {
            "vehicle_id",
            "occurred_at",
            "operation_type",
            "direction",
            "amount",
            "balance_after",
        }
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"{path.name}: нужны колонки {sorted(required)}")

        for row_num, row in enumerate(reader, start=2):
            try:
                vid = int(_strip(row["vehicle_id"]))
                if not db.get(models.Vehicle, vid):
                    skipped += 1
                    errors.append(
                        f"{path.name}:{row_num}: vehicle_id={vid} не найден (FK)"
                    )
                    continue

                trip_raw = _strip(row.get("trip_id", ""))
                trip_id = _parse_optional_int(trip_raw) if trip_raw else None
                if trip_id is not None:
                    tr = db.get(models.Trip, trip_id)
                    if tr is None or tr.vehicle_id != vid:
                        skipped += 1
                        errors.append(
                            f"{path.name}:{row_num}: trip_id={trip_id} "
                            f"не найден или другой vehicle (FK)"
                        )
                        continue

                rev_raw = _strip(row.get("recommendation_event_id", ""))
                rev_id = _parse_optional_int(rev_raw) if rev_raw else None
                if rev_id is not None:
                    ev = db.get(models.RecommendationEvent, rev_id)
                    if ev is None or ev.vehicle_id != vid:
                        skipped += 1
                        errors.append(
                            f"{path.name}:{row_num}: recommendation_event_id="
                            f"{rev_id} не найден или другой vehicle (FK)"
                        )
                        continue

                tx = models.AccountTransaction(
                    vehicle_id=vid,
                    occurred_at=_parse_dt(row["occurred_at"]),
                    operation_type=_strip(row["operation_type"]),
                    direction=_strip(row["direction"]),
                    amount=_parse_decimal(row["amount"]),
                    balance_after=_parse_decimal(row["balance_after"]),
                    trip_id=trip_id,
                    recommendation_event_id=rev_id,
                )
                db.add(tx)
                db.commit()
                imported += 1
            except (ValueError, KeyError, IntegrityError, InvalidOperation) as exc:
                skipped += 1
                errors.append(f"{path.name}:{row_num}: {exc}")
                db.rollback()

    return {"imported": imported, "skipped": skipped, "errors": errors}


def sync_vehicle_balances_from_transactions(db: Session) -> int:
    """Выставить current_balance по последней операции (MVP-согласование)."""
    updated = 0
    for v in db.query(models.Vehicle).all():
        last = (
            db.query(models.AccountTransaction)
            .filter_by(vehicle_id=v.vehicle_id)
            .order_by(
                models.AccountTransaction.occurred_at.desc(),
                models.AccountTransaction.transaction_id.desc(),
            )
            .first()
        )
        if last is not None:
            v.current_balance = last.balance_after
            updated += 1
    db.commit()
    return updated


def apply_recommendation_related_transactions(
    db: Session, queue: list[tuple[int, int]]
) -> dict[str, Any]:
    applied = 0
    errors: list[str] = []
    for event_id, tx_id in queue:
        ev = db.get(models.RecommendationEvent, event_id)
        tx = db.get(models.AccountTransaction, tx_id)
        if ev is None or tx is None:
            errors.append(
                f"связь event_id={event_id} → transaction_id={tx_id}: "
                "запись не найдена"
            )
            continue
        if tx.vehicle_id != ev.vehicle_id:
            errors.append(
                f"связь event_id={event_id}: транзакция {tx_id} другого авто"
            )
            continue
        ev.related_transaction_id = tx_id
        applied += 1
    db.commit()
    return {"applied": applied, "errors": errors}


def import_vehicle_behavior_features(db: Session, path: Path) -> dict[str, Any]:
    imported = skipped = 0
    errors: list[str] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {
            "vehicle_id",
            "updated_at",
            "trips_7d",
            "trips_30d",
            "topup_count_30d",
            "debt_episodes_30d",
            "fines_count_30d",
            "days_since_registration",
            "trip_count_total",
            "segment_code",
            "segment_name",
            "segment_assigned_at",
        }
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"{path.name}: нужны колонки {sorted(required)}")

        for row_num, row in enumerate(reader, start=2):
            try:
                vid = int(_strip(row["vehicle_id"]))
                if not db.get(models.Vehicle, vid):
                    skipped += 1
                    errors.append(
                        f"{path.name}:{row_num}: vehicle_id={vid} не найден (FK)"
                    )
                    continue

                at_raw = _strip(row.get("avg_trip_amount", ""))
                avg_trip = _parse_decimal(at_raw) if at_raw else None
                dur_raw = _strip(row.get("avg_trip_duration_min", ""))
                avg_dur = int(dur_raw) if dur_raw else None
                ws_raw = _strip(row.get("weekend_trip_share", ""))
                weekend = _parse_decimal(ws_raw) if ws_raw else None
                ms_raw = _strip(row.get("morning_entry_share", ""))
                morning = _parse_decimal(ms_raw) if ms_raw else None
                atu_raw = _strip(row.get("avg_topup_amount", ""))
                avg_top = _parse_decimal(atu_raw) if atu_raw else None

                row_model = models.VehicleBehaviorFeatures(
                    vehicle_id=vid,
                    updated_at=_parse_dt(row["updated_at"]),
                    trips_7d=int(_strip(row["trips_7d"])),
                    trips_30d=int(_strip(row["trips_30d"])),
                    avg_trip_amount=avg_trip,
                    avg_trip_duration_min=avg_dur,
                    weekend_trip_share=weekend,
                    morning_entry_share=morning,
                    topup_count_30d=int(_strip(row["topup_count_30d"])),
                    avg_topup_amount=avg_top,
                    debt_episodes_30d=int(_strip(row["debt_episodes_30d"])),
                    fines_count_30d=int(_strip(row["fines_count_30d"])),
                    days_since_registration=int(
                        _strip(row["days_since_registration"])
                    ),
                    trip_count_total=int(_strip(row["trip_count_total"])),
                    segment_code=_strip(row["segment_code"]),
                    segment_name=_strip(row["segment_name"]),
                    segment_assigned_at=_parse_dt(row["segment_assigned_at"]),
                )
                existing = db.get(models.VehicleBehaviorFeatures, vid)
                if existing:
                    db.delete(existing)
                    db.flush()
                db.add(row_model)
                db.commit()
                imported += 1
            except (ValueError, KeyError, IntegrityError, InvalidOperation) as exc:
                skipped += 1
                errors.append(f"{path.name}:{row_num}: {exc}")
                db.rollback()

    return {"imported": imported, "skipped": skipped, "errors": errors}


def import_directory(directory: Path) -> dict[str, Any]:
    directory = directory.resolve()
    summary: dict[str, Any] = {"directory": str(directory), "steps": []}

    files = {
        "vehicles": directory / "sample_vehicles.csv",
        "trips": directory / "sample_trips.csv",
        "recommendation_events": directory / "sample_recommendation_events.csv",
        "account_transactions": directory / "sample_account_transactions.csv",
        "behavior": directory / "sample_vehicle_behavior_features.csv",
    }

    db = SessionLocal()
    update_queue: list[tuple[int, int]] = []
    try:
        if files["vehicles"].exists():
            r = import_vehicles(db, files["vehicles"])
            summary["steps"].append({"vehicles": r})
        else:
            summary["steps"].append({"vehicles": "skipped (file missing)"})

        if files["trips"].exists():
            r = import_trips(db, files["trips"])
            summary["steps"].append({"trips": r})
        else:
            summary["steps"].append({"trips": "skipped (file missing)"})

        if files["recommendation_events"].exists():
            r = import_recommendation_events_phase1(
                db, files["recommendation_events"]
            )
            update_queue.extend(r.pop("update_queue", []))
            summary["steps"].append({"recommendation_events": r})
        else:
            summary["steps"].append(
                {"recommendation_events": "skipped (file missing)"}
            )

        if files["account_transactions"].exists():
            r = import_account_transactions(
                db, files["account_transactions"]
            )
            summary["steps"].append({"account_transactions": r})
        else:
            summary["steps"].append(
                {"account_transactions": "skipped (file missing)"}
            )

        if update_queue:
            r = apply_recommendation_related_transactions(db, update_queue)
            summary["steps"].append({"recommendation_event_links": r})

        if files["account_transactions"].exists():
            n = sync_vehicle_balances_from_transactions(db)
            summary["steps"].append({"balance_sync": {"vehicles_updated": n}})

        if files["behavior"].exists():
            r = import_vehicle_behavior_features(db, files["behavior"])
            summary["steps"].append({"vehicle_behavior_features": r})
        else:
            summary["steps"].append({"vehicle_behavior_features": "skipped"})

    finally:
        db.close()

    return summary


def main() -> None:
    if len(sys.argv) != 2:
        print(
            "Использование: python -m app.import_csv <каталог_с_CSV>\n"
            "Пример: python -m app.import_csv data"
        )
        sys.exit(1)

    target = Path(sys.argv[1])
    if not target.is_dir():
        print(f"Каталог не найден: {target}")
        sys.exit(1)

    result = import_directory(target)
    print(result)


if __name__ == "__main__":
    main()
