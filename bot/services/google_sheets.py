import asyncio
import logging
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

import gspread
from gspread import utils as gutils
from google.oauth2.service_account import Credentials
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from config import settings
from bot.utils.parser import Anketa, normalize_time

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
    skip_prefixes = ("ПСП", "ВЧС", "ВНС", "СВОБОДН", "ГРУППЫ", "1 СМЕНА", "2 СМЕНА", "3 СМЕНА")
    return any(val.startswith(p) for p in skip_prefixes)

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

def _normalize_phone(ph: str) -> str:
    return ph.replace(" ", "").replace("-", "").replace("+", "")

_STUDENTS_HEADER = ["Дата", "Ребёнок", "Родитель", "Телефон", "Филиал", "Класс", "Язык", "Формат", "Время", "Группа", "Менеджер", "Лист(таблицы)", "Строка(таблицы)"]
_WAITING_HEADER  = ["Дата", "Ребёнок", "Родитель", "Телефон", "Филиал", "Класс", "Язык", "Формат", "Время", "Причина", "Менеджер", "Статус"]
_PENDING_HEADER  = ["UUID", "Дата", "Тип", "Данные (JSON)", "Статус"]

# ──────────────────────────────────────────────
# Синхронный сервис с Tenacity (Retries)
# ──────────────────────────────────────────────

