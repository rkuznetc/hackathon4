FROM python:3.12-slim

WORKDIR /app

# Устанавливаем зависимости Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения и тесты
COPY app/ ./app/
COPY tests/ ./tests/
COPY pytest.ini .

# Запускаем FastAPI через uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
