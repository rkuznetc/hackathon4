from app import crud
from app.schemas import TripCreate
from app.services.forecast_service import calculate_driver_forecast
from app.services.notification_service import get_driver_notifications


def test_register_creates_user_and_driver(client):
    response = client.post(
        "/auth/register",
        json={
            "email": "new@example.com",
            "password": "password123",
            "name": "New Driver",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["user"]["email"] == "new@example.com"
    assert data["user"]["driver_id"] > 0


def test_login_returns_token(client):
    client.post(
        "/auth/register",
        json={
            "email": "login@example.com",
            "password": "password123",
            "name": "Login User",
        },
    )
    response = client.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "password123"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_me_profile_without_token_returns_401(client):
    response = client.get("/me/profile")
    assert response.status_code == 401


def test_me_profile_with_token_returns_profile(client, auth_headers):
    response = client.get("/me/profile", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test User"
    assert "balance" in data


def test_top_up_creates_transaction(client, auth_headers):
    response = client.post(
        "/me/top-up",
        json={"amount": 1000},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["balance"] == 1000

    tx_response = client.get("/me/transactions", headers=auth_headers)
    assert tx_response.status_code == 200
    items = tx_response.json()["items"]
    assert len(items) == 1
    assert items[0]["type"] == "top_up"
    assert items[0]["amount"] == 1000


def test_transactions_pagination(client, auth_headers):
    for amount in [100, 200, 300]:
        client.post("/me/top-up", json={"amount": amount}, headers=auth_headers)

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
    client.post("/me/top-up", json={"amount": 10000}, headers=auth_headers)
    profile = client.get("/me/profile", headers=auth_headers).json()
    driver_id = profile["id"]

    for i in range(3):
        crud.create_trip(
            db_session,
            driver_id,
            TripCreate(road_name=f"Road-{i}", cost=100 + i),
        )

    response = client.get("/me/trips?limit=2&offset=0", headers=auth_headers)
    data = response.json()
    assert len(data["items"]) == 2
    assert data["total"] == 3


def test_forecast_without_trips_returns_zero(client, auth_headers):
    response = client.get("/me/forecast", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["trip_count"] == 0
    assert data["forecast_30_days"] == 0
    assert data["average_trip_cost"] == 0


def test_low_balance_notification(client, auth_headers, db_session):
    client.post("/me/top-up", json={"amount": 2000}, headers=auth_headers)
    profile = client.get("/me/profile", headers=auth_headers).json()
    driver_id = profile["id"]

    for _ in range(3):
        crud.create_trip(
            db_session,
            driver_id,
            TripCreate(road_name="М-11", cost=500),
        )

    db_session.commit()
    driver = crud.get_driver(db_session, driver_id)
    assert driver.balance < calculate_driver_forecast(
        db_session, driver_id
    ).forecast_30_days

    result = get_driver_notifications(db_session, driver_id, limit=20, offset=0)
    assert any(n.title == "Низкий баланс" for n in result.items)


def test_invalid_login_returns_401(client):
    response = client.post(
        "/auth/login",
        json={"email": "nobody@example.com", "password": "wrong"},
    )
    assert response.status_code == 401