class SyncGoogleSheetsService:
    def __init__(self):
        self._client: Optional[gspread.Client] = None

    def _get_client(self) -> gspread.Client:
        if not self._client:
            if settings.GOOGLE_CREDENTIALS_JSON:
                creds_dict = json.loads(settings.GOOGLE_CREDENTIALS_JSON)
                creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
            else:
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
        if settings.PENDING_SHEET not in existing:
            ws = ss.add_worksheet(settings.PENDING_SHEET, rows=2000, cols=10)
            ws.append_row(_PENDING_HEADER)
            logger.info(f"Created sheet: {settings.PENDING_SHEET}")

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def find_matching_candidates(self, sheet_name: str, anketa: Anketa) -> List[Dict[str, Any]]:
        try:
            ws = self._spreadsheet().worksheet(sheet_name)
            rows = ws.get_all_values()
        except gspread.exceptions.WorksheetNotFound:
            logger.error(f"Sheet not found: {sheet_name}")
            return []

        candidates = []
        for i, row in enumerate(rows):
            # i = 0 based + 1 = 1 based. Data starts at row 5 (index 4)
            if i + 1 < settings.DATA_START_ROW:
                continue
            if len(row) <= settings.COL_ACTUAL:
                continue
            if _is_header_row(row):
                continue
            
            group = str(row[settings.COL_GROUP]).strip()
            if not group:
                continue
                
            row_class = str(row[settings.COL_CLASS]).strip()
            row_lang  = _normalize(row[settings.COL_LANGUAGE])
            row_fmt   = _normalize(row[settings.COL_FORMAT])
            row_time  = normalize_time(str(row[settings.COL_TIME]).strip())
            
            capacity  = _safe_int(row[settings.COL_CAPACITY])
            actual    = _safe_int(row[settings.COL_ACTUAL])

            if capacity == 0:
                continue
                
            if not _class_matches(group, row_class, anketa.grade):
                continue

            has_space = actual < capacity
            a_lang = _normalize(anketa.language)
            a_fmt = _normalize(anketa.fmt)
            a_time = normalize_time(anketa.time)
            
            match_type = 0
            
            # Приоритетный поиск
            if row_lang == a_lang and row_fmt == a_fmt and row_time == a_time:
                match_type = 1 if has_space else 2
            elif has_space:
                if row_lang == a_lang and row_fmt != a_fmt and row_time == a_time:
                    match_type = 4
                elif "МИКС" in row_lang and a_lang in ["РУС", "УЗБ"]:
                    match_type = 5
                elif row_lang == a_lang and row_fmt == a_fmt and row_time != a_time:
                    match_type = 3

            if match_type > 0:
                candidates.append({
                    "row_index": i + 1,
                    "group":     group,
                    "class":     row_class,
                    "language":  row_lang,
                    "time":      row_time,
                    "format":    row_fmt,
                    "capacity":  capacity,
                    "actual":    actual,
                    "has_space": has_space,
                    "match_type": match_type,
                    "sheet_name": sheet_name
                })

        return sorted(candidates, key=lambda c: c["match_type"])

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def enroll_student_in_sheet(self, sheet_name: str, row_index: int, new_value: int) -> bool:
        ws = self._spreadsheet().worksheet(sheet_name)
        col_num = settings.COL_ACTUAL + 1
        cell = gutils.rowcol_to_a1(row_index, col_num)
        ws.update(cell, [[new_value]])
        return True

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def log_enrolled(self, anketa: Anketa, group: str, sheet_name: str, row_index: int):
        ws = self._spreadsheet().worksheet(settings.STUDENTS_SHEET)
        ws.append_row([
            _now(), anketa.child, anketa.parent, anketa.phone,
            anketa.branch, anketa.grade, anketa.language,
            anketa.fmt, anketa.time, group, anketa.manager,
            sheet_name, row_index
        ])

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def log_waiting(self, anketa: Anketa, reason: str):
        ws = self._spreadsheet().worksheet(settings.WAITING_SHEET)
        ws.append_row([
            _now(), anketa.child, anketa.parent, anketa.phone,
            anketa.branch, anketa.grade, anketa.language,
            anketa.fmt, anketa.time, reason, anketa.manager,
            "ожидает"
        ])

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def check_duplicate(self, child_name: str, phone: str) -> bool:
        ws = self._spreadsheet().worksheet(settings.STUDENTS_SHEET)
        rows = ws.get_all_values()[1:]
        target_child = _normalize(child_name)
        target_phone = _normalize_phone(phone)
        for r in rows:
            if len(r) >= 4 and _normalize(r[1]) == target_child and _normalize_phone(r[3]) == target_phone:
                if len(r) > 11 and r[11] != "[ОТМЕНЕНО]": # ensure it's not cancelled
                    return True
                elif len(r) <= 11:
                    return True
        return False

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def get_groups_status(self, sheet_name: str) -> List[Dict]:
        try:
            ws = self._spreadsheet().worksheet(sheet_name)
            rows = ws.get_all_values()
        except Exception as e:
            logger.error(f"Error getting group status: {e}")
            return []

        result = []
        for i, row in enumerate(rows):
            if i + 1 < settings.DATA_START_ROW: continue
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
        if branch: return [r for r in rows if len(r) > 4 and _normalize(r[4]) == _normalize(branch)]
        return rows

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def get_waiting(self, branch: str = None) -> List[List]:
        ws = self._spreadsheet().worksheet(settings.WAITING_SHEET)
        rows = ws.get_all_values()[1:]
        if branch: return [r for r in rows if len(r) > 4 and _normalize(r[4]) == _normalize(branch)]
        return rows
        
    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def save_pending_request(self, uuid_str: str, req_type: str, data: dict):
        ws = self._spreadsheet().worksheet(settings.PENDING_SHEET)
        ws.append_row([uuid_str, _now(), req_type, json.dumps(data, ensure_ascii=False), "ожидает"])

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def get_pending_request(self, uuid_str: str) -> Optional[dict]:
        ws = self._spreadsheet().worksheet(settings.PENDING_SHEET)
        rows = ws.get_all_values()[1:]
        for r in rows:
            if len(r) >= 5 and r[0] == uuid_str:
                if r[4] == "ожидает":
                    return {"type": r[2], "data": json.loads(r[3])}
        return None

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def resolve_pending_request(self, uuid_str: str, new_status: str):
        ws = self._spreadsheet().worksheet(settings.PENDING_SHEET)
        rows = ws.get_all_values()
        for i, r in enumerate(rows):
            if len(r) >= 5 and r[0] == uuid_str:
                cell = gutils.rowcol_to_a1(i + 1, 5) # Status is 5th column
                ws.update(cell, [[new_status]])
                break
                
    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def cancel_student(self, child_name: str, phone: str) -> bool:
        ws = self._spreadsheet().worksheet(settings.STUDENTS_SHEET)
        rows = ws.get_all_values()
        target_child = _normalize(child_name)
        target_phone = _normalize_phone(phone)
        found_idx = -1
        for i, r in enumerate(rows):
            if i == 0: continue
            if len(r) >= 13 and _normalize(r[1]) == target_child and _normalize_phone(r[3]) == target_phone:
                if r[12] != "[ОТМЕНЕНО]":
                    found_idx = i
                    break
        
        if found_idx == -1: return False
        
        row_data = rows[found_idx]
        sheet_name = row_data[11]
        sheet_row_idx = _safe_int(row_data[12])
        
        # Обновляем таблицу филиала
        try:
            branch_ws = self._spreadsheet().worksheet(sheet_name)
            cell_val = branch_ws.acell(gutils.rowcol_to_a1(sheet_row_idx, settings.COL_ACTUAL + 1)).value
            new_val = max(0, _safe_int(cell_val) - 1)
            branch_ws.update(gutils.rowcol_to_a1(sheet_row_idx, settings.COL_ACTUAL + 1), [[new_val]])
        except Exception as e:
            logger.error(f"Error subtracting capacity in sheet {sheet_name}: {e}")
            # Продожаем, чтобы хотя бы в ЗАПИСИ отметить отмену.
            
        # Обновляем ЗАПИСИ
        ws.update(gutils.rowcol_to_a1(found_idx + 1, 12), [["[ОТМЕНЕНО]"]])
        ws.update(gutils.rowcol_to_a1(found_idx + 1, 13), [["[ОТМЕНЕНО]"]])
        return True


