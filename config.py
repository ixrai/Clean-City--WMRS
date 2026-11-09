"""
CleanCity - application configuration.

All environment-specific values come from the `.env` file (see `.env.example`).
Nothing secret is hard-coded here.
"""
import os
import secrets
from dotenv import load_dotenv

# Base directory of the project
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Load environment variables from the .env file next to this module
load_dotenv(os.path.join(BASE_DIR, '.env'))


def _env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


# ---------------------------------------------------------------------------
# Database
#   DB_TYPE = sqlite (default, zero setup)  |  mysql  |  auto (try MySQL, else SQLite)
# ---------------------------------------------------------------------------
DB_TYPE = os.getenv("DB_TYPE", "sqlite").strip().lower()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "waste_management"),
    "port": int(os.getenv("DB_PORT", 3306)),
}

# SQLITE_DB_PATH can be overridden (the test-suite points it at a throw-away file)
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH") or os.path.join(BASE_DIR, 'database', 'waste_management.db')

# ---------------------------------------------------------------------------
# Firebase web configuration (these identifiers are public by design - they are
# shipped to every browser. Access is controlled by Firebase Auth rules/domains.)
# ---------------------------------------------------------------------------
FIREBASE_CONFIG = {
    "apiKey": os.getenv("FIREBASE_API_KEY", ""),
    "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", ""),
    "projectId": os.getenv("FIREBASE_PROJECT_ID", ""),
    "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET", ""),
    "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID", ""),
    "appId": os.getenv("FIREBASE_APP_ID", ""),
    "measurementId": os.getenv("FIREBASE_MEASUREMENT_ID", ""),
}
FIREBASE_ENABLED = bool(FIREBASE_CONFIG["apiKey"])


# ---------------------------------------------------------------------------
# Flask secret key: taken from .env; otherwise generated once and stored in
# `.secret_key` so sessions survive restarts. Never a hard-coded string.
# ---------------------------------------------------------------------------
def _load_secret_key():
    key = os.getenv("SECRET_KEY", "").strip()
    if key:
        return key
    key_file = os.path.join(BASE_DIR, '.secret_key')
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as f:
                stored = f.read().strip()
            if stored:
                return stored
        generated = secrets.token_hex(32)
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(generated)
        return generated
    except OSError:
        return secrets.token_hex(32)   # read-only filesystem: per-process key


SECRET_KEY = _load_secret_key()

# ---------------------------------------------------------------------------
# Runtime behaviour
# ---------------------------------------------------------------------------
DEBUG = _env_bool("FLASK_DEBUG", False)
# Show the one-click demo credential buttons on the login page (turn off in production)
DEMO_MODE = _env_bool("DEMO_MODE", True)
SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)  # set True behind HTTPS

# ---------------------------------------------------------------------------
# File uploads
# ---------------------------------------------------------------------------
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER") or os.path.join(BASE_DIR, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload size
