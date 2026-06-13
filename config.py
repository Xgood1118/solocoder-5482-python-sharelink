import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    PORT = int(os.getenv("PORT", "5000"))
    FILE_STORAGE_PATH = os.getenv("FILE_STORAGE_PATH", os.path.join(os.getcwd(), "files"))
    DATA_PATH = os.getenv("DATA_PATH", os.path.join(os.getcwd(), "data"))
    METADATA_SNAPSHOT_FILE = os.path.join(DATA_PATH, "shares_snapshot.json")
    ACCESS_LOGS_FILE = os.path.join(DATA_PATH, "access_logs.json")
    STATS_DATA_FILE = os.path.join(DATA_PATH, "stats_data.json")

    NANOID_LENGTH = 10
    BCRYPT_ROUNDS = 12

    BRUTE_FORCE_MAX_ATTEMPTS = 20
    BRUTE_FORCE_WINDOW_SECONDS = 60
    BRUTE_FORCE_BAN_SECONDS = 300

    SNAPSHOT_INTERVAL_MINUTES = 5
    STATS_RETENTION_DAYS = 90

    DEFAULT_MAX_CONCURRENT_PER_IP = 1
    DEFAULT_DOWNLOAD_SPEED_KBPS = 5120

    ALLOWED_OFFICE_EXTENSIONS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"}
    ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
    ALLOWED_PDF_EXTENSIONS = {".pdf"}

    WATERMARK_OPACITY = 0.3
    WATERMARK_COLOR = (128, 128, 128)

    LIBREOFFICE_PATH = os.getenv("LIBREOFFICE_PATH", "libreoffice")

    os.makedirs(FILE_STORAGE_PATH, exist_ok=True)
    os.makedirs(DATA_PATH, exist_ok=True)
