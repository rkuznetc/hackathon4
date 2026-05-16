"""
Офлайн-проверка CSV после генератора (без импорта в БД).

Запуск из корня репозитория:
  python generator/validate.py --data-dir data

Из папки generator:
  python validate.py --data-dir ../data
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Ожидаемые имена файлов (как у backend import_csv / generator output)
EXPECTED = [
    "vehicles.csv",
    "trips.csv",
    "account_transactions.csv",
    "recommendation_events.csv",
    "vehicle_behavior_features.csv",
]


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return [], []
        fields = [str(h) for h in reader.fieldnames if h is not None]
        rows = list(reader)
    return fields, rows


def validate_data_dir(data_dir: Path) -> list[str]:
    """Возвращает список ошибок; пустой список = OK."""
    errors: list[str] = []
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        return [f"не каталог: {data_dir}"]

    for name in EXPECTED:
        p = data_dir / name
        if not p.exists():
            errors.append(f"нет файла {name}")

    if errors:
        return errors

    v_path = data_dir / "vehicles.csv"
    v_fields, v_rows = _read_csv(v_path)
    need_v = {
        "vehicle_id",
        "license_plate",
        "owner_name",
        "registered_at",
        "phone",
        "current_balance",
        "autopay_enabled",
        "has_subscription",
        "account_status",
    }
    if not need_v.issubset(set(v_fields)):
        errors.append(f"vehicles.csv: не хватает колонок из {sorted(need_v)}")
    elif not v_rows:
        errors.append("vehicles.csv: нет строк данных")
    else:
        ids = [int(r["vehicle_id"]) for r in v_rows]
        dup = [i for i, c in Counter(ids).items() if c > 1]
        if dup:
            errors.append(f"vehicles.csv: дубликаты vehicle_id: {dup[:5]}")

    if errors:
        return errors

    vid_from_vehicles = {int(r["vehicle_id"]) for r in v_rows}

    t_path = data_dir / "trips.csv"
    t_fields, t_rows = _read_csv(t_path)
    need_t = {
        "trip_id",
        "vehicle_id",
        "entered_at",
        "exited_at",
        "trip_amount",
        "is_paid",
        "payment_due_at",
    }
    if not need_t.issubset(set(t_fields)):
        errors.append(f"trips.csv: не хватает колонок из {sorted(need_t)}")
    if t_rows:
        for i, row in enumerate(t_rows, start=2):
            try:
                vid = int(row["vehicle_id"])
                if vid not in vid_from_vehicles:
                    errors.append(
                        f"trips.csv:{i}: vehicle_id {vid} отсутствует в vehicles.csv"
                    )
                    break
            except (KeyError, ValueError):
                errors.append(f"trips.csv:{i}: неверная строка")
                break

    tx_path = data_dir / "account_transactions.csv"
    tx_fields, tx_rows = _read_csv(tx_path)
    need_tx = {
        "transaction_id",
        "vehicle_id",
        "occurred_at",
        "operation_type",
        "direction",
        "amount",
        "balance_after",
    }
    if not need_tx.issubset(set(tx_fields)):
        errors.append(
            f"account_transactions.csv: не хватает колонок из {sorted(need_tx)}"
        )

    r_path = data_dir / "recommendation_events.csv"
    r_fields, r_rows = _read_csv(r_path)
    need_r = {
        "event_id",
        "vehicle_id",
        "shown_at",
        "recommendation_type",
        "title",
        "status",
    }
    if not need_r.issubset(set(r_fields)):
        errors.append(
            f"recommendation_events.csv: не хватает колонок из {sorted(need_r)}"
        )

    f_path = data_dir / "vehicle_behavior_features.csv"
    f_fields, f_rows = _read_csv(f_path)
    need_f = {
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
    if not need_f.issubset(set(f_fields)):
        errors.append(
            f"vehicle_behavior_features.csv: не хватает колонок из {sorted(need_f)}"
        )

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка CSV генератора.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Каталог с теми же CSV, что пишет generator (по умолчанию data от cwd)",
    )
    args = parser.parse_args()
    d = args.data_dir
    if not d.is_absolute():
        d = (Path.cwd() / d).resolve()
    errs = validate_data_dir(d)
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        sys.exit(1)
    _, v_rows = _read_csv(d / "vehicles.csv")
    _, t_rows = _read_csv(d / "trips.csv")
    _, tx_rows = _read_csv(d / "account_transactions.csv")
    _, r_rows = _read_csv(d / "recommendation_events.csv")
    _, f_rows = _read_csv(d / "vehicle_behavior_features.csv")
    seeds = d / "driver_seeds.json"
    report = d / "quality_report.json"
    print("OK")
    print(f"  vehicles: {len(v_rows)}")
    print(f"  trips: {len(t_rows)}")
    print(f"  account_transactions: {len(tx_rows)}")
    print(f"  recommendation_events: {len(r_rows)}")
    print(f"  vehicle_behavior_features: {len(f_rows)}")
    print(f"  driver_seeds.json: {'да' if seeds.exists() else 'нет'}")
    print(f"  quality_report.json: {'да' if report.exists() else 'нет'}")


if __name__ == "__main__":
    main()