# ──────────────────────────────────────────────
# Асинхронный Фасад
# ──────────────────────────────────────────────

class AsyncGoogleSheetsService:
    def __init__(self):
        self._sync = SyncGoogleSheetsService()
        self._lock = asyncio.Lock()

    async def ensure_aux_sheets(self):
        await asyncio.to_thread(self._sync.ensure_aux_sheets)

    async def check_duplicate(self, child_name: str, phone: str) -> bool:
        return await asyncio.to_thread(self._sync.check_duplicate, child_name, phone)
        
    async def get_pending_request(self, uuid_str: str) -> Optional[dict]:
        return await asyncio.to_thread(self._sync.get_pending_request, uuid_str)

    async def resolve_pending_request(self, uuid_str: str, status: str):
        await asyncio.to_thread(self._sync.resolve_pending_request, uuid_str, status)

    async def save_pending_request(self, uuid_str: str, req_type: str, data: dict):
        await asyncio.to_thread(self._sync.save_pending_request, uuid_str, req_type, data)

    async def cancel_student(self, child_name: str, phone: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._sync.cancel_student, child_name, phone)

    async def process_anketa(self, anketa: Anketa, bypass_match: Optional[Dict] = None) -> Dict[str, Any]:
        """Умное зачисление."""
        branch_key = anketa.branch.strip().lower()
        sheet_name = settings.BRANCH_MAP.get(branch_key)
        if not sheet_name:
            return {"status": "error_branch"}

        async with self._lock:
            if bypass_match:
                # Менеджер подтвердил конкретную группу из bypass_match
                # Нужно проверить, актуально ли место
                current_status = await asyncio.to_thread(self._sync.get_groups_status, sheet_name)
                # finding the actual current capacity
                curr = next((g for g in current_status if g["group"] == bypass_match["group"]), None)
                if not curr or curr["free"] <= 0:
                    return {"status": "waitlist_full", "match": bypass_match}
                
                new_count = curr["actual"] + 1
                ok = await asyncio.to_thread(
                    self._sync.enroll_student_in_sheet, sheet_name, bypass_match["row_index"], new_count
                )
                if ok:
                    await asyncio.to_thread(self._sync.log_enrolled, anketa, bypass_match["group"], sheet_name, bypass_match["row_index"])
                    bypass_match["actual"] = new_count
                    return {"status": "enrolled", "match": bypass_match}
                return {"status": "enroll_error"}

            # Нормальный поиск
            candidates = await asyncio.to_thread(self._sync.find_matching_candidates, sheet_name, anketa)
            if not candidates:
                return {"status": "waitlist_no_group"}

            best = candidates[0]
            if best["match_type"] == 1:
                # Однозначное совпадение
                new_count = best["actual"] + 1
                ok = await asyncio.to_thread(
                    self._sync.enroll_student_in_sheet, sheet_name, best["row_index"], new_count
                )
                if ok:
                    await asyncio.to_thread(self._sync.log_enrolled, anketa, best["group"], sheet_name, best["row_index"])
                    best["actual"] = new_count
                    return {"status": "enrolled", "match": best}
                return {"status": "enroll_error"}
            else:
                # match_type 2,3,4,5
                if best["match_type"] == 2:
                    status_str = "waitlist_full"
                else:
                    status_str = f"ask_manager_{best['match_type']}"
                return {"status": status_str, "candidates": candidates, "match": best}

    async def log_waiting(self, anketa: Anketa, reason: str):
         await asyncio.to_thread(self._sync.log_waiting, anketa, reason)

    async def get_groups_status(self, sheet_name: str) -> List[Dict]:
        return await asyncio.to_thread(self._sync.get_groups_status, sheet_name)

    async def get_students(self, branch: str = None) -> List[List]:
        return await asyncio.to_thread(self._sync.get_students, branch)

    async def get_waiting(self, branch: str = None) -> List[List]:
        return await asyncio.to_thread(self._sync.get_waiting, branch)
        
    async def get_waiting_raw(self, branch: str = None) -> List[List]:
        # returns the unmodified list to get actual indexes maybe
        return await asyncio.to_thread(self._sync.get_waiting, branch) # currently the same

sheets_service = AsyncGoogleSheetsService()
