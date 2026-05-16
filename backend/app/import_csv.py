"""
Импорт сгенерированных CSV в БД (офлайн, CLI только — не HTTP).

Ожидаемые имена файлов (или legacy sample_* — см. resolve_import_files):
  vehicles.csv
  trips.csv
  account_transactions.csv
  recommendation_events.csv
  vehicle_behavior_features.csv

Порядок и цикл FK recommendation_events ⇄ account_transactions:
  vehicles → (опционально demo users) → trips → recommendation_events (без related_transaction_id)
  → account_transactions → проставить related_transaction_id у событий → sync балансов → behavior.

Запуск:
  python -m app.import_csv /app/data --create-demo-users --demo-password password123

По умолчанию импорт только в пустую БД (нет строк в core-таблицах).
Пересоздание: docker compose down -v && docker compose up --build -d
Или явно: --force-clear (опасно: удаляет все строки из core-таблиц).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal
from app.security import get_password_hash

# --- Имена файлов: основной набор (генератор) и legacy sample_* ---
FILE_SPECS: list[tuple[str, str]] = [
    ("vehicles", "vehicles.csv", "sample_vehicles.csv"),
    ("trips", "trips.csv", "sample_trips.csv"),
    ("account_transactions", "account_transactions.csv", "sample_account_transactions.csv"),
    ("recommendation_events", "recommendation_events.csv", "sample_recommendation_events.csv"),
    ("vehicle_behavior_features", "vehicle_behavior_features.csv", "sample_vehicle_behavior_features.csv"),
]

ALLOWED_ACCOUNT_STATUS = frozenset({"active", "debt", "blocked"})
ALLOWED_OPERATION_TYPE = frozenset(
    {
        "trip_charge",
        "topup_manual",
        "topup_autopay",
        "fine_assessed",
        "fine_paid",
        "subscription_purchase",
    }
)
ALLOWED_DIRECTION = frozenset({"credit", "debit"})
ALLOWED_RECOMMENDATION_TYPE = frozenset(
    {
        "enable_autopay",
        "buy_subscription",
        "repay_debt",
        "topup_balance",
        "topup_forecast",
        "pay_before_deadline",
    }
)
ALLOWED_RECOMMENDATION_STATUS = frozenset({"shown", "accepted", "dismissed", "expired"})
ALLOWED_SEGMENT_CODE = frozenset(
    {"commuter", "weekend_guest", "taxi_driver", "tourist", "new_user"}
)

REQUIRED_COLS = {
    "vehicles": {
        "vehicle_id",
        "license_plate",
        "owner_name",
        "registered_at",
        "phone",
        "current_balance",
        "autopay_enabled",
        "has_subscription",
        "account_status",
    },
    "trips": {
        "trip_id",
        "vehicle_id",
        "entered_at",
        "exited_at",
        "trip_amount",
        "is_paid",
        "payment_due_at",
    },
    "account_transactions": {
        "transaction_id",
        "vehicle_id",
        "occurred_at",
        "operation_type",
        "direction",
        "amount",
        "balance_after",
    },
    "recommendation_events": {
        "event_id",
        "vehicle_id",
        "shown_at",
        "recommendation_type",
        "title",
        "status",
    },
    "vehicle_behavior_features": {
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
    },
}


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
    return Decimal(str(raw).strip())


def _parse_bool(raw: str) -> bool:
    v = str(raw).strip().lower()
    if v in ("1", "true", "yes", "y"):
        return True
    if v in ("0", "false", "no", "n"):
        return False
    raise ValueError(f"ожидался boolean, получено: {raw!r}")


def resolve_import_files(directory: Path) -> tuple[dict[str, Path], list[str]]:
    """Возвращает map key -> path и список предупреждений (legacy имена)."""
    directory = directory.resolve()
    out: dict[str, Path] = {}
    warnings: list[str] = []
    for key, primary, legacy in FILE_SPECS:
        p1 = directory / primary
        p2 = directory / legacy
        if p1.exists():
            out[key] = p1
        elif p2.exists():
            out[key] = p2
            warnings.append(f"используется legacy файл {legacy} вместо {primary}")
        else:
            raise FileNotFoundError(
                f"В каталоге {directory} нет ни {primary}, ни {legacy} для «{key}»"
            )
    return out, warnings


def _read_csv(path: Path) -> tuple[list[str | None], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = [dict(row) for row in reader]
    return list(fieldnames) if fieldnames else [], rows


def _require_columns(kind: str, path: Path, fieldnames: list[str | None]) -> None:
    names = set(fieldnames) - {None}
    req = REQUIRED_COLS[kind]
    if not req.issubset(names):
        missing = sorted(req - names)
        raise ValueError(f"{path.name}: отсутствуют обязательные колонки: {missing}")


def _assert_no_duplicate_pks(kind: str, path: Path, ids: list[int]) -> None:
    c = Counter(ids)
    dup = sorted([i for i, n in c.items() if n > 1])
    if dup:
        raise ValueError(f"{path.name}: дубликаты первичного ключа ({kind}): {dup[:10]}...")


def validate_parsed_tables(tables: Mapping[str, tuple[Path, list[str | None], list[dict[str, str]]]]) -> None:
    """Полная проверка до касания БД: колонки, PK, FK между CSV, enums, неотрицательные суммы."""
    paths = {k: v[0] for k, v in tables.items()}
    fieldnames_map = {k: v[1] for k, v in tables.items()}
    rows_map = {k: v[2] for k, v in tables.items()}

    for kind in REQUIRED_COLS:
        _require_columns(kind, paths[kind], fieldnames_map[kind])

    vrows = rows_map["vehicles"]
    vehicle_ids: set[int] = set()
    plates: set[str] = set()
    phones: set[str] = set()
    for i, row in enumerate(vrows, start=2):
        p = paths["vehicles"]
        vid = int(_strip(row["vehicle_id"]))
        plate = _strip(row["license_plate"])
        phone = _strip(row["phone"])
        st = _strip(row["account_status"])
        if st not in ALLOWED_ACCOUNT_STATUS:
            raise ValueError(f"{p.name}:{i}: недопустимый account_status {st!r}")
        if vid in vehicle_ids:
            raise ValueError(f"{p.name}:{i}: дубликат vehicle_id={vid}")
        vehicle_ids.add(vid)
        if plate in plates:
            raise ValueError(f"{p.name}:{i}: дубликат license_plate {plate!r}")
        plates.add(plate)
        if phone in phones:
            raise ValueError(f"{p.name}:{i}: дубликат phone {phone!r}")
        phones.add(phone)
        _parse_decimal(row["current_balance"])
        try:
            _parse_bool(row["autopay_enabled"])
            _parse_bool(row["has_subscription"])
        except ValueError as e:
            raise ValueError(f"{p.name}:{i}: {e}") from e
        date.fromisoformat(_strip(row["registered_at"]))
        sub_t = _strip(row.get("subscription_type", ""))
        sub_u = _strip(row.get("subscription_valid_until", ""))
        if sub_t and sub_t not in (
            "daily_unlimited",
            "weekend_pack",
            "trip_pack_10",
            "trip_pack_30",
            "monthly_unlimited",
        ):
            raise ValueError(f"{p.name}:{i}: неожиданный subscription_type {sub_t!r}")
        if sub_u:
            date.fromisoformat(sub_u)

    _assert_no_duplicate_pks("vehicle_id", paths["vehicles"], list(vehicle_ids))

    trip_ids: set[int] = set()
    for i, row in enumerate(rows_map["trips"], start=2):
        p = paths["trips"]
        tid = int(_strip(row["trip_id"]))
        if tid in trip_ids:
            raise ValueError(f"{p.name}:{i}: дубликат trip_id={tid}")
        vid = int(_strip(row["vehicle_id"]))
        if vid not in vehicle_ids:
            raise ValueError(f"{p.name}:{i}: vehicle_id={vid} отсутствует в vehicles.csv")
        entered = _parse_dt(row["entered_at"])
        exited = _parse_dt(row["exited_at"])
        if exited < entered:
            raise ValueError(f"{p.name}:{i}: exited_at раньше entered_at")
        amt = _parse_decimal(row["trip_amount"])
        if amt < 0:
            raise ValueError(f"{p.name}:{i}: trip_amount < 0")
        _parse_bool(row["is_paid"])
        _parse_dt(row["payment_due_at"])
        trip_ids.add(tid)

    _assert_no_duplicate_pks("trip_id", paths["trips"], list(trip_ids))

    event_ids: set[int] = set()
    for i, row in enumerate(rows_map["recommendation_events"], start=2):
        p = paths["recommendation_events"]
        eid = int(_strip(row["event_id"]))
        if eid in event_ids:
            raise ValueError(f"{p.name}:{i}: дубликат event_id={eid}")
        vid = int(_strip(row["vehicle_id"]))
        if vid not in vehicle_ids:
            raise ValueError(f"{p.name}:{i}: vehicle_id={vid} отсутствует в vehicles.csv")
        rt = _strip(row["recommendation_type"])
        if rt not in ALLOWED_RECOMMENDATION_TYPE:
            raise ValueError(f"{p.name}:{i}: недопустимый recommendation_type {rt!r}")
        st = _strip(row["status"])
        if st not in ALLOWED_RECOMMENDATION_STATUS:
            raise ValueError(f"{p.name}:{i}: недопустимый status {st!r}")
        _parse_dt(row["shown_at"])
        resp = _strip(row.get("responded_at", ""))
        if resp:
            _parse_dt(resp)
        rel = _strip(row.get("related_transaction_id", ""))
        if rel:
            int(rel)  # проверим существование после парсинга transactions
        event_ids.add(eid)

    _assert_no_duplicate_pks("event_id", paths["recommendation_events"], list(event_ids))

    tx_ids: set[int] = set()
    for i, row in enumerate(rows_map["account_transactions"], start=2):
        p = paths["account_transactions"]
        txid = int(_strip(row["transaction_id"]))
        if txid in tx_ids:
            raise ValueError(f"{p.name}:{i}: дубликат transaction_id={txid}")
        vid = int(_strip(row["vehicle_id"]))
        if vid not in vehicle_ids:
            raise ValueError(f"{p.name}:{i}: vehicle_id={vid} отсутствует в vehicles.csv")
        op = _strip(row["operation_type"])
        if op not in ALLOWED_OPERATION_TYPE:
            raise ValueError(f"{p.name}:{i}: недопустимый operation_type {op!r}")
        dr = _strip(row["direction"])
        if dr not in ALLOWED_DIRECTION:
            raise ValueError(f"{p.name}:{i}: недопустимый direction {dr!r}")
        amt = _parse_decimal(row["amount"])
        if amt < 0:
            raise ValueError(f"{p.name}:{i}: amount < 0")
        _parse_decimal(row["balance_after"])
        _parse_dt(row["occurred_at"])
        trip_raw = _strip(row.get("trip_id", ""))
        if trip_raw:
            tid = int(trip_raw)
            if tid not in trip_ids:
                raise ValueError(f"{p.name}:{i}: trip_id={tid} отсутствует в trips.csv")
        rev_raw = _strip(row.get("recommendation_event_id", ""))
        if rev_raw:
            rid = int(rev_raw)
            if rid not in event_ids:
                raise ValueError(
                    f"{p.name}:{i}: recommendation_event_id={rid} отсутствует в recommendation_events.csv"
                )
        tx_ids.add(txid)

    _assert_no_duplicate_pks("transaction_id", paths["account_transactions"], list(tx_ids))

    # связь event → transaction из CSV
    for i, row in enumerate(rows_map["recommendation_events"], start=2):
        p = paths["recommendation_events"]
        rel = _strip(row.get("related_transaction_id", ""))
        if not rel:
            continue
        rtx = int(rel)
        if rtx not in tx_ids:
            raise ValueError(
                f"{p.name}:{i}: related_transaction_id={rtx} отсутствует в account_transactions.csv"
            )

    for i, row in enumerate(rows_map["account_transactions"], start=2):
        p = paths["account_transactions"]
        rev_raw = _strip(row.get("recommendation_event_id", ""))
        if not rev_raw:
            continue
        rid = int(rev_raw)
        ev_row = next(
            (r for r in rows_map["recommendation_events"] if int(_strip(r["event_id"])) == rid),
            None,
        )
        if ev_row is None:
            continue
        v_ev = int(_strip(ev_row["vehicle_id"]))
        if int(_strip(row["vehicle_id"])) != v_ev:
            raise ValueError(
                f"{p.name}:{i}: recommendation_event {rid} принадлежит другому vehicle_id"
            )

    for i, row in enumerate(rows_map["vehicle_behavior_features"], start=2):
        p = paths["vehicle_behavior_features"]
        vid = int(_strip(row["vehicle_id"]))
        if vid not in vehicle_ids:
            raise ValueError(f"{p.name}:{i}: vehicle_id={vid} отсутствует в vehicles.csv")
        seg = _strip(row["segment_code"])
        if seg not in ALLOWED_SEGMENT_CODE:
            raise ValueError(f"{p.name}:{i}: недопустимый segment_code {seg!r}")
        _parse_dt(row["updated_at"])
        _parse_dt(row["segment_assigned_at"])
        int(_strip(row["trips_7d"]))
        int(_strip(row["trips_30d"]))
        int(_strip(row["topup_count_30d"]))
        int(_strip(row["debt_episodes_30d"]))
        int(_strip(row["fines_count_30d"]))
        int(_strip(row["days_since_registration"]))
        int(_strip(row["trip_count_total"]))
        at_raw = _strip(row.get("avg_trip_amount", ""))
        if at_raw:
            _parse_decimal(at_raw)
        ad_raw = _strip(row.get("avg_trip_duration_min", ""))
        if ad_raw:
            int(ad_raw)
        for opt in ("weekend_trip_share", "morning_entry_share"):
            ox = _strip(row.get(opt, ""))
            if ox:
                _parse_decimal(ox)
        atu = _strip(row.get("avg_topup_amount", ""))
        if atu:
            _parse_decimal(atu)


def database_has_core_data(db: Session) -> bool:
    checks = [
        db.query(models.Vehicle).first(),
        db.query(models.Trip).first(),
        db.query(models.AccountTransaction).first(),
        db.query(models.RecommendationEvent).first(),
        db.query(models.VehicleBehaviorFeatures).first(),
    ]
    return any(x is not None for x in checks)


def force_clear_core_tables(db: Session) -> None:
    """Удаляет все строки из доменных таблиц (разрыв циклических FK)."""
    db.execute(text("UPDATE account_transactions SET recommendation_event_id = NULL"))
    db.execute(text("UPDATE recommendation_events SET related_transaction_id = NULL"))
    db.flush()
    db.query(models.AccountTransaction).delete(synchronize_session=False)
    db.query(models.RecommendationEvent).delete(synchronize_session=False)
    db.query(models.Trip).delete(synchronize_session=False)
    db.query(models.VehicleBehaviorFeatures).delete(synchronize_session=False)
    db.query(models.User).delete(synchronize_session=False)
    db.query(models.Vehicle).delete(synchronize_session=False)
    db.flush()


def _insert_vehicles(db: Session, path: Path, rows: list[dict[str, str]]) -> int:
    n = 0
    for row_num, row in enumerate(rows, start=2):
        try:
            sub_type = _strip(row.get("subscription_type", "")) or None
            sub_until = _parse_optional_date(_strip(row.get("subscription_valid_until", "")))
            vid = int(_strip(row["vehicle_id"]))
            v = models.Vehicle(
                vehicle_id=vid,
                license_plate=_strip(row["license_plate"]),
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
            n += 1
        except (ValueError, KeyError, InvalidOperation) as exc:
            raise ValueError(f"{path.name}:{row_num}: {exc}") from exc
    return n


def create_demo_users_for_vehicles(
    db: Session, demo_password: str
) -> tuple[int, list[str]]:
    """Создаёт User для каждого Vehicle без пользователя. Пропуск, если user уже есть."""
    created = 0
    notes: list[str] = []
    pwd_hash = get_password_hash(demo_password)
    for v in db.query(models.Vehicle).order_by(models.Vehicle.vehicle_id):
        existing = (
            db.query(models.User).filter_by(vehicle_id=v.vehicle_id).first()
        )
        if existing:
            notes.append(f"пропуск user: для vehicle_id={v.vehicle_id} уже есть запись users")
            continue
        db.add(
            models.User(
                password_hash=pwd_hash,
                vehicle_id=v.vehicle_id,
                is_admin=False,
            )
        )
        created += 1
    db.flush()
    return created, notes


def _insert_trips(db: Session, path: Path, rows: list[dict[str, str]]) -> int:
    n = 0
    for row_num, row in enumerate(rows, start=2):
        try:
            vid = int(_strip(row["vehicle_id"]))
            trip = models.Trip(
                trip_id=int(_strip(row["trip_id"])),
                vehicle_id=vid,
                entered_at=_parse_dt(row["entered_at"]),
                exited_at=_parse_dt(row["exited_at"]),
                trip_amount=_parse_decimal(row["trip_amount"]),
                is_paid=_parse_bool(row["is_paid"]),
                payment_due_at=_parse_dt(row["payment_due_at"]),
            )
            db.add(trip)
            n += 1
        except (ValueError, KeyError, InvalidOperation) as exc:
            raise ValueError(f"{path.name}:{row_num}: {exc}") from exc
    return n


def _insert_recommendation_events_phase1(
    db: Session, path: Path, rows: list[dict[str, str]]
) -> tuple[int, list[tuple[int, int]]]:
    update_queue: list[tuple[int, int]] = []
    n = 0
    for row_num, row in enumerate(rows, start=2):
        try:
            vid = int(_strip(row["vehicle_id"]))
            eid = int(_strip(row["event_id"]))
            rel_tx_raw = _strip(row.get("related_transaction_id", ""))
            rel_tx = _parse_optional_int(rel_tx_raw) if rel_tx_raw else None
            responded = _strip(row.get("responded_at", ""))
            deep_link = _strip(row.get("deep_link", "")) or None
            ev = models.RecommendationEvent(
                event_id=eid,
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
            if rel_tx is not None:
                update_queue.append((eid, rel_tx))
            n += 1
        except (ValueError, KeyError, InvalidOperation) as exc:
            raise ValueError(f"{path.name}:{row_num}: {exc}") from exc
    return n, update_queue


def _insert_account_transactions(
    db: Session, path: Path, rows: list[dict[str, str]]
) -> int:
    n = 0
    for row_num, row in enumerate(rows, start=2):
        try:
            vid = int(_strip(row["vehicle_id"]))
            trip_raw = _strip(row.get("trip_id", ""))
            trip_id = _parse_optional_int(trip_raw) if trip_raw else None
            rev_raw = _strip(row.get("recommendation_event_id", ""))
            rev_id = _parse_optional_int(rev_raw) if rev_raw else None
            tx = models.AccountTransaction(
                transaction_id=int(_strip(row["transaction_id"])),
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
            n += 1
        except (ValueError, KeyError, InvalidOperation) as exc:
            raise ValueError(f"{path.name}:{row_num}: {exc}") from exc
    return n


def sync_vehicle_balances_from_transactions(db: Session) -> int:
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
                f"связь event_id={event_id} → transaction_id={tx_id}: запись не найдена"
            )
            continue
        if tx.vehicle_id != ev.vehicle_id:
            errors.append(
                f"связь event_id={event_id}: транзакция {tx_id} другого авто"
            )
            continue
        ev.related_transaction_id = tx_id
        applied += 1
    return {"applied": applied, "errors": errors}


def _insert_behavior(
    db: Session, path: Path, rows: list[dict[str, str]]
) -> int:
    n = 0
    for row_num, row in enumerate(rows, start=2):
        try:
            vid = int(_strip(row["vehicle_id"]))
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
                days_since_registration=int(_strip(row["days_since_registration"])),
                trip_count_total=int(_strip(row["trip_count_total"])),
                segment_code=_strip(row["segment_code"]),
                segment_name=_strip(row["segment_name"]),
                segment_assigned_at=_parse_dt(row["segment_assigned_at"]),
            )
            db.add(row_model)
            n += 1
        except (ValueError, KeyError, InvalidOperation) as exc:
            raise ValueError(f"{path.name}:{row_num}: {exc}") from exc
    return n


def load_tables_from_disk(
    files: dict[str, Path],
) -> dict[str, tuple[Path, list[str | None], list[dict[str, str]]]]:
    out: dict[str, tuple[Path, list[str | None], list[dict[str, str]]]] = {}
    for key, path in files.items():
        fieldnames, rows = _read_csv(path)
        out[key] = (path, fieldnames, rows)
    return out


def run_import(
    directory: Path,
    *,
    db: Session | None = None,
    create_demo_users: bool = False,
    demo_password: str = "password123",
    force_clear: bool = False,
) -> dict[str, Any]:
    """
    Импорт каталога. Если db не передан, открывается SessionLocal и закрывается здесь.
    """
    directory = directory.resolve()
    files, legacy_warnings = resolve_import_files(directory)
    tables = load_tables_from_disk(files)
    validate_parsed_tables(tables)

    own_session = db is None
    if own_session:
        db = SessionLocal()

    summary: dict[str, Any] = {
        "directory": str(directory),
        "legacy_warnings": legacy_warnings,
        "vehicles_imported": 0,
        "trips_imported": 0,
        "account_transactions_imported": 0,
        "recommendation_events_imported": 0,
        "vehicle_behavior_features_imported": 0,
        "users_created": 0,
        "users_skipped_notes": [],
        "balance_sync_vehicles_updated": 0,
        "recommendation_links": None,
        "skipped_rows": 0,
    }

    try:
        if force_clear:
            force_clear_core_tables(db)
        elif database_has_core_data(db):
            raise RuntimeError(
                "В БД уже есть данные (vehicles/trips/account_transactions/"
                "recommendation_events/vehicle_behavior_features). И импорт только в пустую БД.\n"
                "Пересоздайте том: docker compose down -v && docker compose up --build -d\n"
                "Или запустите с --force-clear (удалит все строки из этих таблиц и users)."
            )

        vpath, _, vrows = tables["vehicles"]
        summary["vehicles_imported"] = _insert_vehicles(db, vpath, vrows)
        db.flush()

        if create_demo_users:
            uc, notes = create_demo_users_for_vehicles(db, demo_password)
            summary["users_created"] = uc
            summary["users_skipped_notes"] = notes

        tpath, _, trows = tables["trips"]
        summary["trips_imported"] = _insert_trips(db, tpath, trows)
        db.flush()

        rpath, _, rrows = tables["recommendation_events"]
        nrec, update_queue = _insert_recommendation_events_phase1(db, rpath, rrows)
        summary["recommendation_events_imported"] = nrec
        db.flush()

        apath, _, arows = tables["account_transactions"]
        summary["account_transactions_imported"] = _insert_account_transactions(
            db, apath, arows
        )
        db.flush()

        if update_queue:
            summary["recommendation_links"] = apply_recommendation_related_transactions(
                db, update_queue
            )
            errs = summary["recommendation_links"].get("errors") or []
            if errs:
                raise RuntimeError(
                    "Ошибки при проставлении related_transaction_id: " + "; ".join(errs)
                )

        summary["balance_sync_vehicles_updated"] = sync_vehicle_balances_from_transactions(
            db
        )

        bpath, _, brows = tables["vehicle_behavior_features"]
        summary["vehicle_behavior_features_imported"] = _insert_behavior(db, bpath, brows)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if own_session and db is not None:
            db.close()

    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Импорт CSV (генератор / совместимый набор).")
    parser.add_argument(
        "directory",
        type=Path,
        help="Каталог с CSV (в Docker обычно /app/data)",
    )
    parser.add_argument(
        "--create-demo-users",
        action="store_true",
        help="Создать User с is_admin=false для каждого vehicle (пропуск, если user уже есть)",
    )
    parser.add_argument(
        "--demo-password",
        default="password123",
        help="Пароль для demo users (хэшируется, в БД plaintext не хранится)",
    )
    parser.add_argument(
        "--force-clear",
        action="store_true",
        help="ОПАСНО: удалить все строки доменных таблиц и users перед импортом",
    )
    args = parser.parse_args(argv)

    target = args.directory
    if not target.is_dir():
        print(f"Каталог не найден: {target}", file=sys.stderr)
        sys.exit(1)

    try:
        result = run_import(
            target,
            create_demo_users=args.create_demo_users,
            demo_password=args.demo_password,
            force_clear=args.force_clear,
        )
    except Exception as exc:
        print(f"Ошибка импорта: {exc}", file=sys.stderr)
        sys.exit(2)

    print("Импорт успешно завершён.")
    print(f"  vehicles: {result['vehicles_imported']}")
    print(f"  trips: {result['trips_imported']}")
    print(f"  account_transactions: {result['account_transactions_imported']}")
    print(f"  recommendation_events: {result['recommendation_events_imported']}")
    print(f"  vehicle_behavior_features: {result['vehicle_behavior_features_imported']}")
    print(f"  users_created: {result['users_created']}")
    if result.get("users_skipped_notes"):
        for line in result["users_skipped_notes"]:
            print(f"  user note: {line}")
    print(f"  balance_sync_vehicles_updated: {result['balance_sync_vehicles_updated']}")
    if result.get("recommendation_links"):
        rl = result["recommendation_links"]
        print(f"  recommendation_links_applied: {rl.get('applied', 0)}")
        for err in rl.get("errors", []):
            print(f"  link error: {err}")
    print(f"  skipped_rows: {result['skipped_rows']}")
    for w in result.get("legacy_warnings", []):
        print(f"  warning: {w}")


if __name__ == "__main__":
    main()
