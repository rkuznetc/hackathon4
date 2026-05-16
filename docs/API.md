# API Reference (Vehicle)

Swagger: http://localhost:8000/docs

## Матрица доступа

| Область | Токен | Поведение |
|---------|-------|-----------|
| **`/auth/*`** | не нужен | Регистрация и вход |
| **`/health/*`** | не нужен | Liveness / readiness |
| **`/me/*`** | **обычный** Bearer JWT | Мобильный контракт; автомобиль из токена |
| **`/vehicles/*`** | **admin** Bearer JWT | Dev/admin API по явному `vehicle_id` |

### `/vehicles/*` и коды ответов

- Без заголовка `Authorization` или с невалидным JWT → **401 Unauthorized**.
- С валидным JWT **обычного** пользователя (`users.is_admin = false`) → **403 Forbidden**.
- С валидным JWT **администратора** (`users.is_admin = true`) → доступ разрешён (**200** и др. по смыслу ручки).

Регистрация через **`POST /auth/register`** всегда создаёт пользователя с **`is_admin = false`**. Отдельной публичной регистрации администратора нет.

### Тестовые учётные записи после `seed`

См. корневой `README.md`. Администратор для **`/vehicles/*`**: телефон **`+79001009999`**, пароль **`admin123`**.

---

## Контракты

### Мобильный клиент → `/me/*` + обычный JWT

Автомобиль определяется из токена (пользователь → `vehicle_id`). В URL передавать `vehicle_id` не нужно.

### Dev/admin → `/vehicles/{vehicle_id}/*` + admin JWT

Те же данные, что у `/me`, но для любого автомобиля по id. Нужен токен пользователя с **`is_admin = true`**.

---

## Swagger — Authorize

1. Получите токен: **`POST /auth/register`** или **`POST /auth/login`** (или учётные данные из **`seed`**).
2. Кнопка **Authorize** → в поле введите: `Bearer <access_token>` (слово `Bearer` и пробел обязательны, если UI этого требует).
3. Для **`/me/*`** используйте токен **обычного** пользователя.
4. Для **`/vehicles/*`** используйте токен **admin** (из seed: `+79001009999` / `admin123`).

Проверка **401 / 403**:

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/vehicles/1/profile
# ожидается 401

curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/vehicles/1/profile \
  -H "Authorization: Bearer <USER_TOKEN>"
# ожидается 403
```

---

## Health (без токена)

### GET /health/live

Приложение запущено.

**Ответ 200:**

```json
{
  "status": "ok",
  "service": "toll-roads-backend"
}
```

### GET /health/ready

Проверка подключения к БД.

**Ответ 200** (БД доступна):

```json
{
  "status": "ready",
  "database": "ok"
}
```

**Ответ 503** (БД недоступна):

```json
{
  "status": "not_ready",
  "database": "error"
}
```

---

## Auth

### POST /auth/register

Создаёт **Vehicle** и **User** (`is_admin = false`), возвращает JWT и краткие сведения.

**Тело:**

```json
{
  "phone": "+79991234567",
  "password": "password123",
  "license_plate": "А123ВС777",
  "owner_name": "Иван Петров"
}
```

**Ответ (пример):**

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "phone": "+79991234567",
    "vehicle_id": 1
  },
  "vehicle": {
    "vehicle_id": 1,
    "license_plate": "А123ВС777",
    "owner_name": "Иван Петров"
  }
}
```

JWT payload: `sub` = **id пользователя** (`users.id`), не `vehicle_id`.

### POST /auth/login

```json
{
  "phone": "+79991234567",
  "password": "password123"
}
```

Ошибка учётных данных — **401**.

---

## Me (обычный Bearer token)

