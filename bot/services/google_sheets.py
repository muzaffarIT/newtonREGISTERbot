"""
google_sheets.py — работа с Google Sheets через gspread.
Исполняется в отдельном потоке (asyncio.to_thread) для неблокирующей работы Telegram.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

import gspread
from gspread import utils as gutils
from google.oauth2.service_account import Credentials
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from config import settings
from bot.utils.parser import Anketa

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ──────────────────────────────────────────────
# Вспомогательные функции (без API вызовов)
# ──────────────────────────────────────────────

def _is_header_row(row: list) -> bool:
    if not row or not row[0]:
        return False
    val = str(row[0]).strip().upper()
    return (
        val.startswith("ПСП")
        or val.startswith("ВЧС")
        or val.startswith("СВОБОДН")
        or val == "ГРУППЫ"
    )

def _safe_int(val) -> int:
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return 0

def _normalize(s: str) -> str:
    return str(s).strip().upper()

def _class_matches(row_group: str, row_class: str, grade: str) -> bool:
    g = _normalize(grade)
    rg = _normalize(row_group)
    rc = _normalize(row_class)
    if "ПОЧЕМУЧК" in g:
        return "ПОЧЕМУЧК" in rg
    return rc == g or g in rc

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")

_STUDENTS_HEADER = ["Дата", "Ребёнок", "Родитель", "Телефон", "Филиал", "Класс", "Язык", "Формат", "Время", "Группа", "Менеджер"]
_WAITING_HEADER  = ["Дата", "Ребёнок", "Родитель", "Телефон", "Филиал", "Класс", "Язык", "Формат", "Время", "Причина", "Менеджер"]


# ──────────────────────────────────────────────
# Синхронный сервис с Tenacity (Retries)
# ──────────────────────────────────────────────

class SyncGoogleSheetsService:
    """Обертка gspread с повторными попытками (Tenacity)"""

    def __init__(self):
        self._client: Optional[gspread.Client] = None

    def _get_client(self) -> gspread.Client:
        if not self._client:
            creds = Credentials.from_service_account_file(settings.CREDENTIALS_FILE, scopes=SCOPES)
            self._client = gspread.authorize(creds)
        return self._client

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(5), reraise=True)
    def _spreadsheet(self) -> gspread.Spreadsheet:
        return self._get_client().open_by_key(settings.SPREADSHEET_ID)

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def ensure_aux_sheets(self):
        ss = self._spreadsheet()
        existing = {ws.title for ws in ss.worksheets()}
        if settings.STUDENTS_SHEET not in existing:
            ws = ss.add_worksheet(settings.STUDENTS_SHEET, rows=2000, cols=15)
            ws.append_row(_STUDENTS_HEADER)
            logger.info(f"Created sheet: {settings.STUDENTS_SHEET}")
        if settings.WAITING_SHEET not in existing:
            ws = ss.add_worksheet(settings.WAITING_SHEET, rows=2000, cols=15)
            ws.append_row(_WAITING_HEADER)
            logger.info(f"Created sheet: {settings.WAITING_SHEET}")

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def find_matching_group(self, sheet_name: str, anketa: Anketa) -> Optional[Dict[str, Any]]:
        try:
            ws = self._spreadsheet().worksheet(sheet_name)
            rows = ws.get_all_values()
        except gspread.exceptions.WorksheetNotFound:
            logger.error(f"Sheet not found: {sheet_name}")
            return None

        candidates = []
        for i, row in enumerate(rows):
            if len(row) <= settings.COL_ACTUAL:
                continue
            if _is_header_row(row):
                continue
            group = str(row[settings.COL_GROUP]).strip()
            if not group:
                continue
            row_class = str(row[settings.COL_CLASS]).strip()
            row_lang  = _normalize(row[settings.COL_LANGUAGE])
            row_time  = str(row[settings.COL_TIME]).strip()
            row_fmt   = _normalize(row[settings.COL_FORMAT])
            capacity  = _safe_int(row[settings.COL_CAPACITY])
            actual    = _safe_int(row[settings.COL_ACTUAL])

            if capacity == 0:
                continue
                
            if not _class_matches(group, row_class, anketa.grade):
                continue
            if row_lang != _normalize(anketa.language):
                continue
            if row_fmt != _normalize(anketa.fmt):
                continue

            candidates.append({
                "row_index": i + 1,
                "group":     group,
                "class":     row_class,
                "language":  row_lang,
                "time":      row_time,
                "format":    row_fmt,
                "capacity":  capacity,
                "actual":    actual,
                "has_space": actual < capacity,
                "time_exact": row_time == anketa.time,
            })

        if not candidates:
            return None
            
        for c in candidates:
            if c["time_exact"] and c["has_space"]:
                return c
        for c in candidates:
            if c["time_exact"]:
                return c
        for c in candidates:
            if c["has_space"]:
                return c
        return candidates[0]

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def enroll_student_in_sheet(self, sheet_name: str, row_index: int, new_value: int) -> bool:
        ws = self._spreadsheet().worksheet(sheet_name)
        col_num = settings.COL_ACTUAL + 1
        cell = gutils.rowcol_to_a1(row_index, col_num)
        ws.update(cell, [[new_value]])
        return True

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def log_enrolled(self, anketa: Anketa, group: str):
        ws = self._spreadsheet().worksheet(settings.STUDENTS_SHEET)
        ws.append_row([
            _now(), anketa.child, anketa.parent, anketa.phone,
            anketa.branch, anketa.grade, anketa.language,
            anketa.fmt, anketa.time, group, anketa.manager,
        ])

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def log_waiting(self, anketa: Anketa, reason: str):
        ws = self._spreadsheet().worksheet(settings.WAITING_SHEET)
        ws.append_row([
            _now(), anketa.child, anketa.parent, anketa.phone,
            anketa.branch, anketa.grade, anketa.language,
            anketa.fmt, anketa.time, reason, anketa.manager,
        ])

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def get_groups_status(self, sheet_name: str) -> List[Dict]:
        try:
            ws = self._spreadsheet().worksheet(sheet_name)
            rows = ws.get_all_values()
        except Exception as e:
            logger.error(f"Error getting group status: {e}")
            return []

        result = []
        for row in rows:
            if len(row) <= settings.COL_ACTUAL: continue
            if _is_header_row(row): continue
            group = str(row[settings.COL_GROUP]).strip()
            if not group: continue
            capacity = _safe_int(row[settings.COL_CAPACITY])
            actual   = _safe_int(row[settings.COL_ACTUAL])
            if capacity == 0: continue
            result.append({
                "group":    group,
                "class":    str(row[settings.COL_CLASS]).strip(),
                "language": str(row[settings.COL_LANGUAGE]).strip(),
                "time":     str(row[settings.COL_TIME]).strip(),
                "format":   str(row[settings.COL_FORMAT]).strip(),
                "capacity": capacity,
                "actual":   actual,
                "free":     capacity - actual,
            })
        return result

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def get_students(self, branch: str = None) -> List[List]:
        ws = self._spreadsheet().worksheet(settings.STUDENTS_SHEET)
        rows = ws.get_all_values()[1:]
        if branch: return [r for r in rows if _normalize(r[4]) == _normalize(branch)]
        return rows

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def get_waiting(self, branch: str = None) -> List[List]:
        ws = self._spreadsheet().worksheet(settings.WAITING_SHEET)
        rows = ws.get_all_values()[1:]
        if branch: return [r for r in rows if _normalize(r[4]) == _normalize(branch)]
        return rows


# ──────────────────────────────────────────────
# Асинхронный Фасад
# ──────────────────────────────────────────────

class AsyncGoogleSheetsService:
    """Асинхронная обертка для неблокирующей работы в Telegram-боте"""
    
    def __init__(self):
        self._sync = SyncGoogleSheetsService()
        self._lock = asyncio.Lock()

    async def ensure_aux_sheets(self):
        await asyncio.to_thread(self._sync.ensure_aux_sheets)

    async def process_anketa(self, anketa: Anketa) -> Dict[str, Any]:
        """Умное зачисление. Использует глобальный лок, чтобы не было гонки."""
        branch_key = anketa.branch.strip().lower()
        sheet_name = settings.BRANCH_MAP.get(branch_key)
        
        if not sheet_name:
            return {"status": "error_branch"}

        # Лочим эту секцию, чтобы никто параллельно не перезаписал кол-во мест
        async with self._lock:
            match = await asyncio.to_thread(self._sync.find_matching_group, sheet_name, anketa)
            if not match:
                await asyncio.to_thread(self._sync.log_waiting, anketa, "Нет подходящей группы")
                return {"status": "waitlist_no_group"}

            if not match["has_space"]:
                await asyncio.to_thread(self._sync.log_waiting, anketa, f"Нет мест в группе {match['group']}")
                return {"status": "waitlist_full", "match": match}

            # Пишем если есть места
            new_count = match["actual"] + 1
            ok = await asyncio.to_thread(
                self._sync.enroll_student_in_sheet, sheet_name, match["row_index"], new_count
            )
            
            if ok:
                await asyncio.to_thread(self._sync.log_enrolled, anketa, match["group"])
                match["actual"] = new_count
                return {"status": "enrolled", "match": match}
            else:
                return {"status": "enroll_error"}

    async def get_groups_status(self, sheet_name: str) -> List[Dict]:
        return await asyncio.to_thread(self._sync.get_groups_status, sheet_name)

    async def get_students(self, branch: str = None) -> List[List]:
        return await asyncio.to_thread(self._sync.get_students, branch)

    async def get_waiting(self, branch: str = None) -> List[List]:
        return await asyncio.to_thread(self._sync.get_waiting, branch)


# Singleton
sheets_service = AsyncGoogleSheetsService()
