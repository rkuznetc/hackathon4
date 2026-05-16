from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.security import HTTPBearer
from sqlalchemy.exc import ProgrammingError

from app.database import Base, wait_for_db
from app.routers import auth, health, me, vehicles

bearer_scheme = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import engine as db_engine

    wait_for_db()
    try:
        Base.metadata.create_all(bind=db_engine)
    except ProgrammingError as exc:
        # Частый случай: в том же volume осталась старая схема (drivers/trips с id, а не vehicle_id/trip_id).
        detail = str(exc.orig) if getattr(exc, "orig", None) else str(exc)
        hint = (
            "Похоже, в PostgreSQL лежит схема от старой версии приложения. "
            "`create_all` не обновляет существующие таблицы. "
            "Пересоздайте данные: `docker compose down -v` и снова `docker compose up --build -d`. "
            "См. README.md (смена схемы БД)."
        )
        raise RuntimeError(f"{hint}\n\n[детали БД] {detail}") from exc
    yield


app = FastAPI(
    title="Toll Roads Driver Assistant",
    description="REST API для цифрового помощника водителя на платных дорогах",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(me.router)
app.include_router(vehicles.router)


@app.get("/")
def root():
    return {
        "service": "toll-roads-driver-assistant",
        "docs": "/docs",
        "status": "ok",
        "mobile_api": "/me",
        "auth": "/auth",
    }
