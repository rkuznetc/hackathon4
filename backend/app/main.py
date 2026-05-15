from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.database import Base, engine, get_db, wait_for_db

# при старте ждём БД и создаём таблицы
@asynccontextmanager
async def lifespan(app: FastAPI):
    wait_for_db()
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Toll Roads Driver Assistant",
    description="Минимальный REST API для цифрового помощника водителя",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
def root():
    return {
        "service": "toll-roads-driver-assistant",
        "docs": "/docs",
        "status": "ok",
    }


@app.post("/drivers", response_model=schemas.DriverProfile, status_code=201)
def create_driver(data: schemas.DriverCreate, db: Session = Depends(get_db)):
    return crud.create_driver(db, data)


@app.get("/drivers/{driver_id}/profile", response_model=schemas.DriverProfile)
def get_profile(driver_id: int, db: Session = Depends(get_db)):
    driver = crud.get_driver(db, driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Водитель не найден")
    return driver


@app.get("/drivers/{driver_id}/balance", response_model=schemas.BalanceRead)
def get_balance(driver_id: int, db: Session = Depends(get_db)):
    driver = crud.get_driver(db, driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Водитель не найден")
    return schemas.BalanceRead(driver_id=driver.id, balance=driver.balance)


@app.get("/drivers/{driver_id}/trips", response_model=list[schemas.TripRead])
def list_trips(driver_id: int, db: Session = Depends(get_db)):
    if not crud.get_driver(db, driver_id):
        raise HTTPException(status_code=404, detail="Водитель не найден")
    return crud.get_trips(db, driver_id)


@app.post("/drivers/{driver_id}/trips", response_model=schemas.TripRead, status_code=201)
def add_trip(
    driver_id: int, data: schemas.TripCreate, db: Session = Depends(get_db)
):
    driver = crud.get_driver(db, driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Водитель не найден")
    if driver.balance < data.cost:
        raise HTTPException(status_code=400, detail="Недостаточно средств на балансе")
    return crud.create_trip(db, driver_id, data)


@app.post("/drivers/{driver_id}/top-up", response_model=schemas.BalanceRead)
def top_up(
    driver_id: int, data: schemas.TopUpCreate, db: Session = Depends(get_db)
):
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма пополнения должна быть > 0")
    driver = crud.get_driver(db, driver_id)
    if not driver:
        raise HTTPException(status_code=404, detail="Водитель не найден")
    updated = crud.top_up_balance(db, driver_id, data)
    return schemas.BalanceRead(driver_id=updated.id, balance=updated.balance)


@app.get("/drivers/{driver_id}/forecast", response_model=schemas.ForecastRead)
def get_forecast(driver_id: int, db: Session = Depends(get_db)):
    if not crud.get_driver(db, driver_id):
        raise HTTPException(status_code=404, detail="Водитель не найден")
    return crud.get_forecast(db, driver_id)


@app.get(
    "/drivers/{driver_id}/notifications",
    response_model=list[schemas.NotificationRead],
)
def list_notifications(driver_id: int, db: Session = Depends(get_db)):
    if not crud.get_driver(db, driver_id):
        raise HTTPException(status_code=404, detail="Водитель не найден")
    return crud.get_notifications(db, driver_id)
