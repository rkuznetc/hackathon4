from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.schemas import (
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthTokenResponse,
    UserSummary,
    VehicleSummary,
)
from app.security import create_access_token, get_password_hash, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(user, vehicle) -> AuthTokenResponse:
    token = create_access_token(subject=str(user.id))
    return AuthTokenResponse(
        access_token=token,
        user=UserSummary(
            id=user.id,
            phone=vehicle.phone,
            vehicle_id=vehicle.vehicle_id,
        ),
        vehicle=VehicleSummary(
            vehicle_id=vehicle.vehicle_id,
            license_plate=vehicle.license_plate,
            owner_name=vehicle.owner_name,
        ),
    )


@router.post("/register", response_model=AuthTokenResponse, status_code=201)
def register(data: AuthRegisterRequest, db: Session = Depends(get_db)):
    phone = data.phone.strip()
    plate = data.license_plate.strip()
    if crud.get_vehicle_by_phone(db, phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Телефон уже зарегистрирован",
        )
    if crud.get_vehicle_by_license_plate(db, plate):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Госномер уже зарегистрирован",
        )

    user, vehicle = crud.create_user_with_vehicle(
        db,
        phone=phone,
        password_hash=get_password_hash(data.password),
        license_plate=plate,
        owner_name=data.owner_name.strip(),
    )
    return _token_response(user, vehicle)


@router.post("/login", response_model=AuthTokenResponse)
def login(data: AuthLoginRequest, db: Session = Depends(get_db)):
    user = crud.get_user_by_phone(db, data.phone.strip())
    vehicle = crud.get_vehicle(db, user.vehicle_id) if user else None
    if (
        user is None
        or vehicle is None
        or not verify_password(data.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный телефон или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _token_response(user, vehicle)
