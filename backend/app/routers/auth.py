from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserInfo
from app.security import create_access_token, get_password_hash, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(user) -> TokenResponse:
    token = create_access_token(subject=str(user.id))
    return TokenResponse(
        access_token=token,
        user=UserInfo(id=user.id, email=user.email, driver_id=user.driver_id),
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    if crud.get_user_by_email(db, data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email уже зарегистрирован",
        )

    user = crud.create_user_with_driver(
        db,
        email=data.email,
        password_hash=get_password_hash(data.password),
        name=data.name,
    )
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = crud.get_user_by_email(db, data.email)
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _token_response(user)
