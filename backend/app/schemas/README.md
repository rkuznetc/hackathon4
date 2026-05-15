# Папка `backend/app/schemas/`

Pydantic-схемы: валидация JSON на входе и форма ответов API. Отделены от SQLAlchemy-моделей в `models.py`.

## Файлы

| Файл | Содержание |
|------|------------|
| `common.py` | `DriverCreate`, `DriverProfile`, `TripCreate/Read`, `TransactionRead`, `NotificationRead`, `BalanceRead`, `ForecastRead`, `StatsRead`, `TopUpCreate` |
| `auth.py` | `RegisterRequest`, `LoginRequest`, `TokenResponse`, `UserInfo` (без password_hash) |
| `pagination.py` | `PaginationParams`, generic `PaginatedResponse[T]` (`items`, `total`, `limit`, `offset`) |
| `__init__.py` | Реэкспорт схем для `from app.schemas import ...` |

## Изменения относительно скелета

- Добавлены `auth.py`, `pagination.py`, `StatsRead`, поле `horizon_days` в `ForecastRead`
- Старый монолитный `schemas.py` заменён этим пакетом

## Пагинация

`limit`: 1–100 (default 20), `offset` ≥ 0 — валидируется в Query на уровне роутеров; схема ответа — `PaginatedResponse`.
