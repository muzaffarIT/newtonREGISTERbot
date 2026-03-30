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

CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "credentials.json")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

# Branch Mapping: lowercase branch name from anketa -> Google Sheet tab name
BRANCH_MAP = {
    "ракат":    "РАКАТ",
    "паркент":  "ПАРКЕНТ",
    "ганга":    "ГАНГА",
    "сергели":  "СЕРГЕЛИ",
    "чиланзар": "ЧИЛАНЗАР",
}
ALL_BRANCHES = list(BRANCH_MAP.values())

# Column Indices in Branch Sheets (0-based)
COL_GROUP    = 0   # A: Groups
COL_CLASS    = 1   # B: Class
COL_LANGUAGE = 2   # C: Language (РУС / УЗБ / МИКС)
COL_TIME     = 3   # D: Time
COL_FORMAT   = 4   # E: Format (ПСП / ВЧС)
COL_CHILDREN = 5   # F: Children count
COL_FREEZE   = 6   # G: Freeze count
COL_CAPACITY = 7   # H: Capacity
COL_ACTUAL   = 8   # I: Actual count (incremented on enroll)

# Auxiliary Sheets
STUDENTS_SHEET = "ЗАПИСИ"
WAITING_SHEET  = "ОЖИДАНИЕ"
