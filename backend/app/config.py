import os
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

if Path("/app/data").is_dir():
    _default_ml_models = Path("/app/data/ml_final/models")
else:
    _default_ml_models = _APP_DIR.parents[2] / "data" / "ml_final" / "models"

ML_MODELS_DIR = os.getenv("ML_MODELS_DIR", str(_default_ml_models))
