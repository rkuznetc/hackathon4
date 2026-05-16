"""Тесты offline-импорта CSV (формат generator / PK в файлах)."""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from app import import_csv, models
from app.security import verify_password


def _find_hackathon_root() -> Path | None:
    for p in Path(__file__).resolve().parents:
        if (p / "generator" / "validate.py").exists():
            return p
    return None


def _write_row_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def write_minimal_import_bundle(target: Path) -> None:
    """Один автомобиль и связанные строки, валидный набор для import_csv и validate."""
    _write_row_csv(
        target / "vehicles.csv",
        [
            "vehicle_id",
            "license_plate",
            "owner_name",
            "registered_at",
            "phone",
            "current_balance",
            "autopay_enabled",
            "has_subscription",
            "subscription_type",
            "subscription_valid_until",
            "account_status",
        ],
        [
            {
                "vehicle_id": "1",
                "license_plate": "T-TEST-1",
                "owner_name": "Tester",
                "registered_at": "2026-01-01",
                "phone": "+79009999901",
                "current_balance": "1000.00",
                "autopay_enabled": "false",
                "has_subscription": "false",
                "subscription_type": "",
                "subscription_valid_until": "",
                "account_status": "active",
            },
        ],
    )
    _write_row_csv(
        target / "trips.csv",
        [
            "trip_id",
            "vehicle_id",
            "entered_at",
            "exited_at",
            "trip_amount",
            "is_paid",
            "payment_due_at",
        ],
        [
            {
                "trip_id": "1",
                "vehicle_id": "1",
                "entered_at": "2026-02-01T10:00:00",
                "exited_at": "2026-02-01T11:00:00",
                "trip_amount": "100.00",
                "is_paid": "true",
                "payment_due_at": "2026-02-02T23:59:00",
            },
        ],
    )
    _write_row_csv(
        target / "recommendation_events.csv",
        [
            "event_id",
            "vehicle_id",
            "shown_at",
            "recommendation_type",
            "title",
            "status",
            "responded_at",
            "deep_link",
            "related_transaction_id",
        ],
        [
            {
                "event_id": "1",
                "vehicle_id": "1",
                "shown_at": "2026-02-01T12:00:00",
                "recommendation_type": "topup_balance",
                "title": "Пополните",
                "status": "shown",
                "responded_at": "",
                "deep_link": "",
                "related_transaction_id": "",
            },
        ],
    )
    _write_row_csv(
        target / "account_transactions.csv",
        [
            "transaction_id",
            "vehicle_id",
            "occurred_at",
            "operation_type",
            "direction",
            "amount",
            "balance_after",
            "trip_id",
            "recommendation_event_id",
        ],
        [
            {
                "transaction_id": "1",
                "vehicle_id": "1",
                "occurred_at": "2026-02-01T09:00:00",
                "operation_type": "topup_manual",
                "direction": "credit",
                "amount": "1000.00",
                "balance_after": "1000.00",
                "trip_id": "",
                "recommendation_event_id": "",
            },
            {
                "transaction_id": "2",
                "vehicle_id": "1",
                "occurred_at": "2026-02-01T12:00:00",
                "operation_type": "trip_charge",
                "direction": "debit",
                "amount": "100.00",
                "balance_after": "900.00",
                "trip_id": "1",
                "recommendation_event_id": "1",
            },
        ],
    )
    _write_row_csv(
        target / "vehicle_behavior_features.csv",
        [
            "vehicle_id",
            "updated_at",
            "trips_7d",
            "trips_30d",
            "avg_trip_amount",
            "avg_trip_duration_min",
            "weekend_trip_share",
            "morning_entry_share",
            "topup_count_30d",
            "avg_topup_amount",
            "debt_episodes_30d",
            "fines_count_30d",
            "days_since_registration",
            "trip_count_total",
            "segment_code",
            "segment_name",
            "segment_assigned_at",
        ],
        [
            {
                "vehicle_id": "1",
                "updated_at": "2026-02-04T00:00:00",
                "trips_7d": "1",
                "trips_30d": "1",
                "avg_trip_amount": "100.00",
                "avg_trip_duration_min": "60",
                "weekend_trip_share": "0.1",
                "morning_entry_share": "0.2",
                "topup_count_30d": "1",
                "avg_topup_amount": "1000.00",
                "debt_episodes_30d": "0",
                "fines_count_30d": "0",
                "days_since_registration": "30",
                "trip_count_total": "1",
                "segment_code": "new_user",
                "segment_name": "Новичок",
                "segment_assigned_at": "2026-02-01T00:00:00",
            },
        ],
    )


def test_import_csv_generated_format_minimal_fixture(db_session):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_minimal_import_bundle(root)
        summary = import_csv.run_import(
            root,
            db=db_session,
            create_demo_users=False,
        )
        assert summary["vehicles_imported"] == 1
        assert summary["trips_imported"] == 1
        assert summary["recommendation_events_imported"] == 1
        assert summary["account_transactions_imported"] == 2
        assert summary["vehicle_behavior_features_imported"] == 1
        assert db_session.query(models.Vehicle).count() == 1
        assert db_session.query(models.Trip).count() == 1


