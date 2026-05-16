from datetime import datetime
from decimal import Decimal

from app import crud, models
from app.schemas import TripCreate


def test_register_creates_user_and_vehicle(client):
    response = client.post(
        "/auth/register",
        json={
            "phone": "+79992222222",
            "password": "password123",
            "license_plate": "А123ВС777",
            "owner_name": "Новый Пользователь",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["user"]["phone"] == "+79992222222"
    assert data["user"]["vehicle_id"] > 0
    assert data["vehicle"]["license_plate"] == "А123ВС777"


def test_login_returns_token(client):
    client.post(
        "/auth/register",
        json={
            "phone": "+79993333333",
            "password": "password123",
            "license_plate": "К777КК199",
            "owner_name": "Login User",
        },
    )
    response = client.post(
        "/auth/login",
        json={"phone": "+79993333333", "password": "password123"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_me_profile_without_token_returns_401(client):
    response = client.get("/me/profile")
    assert response.status_code == 401


def test_me_profile_with_token_returns_vehicle_profile(client, auth_headers):
    response = client.get("/me/profile", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["owner_name"] == "Test User"
    assert "current_balance" in data
    assert "vehicle_id" in data


def test_me_balance_returns_current_vehicle_balance(client, auth_headers):
    response = client.get("/me/balance", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "vehicle_id" in data
    assert data["current_balance"] == "0.00"
    assert data["account_status"] == "active"


def test_top_up_creates_account_transaction(client, auth_headers):
    response = client.post(
        "/me/top-up",
        json={"amount": "1000.00"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["current_balance"] == "1000.00"
    assert body["transaction"]["operation_type"] == "topup_manual"

    tx_response = client.get("/me/transactions", headers=auth_headers)
    assert tx_response.status_code == 200
    items = tx_response.json()["items"]
    assert len(items) == 1
    assert items[0]["operation_type"] == "topup_manual"
    assert items[0]["direction"] == "credit"


def test_create_trip_creates_trip_and_trip_charge_transaction(client, auth_headers):
    client.post(
        "/me/top-up",
        json={"amount": "5000.00"},
        headers=auth_headers,
    )
    prof = client.get("/me/profile", headers=auth_headers).json()
    vid = prof["vehicle_id"]

    trip_resp = client.post(
        f"/vehicles/{vid}/trips",
        json={
            "entered_at": "2026-03-01T08:00:00",
            "exited_at": "2026-03-01T09:00:00",
            "trip_amount": "250.00",
            "is_paid": True,
            "payment_due_at": "2026-03-02T23:59:00",
        },
    )
    assert trip_resp.status_code == 201
    assert trip_resp.json()["trip_amount"] == "250.00"

    txs = client.get("/me/transactions", headers=auth_headers).json()["items"]
    charge = next(t for t in txs if t["operation_type"] == "trip_charge")
    assert charge["amount"] == "250.00"
    assert charge["trip_id"] == trip_resp.json()["trip_id"]

    bal = client.get("/me/balance", headers=auth_headers).json()
    assert bal["current_balance"] == "4750.00"


def test_transactions_pagination(client, auth_headers):
    for amt in ["100.00", "200.00", "300.00"]:
        client.post("/me/top-up", json={"amount": amt}, headers=auth_headers)

    response = client.get(
        "/me/transactions?limit=2&offset=0",
        headers=auth_headers,
    )
    data = response.json()
    assert data["limit"] == 2
    assert data["offset"] == 0
    assert len(data["items"]) == 2
    assert data["total"] == 3


def test_trips_pagination(client, auth_headers, db_session):
    client.post("/me/top-up", json={"amount": "10000.00"}, headers=auth_headers)
    profile = client.get("/me/profile", headers=auth_headers).json()
    vehicle_id = profile["vehicle_id"]

    for i in range(3):
        crud.create_trip_for_vehicle(
            db_session,
            vehicle_id,
            TripCreate(
                entered_at=datetime(2026, 4, i + 1, 8, 0, 0),
                exited_at=datetime(2026, 4, i + 1, 9, 0, 0),
                trip_amount=Decimal("100.00") + Decimal(i),
                is_paid=True,
                payment_due_at=datetime(2026, 4, i + 2, 12, 0, 0),
            ),
        )

    response = client.get("/me/trips?limit=2&offset=0", headers=auth_headers)
    data = response.json()
    assert len(data["items"]) == 2
    assert data["total"] == 3


def test_recommendations_pagination(client, auth_headers, db_session):
    prof = client.get("/me/profile", headers=auth_headers).json()
    vid = prof["vehicle_id"]
    db_session.add_all(
        [
            models.RecommendationEvent(
                vehicle_id=vid,
                shown_at=datetime(2026, 5, 3, 10, 0, 0),
                recommendation_type="buy_subscription",
                title="Рекомендация A",
                status="shown",
                deep_link=None,
            ),
            models.RecommendationEvent(
                vehicle_id=vid,
                shown_at=datetime(2026, 5, 2, 10, 0, 0),
                recommendation_type="enable_autopay",
                title="Рекомендация B",
                status="shown",
                deep_link=None,
            ),
        ]
    )
    db_session.commit()

    r = client.get(
        "/me/recommendations?limit=1&offset=0", headers=auth_headers
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 2
    assert len(data["items"]) == 1


def test_forecast_without_trips_returns_zero(client, auth_headers):
    response = client.get("/me/forecast", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["trip_count"] == 0
    assert data["forecast_amount"] == "0.00"
    assert data["average_trip_amount"] == "0.00"


def test_vehicle_behavior_features_read(client, auth_headers, db_session):
    prof = client.get("/me/profile", headers=auth_headers).json()
    vid = prof["vehicle_id"]
    db_session.add(
        models.VehicleBehaviorFeatures(
            vehicle_id=vid,
            updated_at=datetime(2026, 6, 1, 0, 0, 0),
            trips_7d=1,
            trips_30d=2,
            avg_trip_amount=Decimal("100.00"),
            avg_trip_duration_min=45,
            weekend_trip_share=Decimal("0.1000"),
            morning_entry_share=Decimal("0.2000"),
            topup_count_30d=1,
            avg_topup_amount=Decimal("500.00"),
            debt_episodes_30d=0,
            fines_count_30d=0,
            days_since_registration=10,
            trip_count_total=2,
            segment_code="commuter",
            segment_name="Коммьютинг",
            segment_assigned_at=datetime(2026, 6, 1, 0, 0, 0),
        )
    )
    db_session.commit()

    r = client.get("/me/behavior", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["segment_code"] == "commuter"


def test_delete_vehicle_cascades_related_data(client, db_session):
    reg = client.post(
        "/auth/register",
        json={
            "phone": "+79994444444",
            "password": "password123",
            "license_plate": "У999УУ77",
            "owner_name": "Delete Me",
        },
    )
    assert reg.status_code == 201
    vid = reg.json()["vehicle"]["vehicle_id"]

    client.post(
        f"/vehicles/{vid}/trips",
        json={
            "entered_at": "2026-07-01T08:00:00",
            "exited_at": "2026-07-01T09:00:00",
            "trip_amount": "50.00",
            "is_paid": False,
            "payment_due_at": "2026-07-02T23:59:00",
        },
    )

    del_r = client.delete(f"/vehicles/{vid}")
    assert del_r.status_code == 204

    assert db_session.query(models.User).count() == 0
    assert db_session.query(models.Vehicle).count() == 0
    assert db_session.query(models.Trip).count() == 0


def test_invalid_trip_exited_before_entered_returns_422_or_400(
    client, auth_headers
):
    prof = client.get("/me/profile", headers=auth_headers).json()
    vid = prof["vehicle_id"]
    response = client.post(
        f"/vehicles/{vid}/trips",
        json={
            "entered_at": "2026-08-01T10:00:00",
            "exited_at": "2026-08-01T09:00:00",
            "trip_amount": "100.00",
            "is_paid": True,
            "payment_due_at": "2026-08-02T12:00:00",
        },
    )
    assert response.status_code == 422


def test_invalid_login_returns_401(client):
    response = client.post(
        "/auth/login",
        json={"phone": "+79995555555", "password": "wrong"},
    )
    assert response.status_code == 401
