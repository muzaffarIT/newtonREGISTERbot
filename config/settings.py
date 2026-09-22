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
    "ганга":    "ГАНГА",
    "сергели":  "СЕРГЕЛИ",
    "чиланзар": "ЧИЛАНЗАР",
}
ALL_BRANCHES = list(BRANCH_MAP.values())

DATA_START_ROW = 5

# Column Indices in Branch Sheets (0-based).
# Раскладка листа филиала (шапка: "ГРУППЫ | Класс | Уровень | Отделение |
# Время обучения | День обучения | Кол-во детей | Кол-во заморозок |
# ВМЕСТИМОСТЬ кабинета | Кол-во факт"):
#   A(0) №  B(1) ГРУППЫ  C(2) Класс  D(3) Уровень  E(4) Отделение
#   F(5) Время обучения  G(6) День обучения  H(7) Кол-во детей
#   I(8) Кол-во заморозок  J(9) ВМЕСТИМОСТЬ  K(10) Кол-во факт
COL_GROUP    = 1   # B: Groups
COL_CLASS    = 2   # C: Class
COL_LEVEL    = 3   # D: Уровень (B / C) — не используется при подборе
COL_LANGUAGE = 4   # E: Отделение (РУС / УЗБ / МИКС)
COL_TIME     = 5   # F: Время обучения
COL_FORMAT   = 6   # G: День обучения (ПСП / ВЧС)
COL_CHILDREN = 7   # H: Кол-во детей (записанных) — обновляется при зачислении/отмене
COL_FREEZE   = 8   # I: Кол-во заморозок
COL_CAPACITY = 9   # J: ВМЕСТИМОСТЬ кабинета

# Auxiliary Sheets
STUDENTS_SHEET = "ЗАПИСИ"
WAITING_SHEET  = "ОЖИДАНИЕ"
PENDING_SHEET  = "PENDING_DB"