def test_import_creates_demo_users(db_session):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_minimal_import_bundle(root)
        import_csv.run_import(
            root,
            db=db_session,
            create_demo_users=True,
            demo_password="secret123",
        )
        u = db_session.query(models.User).filter_by(vehicle_id=1).first()
        assert u is not None
        assert u.is_admin is False
        assert verify_password("secret123", u.password_hash) is True


def test_import_rejects_missing_required_file(db_session):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_minimal_import_bundle(root)
        (root / "trips.csv").unlink()
        with pytest.raises(FileNotFoundError):
            import_csv.run_import(root, db=db_session)


def test_import_rejects_missing_required_column(db_session):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_minimal_import_bundle(root)
        bad = root / "vehicles.csv"
        with bad.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = [h for h in (reader.fieldnames or []) if h != "vehicle_id"]
            rows = [{k: row[k] for k in fieldnames} for row in reader]
        with bad.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        with pytest.raises(ValueError, match="обязательные колонки"):
            import_csv.run_import(root, db=db_session)


def test_import_rejects_duplicate_pk(db_session):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_minimal_import_bundle(root)
        # дублируем trip_id через вторую строку
        p = root / "trips.csv"
        with p.open(encoding="utf-8", newline="") as f:
            reader = list(csv.DictReader(f))
        reader.append(dict(reader[0]))
        reader[-1]["entered_at"] = "2026-02-02T10:00:00"
        reader[-1]["exited_at"] = "2026-02-02T11:00:00"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=reader[0].keys())
            w.writeheader()
            w.writerows(reader)
        with pytest.raises(ValueError, match="дубликат"):
            import_csv.run_import(root, db=db_session)


def test_import_rejects_invalid_fk(db_session):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_minimal_import_bundle(root)
        p = root / "trips.csv"
        with p.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        rows[0]["vehicle_id"] = "999"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        with pytest.raises(ValueError, match="999"):
            import_csv.run_import(root, db=db_session)


def test_import_two_pass_recommendation_transaction_links(db_session):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_minimal_import_bundle(root)
        _write_row_csv(
            root / "recommendation_events.csv",
            [
                "event_id",
                "vehicle_id",
                "shown_at",
                "recommendation_type",
                "title",
                "status",
                "responded_at",
                "deep_link",
                "related_transaction_id",
            ],
            [
                {
                    "event_id": "1",
                    "vehicle_id": "1",
                    "shown_at": "2026-02-01T12:00:00",
                    "recommendation_type": "topup_balance",
                    "title": "Пополните",
                    "status": "accepted",
                    "responded_at": "2026-02-01T13:00:00",
                    "deep_link": "",
                    "related_transaction_id": "2",
                },
            ],
        )
        _write_row_csv(
            root / "account_transactions.csv",
            [
                "transaction_id",
                "vehicle_id",
                "occurred_at",
                "operation_type",
                "direction",
                "amount",
                "balance_after",
                "trip_id",
                "recommendation_event_id",
            ],
            [
                {
                    "transaction_id": "1",
                    "vehicle_id": "1",
                    "occurred_at": "2026-02-01T09:00:00",
                    "operation_type": "topup_manual",
                    "direction": "credit",
                    "amount": "1000.00",
                    "balance_after": "1000.00",
                    "trip_id": "",
                    "recommendation_event_id": "",
                },
                {
                    "transaction_id": "2",
                    "vehicle_id": "1",
                    "occurred_at": "2026-02-01T13:05:00",
                    "operation_type": "topup_manual",
                    "direction": "credit",
                    "amount": "500.00",
                    "balance_after": "1500.00",
                    "trip_id": "",
                    "recommendation_event_id": "1",
                },
            ],
        )
        import_csv.run_import(root, db=db_session)
        ev = db_session.get(models.RecommendationEvent, 1)
        assert ev is not None
        assert ev.related_transaction_id == 2


def test_import_rejects_non_empty_database_without_force(db_session):
    from datetime import date
    from decimal import Decimal

    v = models.Vehicle(
        license_plate="PRE",
        owner_name="X",
        registered_at=date(2026, 1, 1),
        phone="+79000000000",
        current_balance=Decimal("0"),
        autopay_enabled=False,
        has_subscription=False,
        subscription_type=None,
        subscription_valid_until=None,
        account_status="active",
    )
    db_session.add(v)
    db_session.commit()

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_minimal_import_bundle(root)
        with pytest.raises(RuntimeError, match="уже есть данные"):
            import_csv.run_import(root, db=db_session)


def test_import_generator_validate_command(tmp_path):
    """Smoke: валидатор generator на минимальном наборе CSV (только локально с корнем репо)."""
    root = _find_hackathon_root()
    if root is None:
        pytest.skip("Каталог generator/ не смонтирован (нет в Docker-образе backend)")
    write_minimal_import_bundle(tmp_path)
    script = root / "generator" / "validate.py"
    r = subprocess.run(
        [sys.executable, str(script), "--data-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr + r.stdout


def test_demo_user_skipped_when_user_exists(db_session):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_minimal_import_bundle(root)
        import_csv.run_import(root, db=db_session, create_demo_users=True)
        c2, notes = import_csv.create_demo_users_for_vehicles(db_session, "another")
        assert c2 == 0
        assert notes
