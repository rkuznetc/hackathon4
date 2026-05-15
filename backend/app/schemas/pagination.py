from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from app.config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT

T = TypeVar("T")


class PaginationParams(BaseModel):
    limit: int = Field(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)
    offset: int = Field(0, ge=0)


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
