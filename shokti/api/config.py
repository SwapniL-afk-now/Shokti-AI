"""API-specific configuration — JWT secret, expiry, CORS."""
import os
import warnings
from pathlib import Path


def _load_secret() -> str:
    secret = os.environ.get("JWT_SECRET_KEY")
    if secret:
        return secret
    # Try to load from .env file (simple line-based KEY=VALUE parser)
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            if key.strip() == "JWT_SECRET_KEY":
                return val.strip().strip("\"'")
    warnings.warn("JWT_SECRET not set — using insecure default. Set JWT_SECRET env var in production.")
    return "change-me-in-production-use-env-var"


# Load once at module level
JWT_SECRET = _load_secret()
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7
CORS_ORIGINS: list[str] | None = None
JWT_ALGORITHM = "HS256"