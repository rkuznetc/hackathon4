import os
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

# DATABASE_URL приходит из docker-compose
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://hackathon:hackathon@localhost:5432/toll_roads",
)

# Подключение к PostgreSQL через SQLAlchemy
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def wait_for_db(max_attempts: int = 30, delay: float = 1.0) -> None:
    """
    Ждём, пока PostgreSQL и DNS в Docker-сети станут доступны.
    На части машин backend стартует чуть раньше, чем резолвится имя db.
    """
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            last_error = exc
            if attempt < max_attempts:
                time.sleep(delay)
    raise last_error  # type: ignore[misc]


def get_db():
    """Зависимость FastAPI: одна сессия на запрос."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