Заголовок: `Authorization: Bearer <access_token>`. Без токена — **401**.

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/me/profile` | Профиль автомобиля |
| GET | `/me/balance` | Баланс и статус |
| GET | `/me/trips` | Поездки (пагинация, сортировка `entered_at` DESC) |
| GET | `/me/transactions` | Операции по счёту (`occurred_at` DESC) |
| GET | `/me/recommendations` | Рекомендации из БД + возможна динамическая `topup_forecast` |
| POST | `/me/recommendations/{event_id}/respond` | Принять или отклонить рекомендацию (см. ниже) |
| GET | `/me/behavior` | Витрина `vehicle_behavior_features`; нет строки → **404** |
| GET | `/me/forecast` | Прогноз расходов (MVP по средней поездке) |
| GET | `/me/stats?period=month` | Статистика: `week` \| `month` \| `all` |
| GET | **`/me/summary`** | **Агрегат для главного экрана** (профиль, баланс, прогноз, статистика, рекомендации) |
| PATCH | **`/me/autopay`** | Включить/выключить флаг **`autopay_enabled`** (без реальных платежей) |
| GET | **`/me/ml/status`** | Доступность offline-обученных моделей |
| GET | **`/me/ml/predictions`** | Прогноз расходов и риск долга (inference) |
| GET | **`/me/ml/recommendations`** | Ранжирование `shown` рекомендаций (без смены status) |
| POST | `/me/top-up` | Ручное пополнение (`topup_manual`, ответ с транзакцией) |

### GET /me/summary

Рекомендуемая точка входа для **главного экрана** мобильного приложения: один запрос вместо нескольких.

Дополнительно возвращается блок **`ml`** (опционально для UI):

```json
"ml": {
  "available": true,
  "spend_forecast_7d": "1234.56",
  "spend_forecast_30d": "4567.89",
  "debt_risk_7d": 0.27,
  "top_recommendation_event_id": 123
}
```

Если артефакты в `data/ml_final/models/` отсутствуют: `"available": false`, `"reason": "models_not_found"`. Старые поля summary не меняются.

### GET /me/ml/status

Проверка, загружены ли модели из `ML_MODELS_DIR` (по умолчанию `/app/data/ml_final/models`).

Без моделей: `available: false`, `reason: "models_not_found"`. **Не** HTTP 500.

### GET /me/ml/predictions

Inference для текущего vehicle (признаки только из прошлой истории в БД).

### GET /me/ml/recommendations

Ранжирует существующие рекомендации со статусом **`shown`** по hybrid score (acceptance prior + business value + debt risk). **Не** создаёт события и **не** меняет `status`.

Если моделей нет: `available: false`, в `items` — рекомендации в исходном порядке с `hybrid_score: null`.

Обучение: `python ml/ml_pipeline_final.py --data-dir data --out-dir data/ml_final --save-models` (offline, не HTTP).

Используются сервисы **forecast**, **stats**, **recommendation** (без дублирования сложной логики в роутере).

**Разделы ответа:**

- **`vehicle`**: `vehicle_id`, `license_plate`, `owner_name`, `account_status`, `has_subscription`, `subscription_type`, `subscription_valid_until`
- **`balance`**: `current_balance`, `autopay_enabled`
- **`forecast`**: `horizon_days`, `forecast_amount`, `average_trip_amount`, `trip_count`
- **`stats`**: `total_spent`, `average_trip_amount`, `trip_count`, `paid_trip_count`, `unpaid_trip_count`
- **`recommendations`**:
  - **`active_count`** — число записей в БД со статусом **`shown`** (без учёта динамической строки в `/me/recommendations`)
  - **`latest`** — до **3** последних таких записей (`shown`), отсортированных по времени показа

### PATCH /me/autopay

Обновляет только флаг **`vehicles.autopay_enabled`**. **Настоящих автоплатежей и списаний с карты нет** — это заготовка под будущую интеграцию.

**Тело:**

```json
{ "autopay_enabled": true }
```

**Ответ:**

```json
{
  "vehicle_id": 1,
  "autopay_enabled": true
}
```

### POST /me/recommendations/{event_id}/respond

Фиксирует реакцию пользователя на рекомендацию из БД (**без** изменения баланса и без ML).

**Тело:**

```json
{ "status": "accepted" }
```

или

```json
{ "status": "dismissed" }
```

Допустимы только **`accepted`** и **`dismissed`**. Значения **`shown`** и **`expired`** через эту ручку ставить **нельзя** (ошибка валидации **422** или **400** — см. OpenAPI).

**Поведение:**

1. Ищется **`RecommendationEvent`** по **`event_id`**.
2. Если запись не найдена **или** принадлежит **другому** автомобилю, чем в JWT → **404** (не раскрываем факт существования чужого `event_id`).
3. Переход разрешён **только из статуса `shown`**. Если текущий статус уже **`accepted`**, **`dismissed`** или **`expired`** → **400** с сообщением вроде «recommendation already responded».
4. Обновляются **`status`** и **`responded_at`** (текущее время UTC в хранилище).
5. Возвращается обновлённое событие (формат как у элементов списка рекомендаций).

**Метрики в будущем:** доли **`accepted`** / **`dismissed`** по типам рекомендаций можно строить по полям **`status`** и **`responded_at`** в `recommendation_events`.

### POST /me/top-up

```json
{ "amount": "1000.00" }
```

Ответ: `vehicle_id`, `current_balance`, `account_status`, `transaction` (объект операции).

---

## Vehicles (admin Bearer JWT)

Все перечисленные ручки требуют **admin** токен.

| Метод | Путь |
|-------|------|
| POST | `/vehicles` |
| DELETE | `/vehicles/{vehicle_id}` |
| GET | `/vehicles/{vehicle_id}/profile` |
| GET | `/vehicles/{vehicle_id}/balance` |
| GET | `/vehicles/{vehicle_id}/trips` |
| POST | `/vehicles/{vehicle_id}/trips` |
| GET | `/vehicles/{vehicle_id}/transactions` |
| POST | `/vehicles/{vehicle_id}/top-up` |
| GET | `/vehicles/{vehicle_id}/recommendations` |
| GET | `/vehicles/{vehicle_id}/behavior` |
| GET | `/vehicles/{vehicle_id}/forecast` |
| GET | `/vehicles/{vehicle_id}/stats` |

### POST /vehicles/{vehicle_id}/trips

```json
{
  "entered_at": "2026-01-10T08:00:00",
  "exited_at": "2026-01-10T09:30:00",
  "trip_amount": "250.00",
  "is_paid": true,
  "payment_due_at": "2026-01-11T23:59:00"
}
```

Создаётся `Trip` и операция `trip_charge` (дебет), баланс уменьшается. Недостаток средств **не даёт 400**: баланс может уйти в минус (режим долга); при `balance < 0` или `is_paid: false` выставляется `account_status = debt`.

---

## Пагинация

Query: `limit` (по умолчанию **20**, 1–**100**), `offset` (≥ 0). При `limit > 100` — **422**.

Формат:

```json
{
  "items": [],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

---

## Рекомендации (общее)

- Записи из таблицы `recommendation_events` отдаются как есть в списках.
- Дополнительно в **`GET /me/recommendations`** может быть **динамическая** рекомендация типа `topup_forecast` (не хранится в БД): в ответе `event_id: 0`, `is_dynamic: true`.
- **`GET /me/summary`** считает **`active_count`** и **`latest`** только по **сохранённым** событиям со статусом **`shown`**.

---

## Примеры curl

Обычный пользователь (после register/login):

```bash
curl http://localhost:8000/me/profile \
  -H "Authorization: Bearer <USER_TOKEN>"

curl http://localhost:8000/me/summary \
  -H "Authorization: Bearer <USER_TOKEN>"

curl -X PATCH http://localhost:8000/me/autopay \
  -H "Authorization: Bearer <USER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"autopay_enabled":true}'

curl -X POST http://localhost:8000/me/recommendations/1/respond \
  -H "Authorization: Bearer <USER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"status":"accepted"}'
```

Админ (после seed):

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79001009999","password":"admin123"}'

curl http://localhost:8000/vehicles/1/profile \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

Health:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

---

## Загрузка данных (не HTTP)

Заполнение БД — CLI **`python -m app.import_csv <каталог>`** (в Docker: каталог **`/app/data`**). REST endpoints для импорта или обучения **нет**.

Флаги CLI (основные):

| Флаг | Назначение |
|------|------------|
| `--create-demo-users` | Создать `users` с паролем для каждого `vehicles.phone` |
| `--demo-password <str>` | Пароль demo-пользователей |
| `--force-clear` | Очистить core-таблицы перед импортом (опасно) |

Порядок и циклические FK: см. [DATABASE.md](DATABASE.md#импорт-csv-cli). Сценарий запуска — корневой [README.md](../README.md).

---

## Устаревшие эндпоинты

Ручки `/drivers/...` **удалены**; используйте **`/vehicles/...`** (с admin JWT).

---

## Что запланировано на потом

- Push-уведомления, платёжный шлюз, реальный автоплатёж, Alembic-миграции, расширенный тарификатор.
