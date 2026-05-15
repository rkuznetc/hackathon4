from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.security import HTTPBearer

from app.database import Base, engine, wait_for_db
from app.routers import auth, drivers, me

bearer_scheme = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    wait_for_db()
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Toll Roads Driver Assistant",
    description="REST API для цифрового помощника водителя на платных дорогах",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(drivers.router)


@app.get("/")
def root():
    return {
        "service": "toll-roads-driver-assistant",
        "docs": "/docs",
        "status": "ok",
        "mobile_api": "/me",
        "auth": "/auth",
    }
