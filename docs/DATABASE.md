# База данных (схема Vehicle)

## СУБД и подключение

| Параметр | Значение |
|----------|----------|
| **СУБД** | PostgreSQL (prod / Docker), SQLite in-memory (pytest) |
| **ORM** | SQLAlchemy 2.x, declarative `Base` |
| **Строка подключения** | переменная окружения `DATABASE_URL` |

По умолчанию локально (`database.py`):

`postgresql://hackathon:hackathon@localhost:5432/toll_roads`

В Docker Compose сервис backend подключается к хосту `db` на порту `5432`. На хост-машине PostgreSQL проброшен на **5433** (см. `docker-compose.yml`).

## Создание схемы

Таблицы создаются при старте приложения: `Base.metadata.create_all(bind=engine)` в `lifespan` (`app/main.py`). **Alembic в проекте нет.**

При изменении моделей ORM существующий том PostgreSQL может содержать **старые таблицы** с другими колонками. В этом случае пересоздайте volume:

```bash
docker compose down -v
docker compose up --build -d
```

## Диаграмма связей

- **vehicles** 1 — N **trips**
- **vehicles** 1 — N **account_transactions**
- **vehicles** 1 — N **recommendation_events**
- **vehicles** 1 — 1 **vehicle_behavior_features**
- **vehicles** 1 — 0..1 **users** (JWT: один пользователь на автомобиль)
- **trips** 1 — 0..N **account_transactions** по FK `trip_id` (см. решение ниже)
- **recommendation_events** ↔ **account_transactions**: два nullable FK (см. ниже)

## Таблица `vehicles`

| Колонка | Тип | Описание |
|---------|-----|----------|
| vehicle_id | INTEGER | PK |
| license_plate | VARCHAR(16) | UNIQUE, NOT NULL |
| owner_name | VARCHAR(100) | NOT NULL |
| registered_at | DATE | NOT NULL |
| phone | VARCHAR(20) | UNIQUE, NOT NULL (идентификатор входа в приложение) |
| current_balance | NUMERIC(12,2) | NOT NULL |
| autopay_enabled | BOOLEAN | NOT NULL |
| has_subscription | BOOLEAN | NOT NULL |
| subscription_type | VARCHAR(30) | NULL, enum-строки в Pydantic |
| subscription_valid_until | DATE | NULL |
| account_status | VARCHAR(20) | NOT NULL: `active`, `debt`, `blocked` |

## Таблица `users` (JWT)

| Колонка | Тип | Описание |
|---------|-----|----------|
| id | INTEGER | PK, **sub** в JWT |
| password_hash | VARCHAR | NOT NULL (только hash, pwdlib + argon2) |
| is_admin | BOOLEAN | NOT NULL, default **false**; `true` — доступ к `/vehicles/*` |
| vehicle_id | INTEGER | FK → `vehicles.vehicle_id`, UNIQUE, ON DELETE CASCADE |

**Решение:** пароль хранится **только** в `users`. Телефон для входа совпадает с `vehicles.phone`; логин ищет пользователя по телефону через связанный `Vehicle`.

## Таблица `trips`

| Колонка | Тип | Описание |
|---------|-----|----------|
| trip_id | INTEGER | PK |
| vehicle_id | INTEGER | FK → vehicles, ON DELETE CASCADE |
| entered_at | TIMESTAMP | NOT NULL |
| exited_at | TIMESTAMP | NOT NULL (≥ entered_at, проверка в API/schema) |
| trip_amount | NUMERIC(10,2) | NOT NULL |
| is_paid | BOOLEAN | NOT NULL |
| payment_due_at | TIMESTAMP | NOT NULL |

## Таблица `account_transactions`

| Колонка | Тип | Описание |
|---------|-----|----------|
| transaction_id | INTEGER | PK |
| vehicle_id | INTEGER | FK → vehicles, ON DELETE CASCADE |
| occurred_at | TIMESTAMP | NOT NULL |
| operation_type | VARCHAR(30) | см. Pydantic / API |
| direction | VARCHAR(10) | `credit` \| `debit` |
| amount | NUMERIC(10,2) | NOT NULL, ≥ 0 |
| balance_after | NUMERIC(12,2) | NOT NULL |
| trip_id | INTEGER | NULL, FK → `trips.trip_id`, ON DELETE SET NULL |
| recommendation_event_id | INTEGER | NULL, FK → `recommendation_events.event_id` (отложенное создание FK, `use_alter`) |

