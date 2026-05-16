from sqlalchemy import text

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.database import engine
from app.schemas import HealthLiveResponse, HealthReadyResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthLiveResponse)
def health_live():
    return HealthLiveResponse(status="ok", service="toll-roads-backend")


@router.get("/ready")
def health_ready():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return HealthReadyResponse(status="ready", database="ok")
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "error"},
        )
