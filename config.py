import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/vidyadesk_erp")
    MONGO_CONNECT = False
    MONGO_MAX_POOL_SIZE = int(os.getenv("MONGO_MAX_POOL_SIZE", "10"))
    MONGO_MIN_POOL_SIZE = int(os.getenv("MONGO_MIN_POOL_SIZE", "0"))
    MONGO_SERVER_SELECTION_TIMEOUT_MS = int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000"))
    DEFAULT_ACADEMIC_YEAR = os.getenv("DEFAULT_ACADEMIC_YEAR", "2026-27")
    DEFAULT_SCHOOL_ID = os.getenv("DEFAULT_SCHOOL_ID", "SCH001")
    UPLOAD_FOLDER = os.path.join(os.getcwd(), "static", "uploads")
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
    SEND_FILE_MAX_AGE_DEFAULT = 31536000
    TEMPLATES_AUTO_RELOAD = False
    JSON_SORT_KEYS = False
    COMPRESS_MIMETYPES = [
        "text/html",
        "text/css",
        "text/javascript",
        "application/javascript",
        "application/json",
        "image/svg+xml",
    ]
    COMPRESS_LEVEL = int(os.getenv("COMPRESS_LEVEL", "6"))
    MAIL_WORKER_URL = os.getenv("MAIL_WORKER_URL", "").rstrip("/")
    MAIL_API_KEY = os.getenv("MAIL_API_KEY", "")
    MAIL_SEND_DELAY_SECONDS = float(os.getenv("MAIL_SEND_DELAY_SECONDS", "0.2"))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "1" if os.getenv("VERCEL") else "0") == "1"