### Поездки ↔ операции

В предметном описании указано «одна поездка — 0..1 транзакция», но типы операций (`trip_charge`, `fine_assessed`, …) допускают **несколько** списаний вокруг одной поездки. В БД **нет** уникального ограничения на `trip_id`: семантически основное списание за проезд — `trip_charge` с заполненным `trip_id`; штрафы и др. могут быть отдельными строками (с тем же или пустым `trip_id`). Это не ломает `create_all` и покрывает MVP.

## Таблица `recommendation_events`

| Колонка | Тип | Описание |
|---------|-----|----------|
| event_id | INTEGER | PK |
| vehicle_id | INTEGER | FK → vehicles, ON DELETE CASCADE |
| shown_at | TIMESTAMP | NOT NULL |
| recommendation_type | VARCHAR(30) | NOT NULL |
| title | VARCHAR(200) | NOT NULL |
| status | VARCHAR(20) | NOT NULL |
| responded_at | TIMESTAMP | NULL |
| deep_link | VARCHAR(255) | NULL |
| related_transaction_id | INTEGER | NULL, FK → `account_transactions.transaction_id` (`use_alter`) |

### Связь recommendation_events ↔ account_transactions

Реализованы **оба** nullable FK:

- `account_transactions.recommendation_event_id` → событие, породившее/связанное со списанием (если применимо).
- `recommendation_events.related_transaction_id` → транзакция, на которую ссылается рекомендация (например, покупка абонемента).

Чтобы не ломать порядок `CREATE TABLE` при цикле, в SQLAlchemy у обоих FK включён **`use_alter`** (отдельный `ALTER TABLE` в конце `create_all`).

## Таблица `vehicle_behavior_features`

Аналитическая **витрина** (агрегаты/сегмент), не источник истины — первичные события в `trips` и `account_transactions`.

| Колонка | Тип |
|---------|-----|
| vehicle_id | INTEGER PK, FK → vehicles ON DELETE CASCADE |
| updated_at | TIMESTAMP |
| trips_7d, trips_30d, … | см. `models.py` |
| segment_code, segment_name, segment_assigned_at | сегмент клиента |

## Каскады

При удалении `Vehicle` каскадом удаляются: `User`, `Trip`, `AccountTransaction`, `RecommendationEvent`, `VehicleBehaviorFeatures` (FK `ON DELETE CASCADE` + relationship cascade).

## GET /me/behavior

Если строки в `vehicle_behavior_features` нет, API возвращает **404** с текстом «ещё не рассчитаны» (зафиксировано в `docs/API.md`).

## Импорт CSV (CLI)

Модуль: `backend/app/import_csv.py`. Запуск: `python -m app.import_csv /app/data` (не HTTP).

### Ожидаемые файлы

| Файл | Таблица |
|------|---------|
| `vehicles.csv` | `vehicles` |
| `trips.csv` | `trips` |
| `account_transactions.csv` | `account_transactions` |
| `recommendation_events.csv` | `recommendation_events` |
| `vehicle_behavior_features.csv` | `vehicle_behavior_features` |

Legacy-имена `sample_*.csv` поддерживаются, если основных файлов нет.

### Порядок загрузки

1. `vehicles`  
2. (опционально) demo `users` — `--create-demo-users`  
3. `trips`  
4. `recommendation_events` — **без** `related_transaction_id` (первый проход)  
5. `account_transactions`  
6. второй проход: проставить `recommendation_events.related_transaction_id` из CSV  
7. синхронизация `vehicles.current_balance`  
8. `vehicle_behavior_features`

Цикл FK `recommendation_events.related_transaction_id` ↔ `account_transactions.recommendation_event_id` разрешён через `use_alter` в ORM и двухпроходный импорт.

### Не импортируется в PostgreSQL

- `driver_seeds.json`, `quality_report.json` (генератор / ML)  
- `data/ml_final/**` (артефакты моделей для inference, не таблицы БД)

### Ограничения

- По умолчанию импорт разрешён только в **пустую** БД (нет строк в core-таблицах).  
- Пересоздание: `docker compose down -v` или `--force-clear`.

## Тесты

`tests/conftest.py`: SQLite `:memory:`, `PRAGMA foreign_keys=ON`, подмена `app.database.engine` до импорта приложения; `lifespan` берёт engine из `app.database` в момент старта (см. `app/main.py`).
