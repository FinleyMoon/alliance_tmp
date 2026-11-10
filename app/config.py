"""Application Configuration."""
import os
import zoneinfo
from datetime import datetime, timezone as dt_timezone, timedelta
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file if present
for env_candidate in [BASE_DIR / ".env", Path("/data/.env")]:
    if env_candidate.exists():
        try:
            with open(env_candidate, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

DATA_DIR = Path(os.getenv("DATA_DIR", "/data" if os.path.exists("/data") else str(BASE_DIR / "data")))
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "alliance.db")))
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(DATA_DIR / "uploads")))
KEYS_DIR = Path(os.getenv("KEYS_DIR", str(DATA_DIR / "keys")))

# Ensure directories exist
for directory in [DATA_DIR, UPLOADS_DIR, KEYS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# Security and Sessions
SECRET_KEY = os.getenv("SECRET_KEY", "alliances-secret-cyber-key-2026-production-fixed")
SESSION_COOKIE_NAME = "alliance_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days
RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 15 * 60  # 15 minutes

# Server Timezone Configuration (Defaults to Moscow)
def get_server_timezone_name() -> str:
    """Retrieve configured timezone name from environment with Moscow default."""
    tz_val = (
        os.getenv("TIMEZONE")
        or os.getenv("APP_TIMEZONE")
        or os.getenv("SERVER_TIMEZONE")
        or os.getenv("TZ")
        or "Europe/Moscow"
    ).strip()
    mapping = {
        "moscow": "Europe/Moscow",
        "msk": "Europe/Moscow",
        "utc": "UTC",
        "gmt": "UTC",
    }
    return mapping.get(tz_val.lower(), tz_val)


def get_server_timezone():
    """Return zoneinfo.ZoneInfo object for server timezone, defaulting to Europe/Moscow."""
    name = get_server_timezone_name()
    try:
        return zoneinfo.ZoneInfo(name)
    except Exception:
        try:
            return zoneinfo.ZoneInfo("Europe/Moscow")
        except Exception:
            return dt_timezone(timedelta(hours=3), name="MSK")


def get_server_now() -> datetime:
    """Return current localized datetime in server timezone."""
    return datetime.now(get_server_timezone())


# Game Engine Defaults
DEFAULT_BOARD_WIDTH = 15
DEFAULT_BOARD_HEIGHT = 15
DEFAULT_STARTING_HP = 3
DEFAULT_STARTING_AP = 0
DEFAULT_STARTING_RANGE_LEVEL = 1
DEFAULT_BURST_CAP_AP = 3
DEFAULT_AP_DROP_CRON = "0 22 * * *"
DEFAULT_AP_DROP_TIMEZONE = get_server_timezone_name()
DEFAULT_AP_DROP_AMOUNT = 1
DEFAULT_RANGE_UPGRADE_COST_BASE = 2
DEFAULT_RANGE_UPGRADE_COST_SCALE = 1.0
DEFAULT_STAGNATION_DAYS_LIMIT = 5

# Maximum avatar upload size in bytes (2 MB)
MAX_AVATAR_SIZE_BYTES = 2 * 1024 * 1024
