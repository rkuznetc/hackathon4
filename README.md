# Toll Roads — Driver Assistant

REST API цифрового помощника водителя на платных дорогах: баланс, поездки, рекомендации, прогнозы и ML-inference.

| Документ | Назначение |
|----------|------------|
| [docs/API.md](docs/API.md) | Контракт HTTP: auth, `/me`, `/vehicles`, ML, коды ответов |
| [docs/DATABASE.md](docs/DATABASE.md) | Схема PostgreSQL, связи, импорт CSV |

Интерактивная спецификация: http://localhost:8000/docs

## Стек

Python · FastAPI · SQLAlchemy 2 · PostgreSQL · JWT (PyJWT) · Docker Compose · scikit-learn (offline training + inference)

## Быстрый старт (Docker)

Предполагается, что в репозитории уже лежат **обученные модели** в `data/ml_final/models/` (см. раздел ML). Тогда обучение можно пропустить.

```bash
# 1. Поднять API и БД
docker compose up --build -d

# 2. Сгенерировать CSV (на хосте, из корня репозитория)
python generator/main.py --output-dir data
python generator/validate.py --data-dir data

# 3. Пустая БД + импорт (импорт только в пустую БД)
docker compose down -v
docker compose up --build -d
docker compose exec backend python -m app.import_csv /app/data \
  --create-demo-users \
  --demo-password password123

# 4. Проверка
curl http://localhost:8000/health/ready
```

- API: http://localhost:8000  
- Swagger: http://localhost:8000/docs  
- PostgreSQL с хоста: `localhost:5433`, пользователь/БД: `hackathon` / `toll_roads`, пароль: `hackathon`

Каталог `data/` монтируется в контейнер read-only как `/app/data` (в т.ч. модели: `/app/data/ml_final/models`).

### Вход после импорта

Логин — колонка `phone` из `data/vehicles.csv`, пароль — значение `--demo-password` (в примере выше: `password123`).

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"phone":"<PHONE_FROM_CSV>","password":"password123"}'
```

Проверка ML (нужны артефакты в `data/ml_final/models/`):

```bash
curl http://localhost:8000/me/ml/status -H "Authorization: Bearer <TOKEN>"
curl http://localhost:8000/me/summary -H "Authorization: Bearer <TOKEN>"
```

### Альтернатива: маленькая демо-БД без генератора

```bash
docker compose up --build -d
docker compose exec backend python -m app.seed
```

После `seed` — демо-пользователи с паролем `password123` и админ `+79001009999` / `admin123` (см. [docs/API.md](docs/API.md)).

## Смена схемы БД

Alembic **нет**; таблицы создаются через `create_all` при старте. После изменения ORM-моделей пересоздайте volume:

```bash
docker compose down -v
docker compose up --build -d
```

Симптомы несовместимой схемы: backend в `Restarting`, в логах ошибки FK/колонок, не открывается `/docs`.

## ML (опционально)

В репозитории закоммичены артефакты для runtime:

- `data/ml_final/models/*.joblib`
- `feature_columns.json`, `recommendation_priors.json`, `model_metadata.json`

Backend читает их из `ML_MODELS_DIR` (в Docker: `/app/data/ml_final/models`). Если каталог пуст — `/me/ml/*` отвечает `available: false`, приложение **не** падает.

### Путь без обучения (рекомендуется для проверки)

1. `docker compose up`  
2. Сгенерировать CSV → импорт (как в «Быстром старте»)  
3. Модели уже на диске — шаг обучения **не нужен**

### Путь с переобучением

Требуются CSV в `data/` (после генератора). Обучение **не** входит в Docker-образ и **не** имеет HTTP API.

```bash
python3 -m venv .venv-ml
source .venv-ml/bin/activate
pip install -r ml/requirements.txt

python ml/ml_pipeline_final.py --data-dir data --out-dir data/ml_final --save-models
```

Отчёты и метрики пишутся в `data/ml_final/`; в PostgreSQL **не** импортируются `driver_seeds.json`, `quality_report.json`, CSV из `ml_final/`.

После переобучения перезапустите backend (достаточно `docker compose restart backend`), если контейнер уже был запущен.

## Генератор данных

Офлайн-скрипты в `generator/` (не часть FastAPI):

| Команда | Действие |
|---------|----------|
| `python generator/main.py --output-dir data` | CSV + `driver_seeds.json`, `quality_report.json` |
| `python generator/validate.py --data-dir data` | Проверка целостности CSV |

Файлы для импорта: `vehicles.csv`, `trips.csv`, `account_transactions.csv`, `recommendation_events.csv`, `vehicle_behavior_features.csv`.  
Также поддерживаются legacy-имена `sample_*.csv` (см. `backend/app/import_csv.py`).

## Импорт CSV

Только CLI внутри контейнера, не REST:

```bash
docker compose exec backend python -m app.import_csv /app/data \
  --create-demo-users \
  --demo-password password123
```

- По умолчанию — **только пустая** БД (нет строк в core-таблицах).  
- Иначе: `docker compose down -v` или опасный `--force-clear`.

Порядок загрузки и цикл FK: [docs/DATABASE.md](docs/DATABASE.md#импорт-csv-cli).

## Тесты

```bash
docker compose run --rm --no-deps backend pytest -q
```

Локально (из `backend/`): venv + `pip install -r requirements.txt` + `pytest`.

## Переменные окружения

| Переменная | По умолчанию (Docker) |
|------------|------------------------|
| `DATABASE_URL` | `postgresql://hackathon:hackathon@db:5432/toll_roads` |
| `SECRET_KEY` | `dev-secret-change-in-production` |
| `JWT_ALGORITHM` | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` |
| `ML_MODELS_DIR` | `/app/data/ml_final/models` |

## Остановка

```bash
docker compose down
docker compose down -v   # удалить данные PostgreSQL
```

## Структура репозитория (кратко)

```
backend/app/     # FastAPI, ORM, import_csv, ML inference
generator/       # синтетические CSV (offline)
ml/              # ml_pipeline_final.py (offline training)
data/            # CSV, ml_final/models/ (монтируется в Docker)
docs/            # API.md, DATABASE.md
```
