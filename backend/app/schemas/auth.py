from pydantic import BaseModel, Field


class AuthRegisterRequest(BaseModel):
    phone: str = Field(min_length=5, max_length=20)
    password: str = Field(min_length=6)
    license_plate: str = Field(min_length=1, max_length=16)
    owner_name: str = Field(min_length=1, max_length=100)


class AuthLoginRequest(BaseModel):
    phone: str = Field(min_length=5, max_length=20)
    password: str


class UserSummary(BaseModel):
    id: int
    phone: str
    vehicle_id: int


class VehicleSummary(BaseModel):
    vehicle_id: int
    license_plate: str
    owner_name: str


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserSummary
    vehicle: VehicleSummary
