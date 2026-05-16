"""
Тестовая БД — SQLite :memory: с FK.

Важно: до импорта `app.main` подменяем `app.database.engine`, иначе lifespan
вызовет create_all на PostgreSQL из DATABASE_URL (в Docker там может быть
старая схема без пересоздания volume).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database

SQLALCHEMY_TEST_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_TEST_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def _sqlite_pragma(dbapi_connection, connection_record):  # noqa: ARG001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


database.engine = engine
database.SessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine
)

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

TestingSessionLocal = database.SessionLocal


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client):
    response = client.post(
        "/auth/register",
        json={
            "phone": "+79991111111",
            "password": "password123",
            "license_plate": "Т901ТТ177",
            "owner_name": "Test User",
        },
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
