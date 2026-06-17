import os
import logging
from dotenv import load_dotenv

load_dotenv()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment or .env file.")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1RA12Gx0d7Bq7wnzZ_WOHZj-W1RFrLymMgxv9CDWX3s0")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
REPORT_CHAT_ID = int(os.getenv("REPORT_CHAT_ID", str(GROUP_CHAT_ID)))

CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "credentials.json")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

DAILY_REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", "18"))
DAILY_REPORT_MINUTE = int(os.getenv("DAILY_REPORT_MINUTE", "0"))

_allowed = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = [int(u.strip()) for u in _allowed.split(",")] if _allowed else []

# Branch Mapping: lowercase branch name from anketa -> Google Sheet tab name
BRANCH_MAP = {
    "ракат":    "РАКАТ",
    "паркент":  "ПАРКЕНТ",
    "ганга":    "ГАНГА2",
    "сергели":  "СЕРГЕЛИ",
    "чиланзар": "ЧИЛАНЗАР",
}
ALL_BRANCHES = list(BRANCH_MAP.values())

DATA_START_ROW = 5

# Column Indices in Branch Sheets (0-based)
COL_GROUP    = 1   # B: Groups
COL_CLASS    = 2   # C: Class
COL_LANGUAGE = 3   # D: Language (РУС / УЗБ / МИКС)
COL_TIME     = 4   # E: Time
COL_FORMAT   = 5   # F: Format (ПСП / ВЧС)
COL_CHILDREN = 6   # G: Children count
COL_FREEZE   = 7   # H: Freeze count
COL_CAPACITY = 8   # I: Capacity
COL_ACTUAL   = 9   # J: Actual count (incremented on enroll)

# Auxiliary Sheets
STUDENTS_SHEET = "ЗАПИСИ"
WAITING_SHEET  = "ОЖИДАНИЕ"
PENDING_SHEET  = "PENDING_DB"
