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
    if not row:
        return False
    val0 = str(row[0]).strip().upper() if row[0] else ""
    val1 = str(row[1]).strip().upper() if len(row) > 1 and row[1] else ""
    skip_prefixes = ("ПСП", "ВЧС", "ВНС", "СВОБОДН", "ГРУППЫ", "1 СМЕНА", "2 СМЕНА", "3 СМЕНА")
    return any(val0.startswith(p) or val1.startswith(p) for p in skip_prefixes)

def get_group_name_and_class(row: list) -> tuple[str, str]:
    """Возвращает (название_группы, класс) с учётом особых строк."""
    col_b = str(row[settings.COL_GROUP]).strip() if len(row) > settings.COL_GROUP else ""
    col_c = str(row[settings.COL_CLASS]).strip() if len(row) > settings.COL_CLASS else ""
    
    if col_b:
        return col_b, col_c
    elif col_c:
        # B пустой, название в C
        group_name = col_c
        # Определяем класс по названию
        upper = col_c.upper()
        if "ПОЧЕМУЧК" in upper:
            grade = "Почемучка"
        elif "ДТМ" in upper:
            grade = "ДТМ"
        elif "SENIOR" in upper:
            grade = "Senior"
        else:
            grade = col_c
        return group_name, grade
    else:
        return "", ""

def _safe_int(val) -> int:
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return 0

def _normalize(s: str) -> str:
    return str(s).strip().upper().replace("-", " ")

import re

def _evaluate_class_match(row_group: str, row_class: str, grade: str) -> int:
    g = _normalize(grade)
    rg = _normalize(row_group)
    rc = _normalize(row_class)
    if "ПОЧЕМУЧК" in g:
        return 2 if "ПОЧЕМУЧК" in rg or "ПОЧЕМУЧК" in rc else 0
    
    if rc == g:
        return 2 # Exact match
        
    g_digits = re.findall(r'\d+', g)
    rc_digits = re.findall(r'\d+', rc)
    if g_digits and rc_digits and g_digits[0] == rc_digits[0]:
        return 1 # e.g. "6" vs "6 МИРЗО УЛУГБЕК"
        
    if "ДТМ" in g and "ДТМ" in rc:
        return 1
        
    if g and (g in rc or rc in g):
        return 1
        
    return 0

def _now() -> str:
    import pytz
    return datetime.now(pytz.timezone("Asia/Tashkent")).strftime("%Y-%m-%d %H:%M")

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
            if i + 1 < settings.DATA_START_ROW:
                continue
            
            # Дополняем строку пустыми значениями до нужной длины
            row = list(row) + [""] * max(0, 15 - len(row))
            
            if _is_header_row(row):
                continue
            
            group, row_class = get_group_name_and_class(row)
            if not group:
                continue
                
            class_match_level = _evaluate_class_match(group, row_class, anketa.grade)
            if class_match_level == 0:
                continue
                
            row_lang  = _normalize(row[settings.COL_LANGUAGE])
            row_fmt   = _normalize(row[settings.COL_FORMAT])
            row_time  = normalize_time(str(row[settings.COL_TIME]).strip())
            
            capacity  = _safe_int(row[settings.COL_CAPACITY])
            actual    = _safe_int(row[settings.COL_CHILDREN])  # G: кол-во детей (записанных)
            freeze    = _safe_int(row[settings.COL_FREEZE]) if len(row) > settings.COL_FREEZE else 0

            if capacity == 0:
                # Если менеджер только создал группу и забыл указать вместимость, предполагаем 12
                capacity = 12

            has_space = actual < capacity
            available_space = capacity - actual - freeze
            
            a_lang = _normalize(anketa.language)
            a_fmt = _normalize(anketa.fmt)
            a_time = normalize_time(anketa.time)
            
            match_type = 0
            
            if row_lang == a_lang and row_fmt == a_fmt and row_time == a_time:
                if has_space:
                    if class_match_level == 2 and available_space > 0:
                        match_type = 1  # Perfect — exact class, has real space (not freeze)
                    elif class_match_level == 2 and available_space <= 0:
                        match_type = 2  # Freeze Warning — fits capacity but uses freeze buffer
                    elif class_match_level == 1:
                        match_type = 3  # Fuzzy Class — partial class match
                else:
                    match_type = 7  # Full — no space at all
            elif has_space and class_match_level >= 1:
                if row_lang == a_lang and row_fmt != a_fmt and row_time == a_time:
                    match_type = 5  # Wrong format
                elif "МИКС" in row_lang and a_lang in ["РУС", "УЗБ"]:
                    match_type = 6  # Mix language
                elif row_lang == a_lang and row_fmt == a_fmt and row_time != a_time:
                    match_type = 4  # Wrong time

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
                    "freeze":    freeze,
                    "has_space": has_space,
                    "available_space": available_space,
                    "match_type": match_type,
                    "sheet_name": sheet_name
                })

        return sorted(candidates, key=lambda c: c["match_type"])

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def enroll_student_in_sheet(self, sheet_name: str, row_index: int, new_value: int) -> bool:
        ws = self._spreadsheet().worksheet(sheet_name)
        col_num = settings.COL_CHILDREN + 1  # G: количество детей (не трогаем J с формулой)
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
        current_shift_name = ""
        for i, row in enumerate(rows):
            if i + 1 < settings.DATA_START_ROW: continue
            
            row = list(row) + [""] * max(0, 15 - len(row))
            
            if _is_header_row(row):
                val = str(row[0]).strip().upper()
                if "СМЕНА" in val:
                    current_shift_name = val.lower()
                continue
            
            group, row_class = get_group_name_and_class(row)
            if not group: continue
            
            capacity = _safe_int(row[settings.COL_CAPACITY])
            if capacity == 0: 
                capacity = 12
            
            actual   = _safe_int(row[settings.COL_CHILDREN])  # G: количество детей
            freeze   = _safe_int(row[settings.COL_FREEZE]) if len(row) > settings.COL_FREEZE else 0
            
            result.append({
                "group":    group,
                "class":    row_class,
                "language": str(row[settings.COL_LANGUAGE]).strip(),
                "time":     str(row[settings.COL_TIME]).strip(),
                "format":   str(row[settings.COL_FORMAT]).strip(),
                "capacity": capacity,
                "actual":   actual,
                "freeze":   freeze,
                "free":     capacity - actual - freeze,
                "shift_name": current_shift_name,
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
            # col 11 = Лист(таблицы), col 12 = Строка(таблицы)
            # We detect cancellation by checking col 11 for the sentinel
            if len(r) >= 12 and _normalize(r[1]) == target_child and _normalize_phone(r[3]) == target_phone:
                if r[11] != "[ОТМЕНЕНО]":  # col index 11 = sheet name column (we mark it)
                    found_idx = i
                    break
        
        if found_idx == -1: return False
        
        row_data = rows[found_idx]
        sheet_name = row_data[11]           # Лист(таблицы)
        sheet_row_idx = _safe_int(row_data[12]) if len(row_data) > 12 else 0  # Строка(таблицы)
        
        # Обновляем таблицу филиала
        if sheet_row_idx > 0 and sheet_name and sheet_name != "[ОТМЕНЕНО]":
            try:
                branch_ws = self._spreadsheet().worksheet(sheet_name)
                cell_val = branch_ws.acell(gutils.rowcol_to_a1(sheet_row_idx, settings.COL_CHILDREN + 1)).value  # G
                new_val = max(0, _safe_int(cell_val) - 1)
                branch_ws.update(gutils.rowcol_to_a1(sheet_row_idx, settings.COL_CHILDREN + 1), [[new_val]])  # G
                logger.info(f"Decremented count in {sheet_name} row {sheet_row_idx} → {new_val}")
            except Exception as e:
                logger.error(f"Error subtracting capacity in sheet {sheet_name}: {e}")
                # Продолжаем, чтобы хотя бы в ЗАПИСИ отметить отмену.
            
        # Обновляем ЗАПИСИ: помечаем столбцы Лист и Строка как [ОТМЕНЕНО]
        ws.update(gutils.rowcol_to_a1(found_idx + 1, 12), [["[ОТМЕНЕНО]"]])
        ws.update(gutils.rowcol_to_a1(found_idx + 1, 13), [["[ОТМЕНЕНО]"]])
        return True

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def update_branch_statistics_sheets(self):
        ss = self._spreadsheet()
        existing = {ws.title for ws in ss.worksheets()}
        
        def is_valid_class(cls_str):
            upper = cls_str.upper()
            subjects = ["МАТЕМАТИКА", "ИНФОРМАТИКА", "ФИЗИКА", "ГЕОГРАФИЯ", "БИОЛОГИЯ", "ХИМИЯ", "ВСЕМИРН", "ПРАВО", "АНГЛИЙСКИЙ", "МАТ ", "MAT "]
            for s in subjects:
                if s in upper:
                    return False
            return True

        def get_shift_by_time(time_str):
            t = time_str.strip().replace(" ", "")
            if not t:
                return "1 смена (8:30-11:30)"
            
            import re
            match = re.match(r'(\d{1,2})[:.](\d{2})', t)
            if match:
                hour = int(match.group(1))
                if 7 <= hour < 12:
                    return "1 смена (8:30-11:30)"
                elif 12 <= hour < 16:
                    return "2 смена (14:30-17:30)"
                elif 16 <= hour <= 22:
                    return "3 смена (17:30-20:30)"
            return "1 смена (8:30-11:30)"

        def map_lang(lang_str):
            l = lang_str.strip().upper()
            if "РУС" in l or l == "РО":
                return "РУС"
            if "УЗБ" in l or l == "КО" or "УЗ" in l:
                return "УЗБ"
            if "МИК" in l or "MIX" in l or l == "МО":
                return "МИКС"
            if "АНГ" in l or l == "АО":
                return "АНГЛ"
            return l

        def sort_classes(cls):
            import re
            if "ПОЧЕМУЧК" in cls.upper():
                return (-1, 0, cls)
            nums = re.findall(r'\d+', cls)
            if nums:
                return (0, int(nums[0]), cls)
            return (1, 0, cls)
            
        for branch_key, sheet_name in settings.BRANCH_MAP.items():
            groups = self.get_groups_status(sheet_name)
            if not groups:
                continue
                
            stats = {}
            formats_list = ["ПСП", "ВЧС"]
            
            for g in groups:
                cls_key = g['class'].strip()
                if not cls_key or not is_valid_class(cls_key):
                    continue
                    
                format_val = g['format']
                if format_val not in formats_list:
                    continue
                    
                time_val = g['time']
                shift_block = get_shift_by_time(time_val)
                lang_key = map_lang(g['language'])
                
                if shift_block not in stats:
                    stats[shift_block] = {}
                if cls_key not in stats[shift_block]:
                    stats[shift_block][cls_key] = {}
                if lang_key not in stats[shift_block][cls_key]:
                    stats[shift_block][cls_key][lang_key] = {}
                if format_val not in stats[shift_block][cls_key][lang_key]:
                    stats[shift_block][cls_key][lang_key][format_val] = {
                        "actual": 0,
                        "groups_count": 0,
                        "free": 0,
                        "capacity": 0
                    }
                
                s = stats[shift_block][cls_key][lang_key][format_val]
                s["actual"] += g["actual"]
                s["groups_count"] += 1
                s["free"] += g["free"]
                s["capacity"] += g["capacity"]
                
            stat_sheet_title = f"статистика {branch_key.lower()}"
            
            if stat_sheet_title not in existing:
                try:
                    ws = ss.add_worksheet(stat_sheet_title, rows=1000, cols=12)
                    logger.info(f"Created stat sheet: {stat_sheet_title}")
                    existing.add(stat_sheet_title)
                except Exception as e:
                    logger.error(f"Error creating stat sheet {stat_sheet_title}: {e}")
                    continue
            else:
                ws = ss.worksheet(stat_sheet_title)
                
            rows = []
            title_rows = []
            header_rows = []
            detail_rows = []
            total_rows = []
            
            sorted_shifts = sorted(stats.keys())
            
            for shift_block in sorted_shifts:
                classes = stats[shift_block]
                
                row_idx = len(rows) + 1
                title_rows.append(row_idx)
                title_row = []
                for fmt in formats_list:
                    title_row.extend([f"{fmt} {shift_block}", "", "", "", "", ""])
                rows.append(title_row)
                
                row_idx = len(rows) + 1
                header_rows.append(row_idx)
                header_row = []
                for _ in formats_list:
                    header_row.extend(["Класс", "Отделение", "Дети", "Группы", "Места", "Средн"])
                rows.append(header_row)
                
                start_detail_row = len(rows) + 1
                sorted_classes = sorted(classes.keys(), key=sort_classes)
                
                stripe_idx = 0
                for cls_key in sorted_classes:
                    sorted_langs = sorted(classes[cls_key].keys())
                    for lang_key in sorted_langs:
                        row_idx = len(rows) + 1
                        is_even = (stripe_idx % 2 == 0)
                        detail_rows.append((row_idx, is_even))
                        stripe_idx += 1
                        
                        row = []
                        d_psp = classes[cls_key][lang_key].get("ПСП")
                        if d_psp and d_psp["groups_count"] > 0:
                            row.extend([cls_key, lang_key, d_psp["actual"], d_psp["groups_count"], d_psp["free"], f'=IFERROR(C{row_idx}/D{row_idx}; "нет группы")'])
                        else:
                            row.extend([cls_key, lang_key, 0, 0, 0, "нет группы"])
                            
                        d_vcs = classes[cls_key][lang_key].get("ВЧС")
                        if d_vcs and d_vcs["groups_count"] > 0:
                            row.extend([cls_key, lang_key, d_vcs["actual"], d_vcs["groups_count"], d_vcs["free"], f'=IFERROR(I{row_idx}/J{row_idx}; "нет группы")'])
                        else:
                            row.extend([cls_key, lang_key, 0, 0, 0, "нет группы"])
                            
                        rows.append(row)
                
                end_detail_row = len(rows)
                
                row_idx = len(rows) + 1
                total_rows.append(row_idx)
                total_row = []
                
                total_row.extend([
                    "Всего ПСП", 
                    "", 
                    f"=SUM(C{start_detail_row}:C{end_detail_row})", 
                    f"=SUM(D{start_detail_row}:D{end_detail_row})", 
                    f"=SUM(E{start_detail_row}:E{end_detail_row})", 
                    f"=IFERROR(C{row_idx}/D{row_idx}; 0)"
                ])
                total_row.extend([
                    "Всего ВЧС", 
                    "", 
                    f"=SUM(I{start_detail_row}:I{end_detail_row})", 
                    f"=SUM(J{start_detail_row}:J{end_detail_row})", 
                    f"=SUM(K{start_detail_row}:K{end_detail_row})", 
                    f"=IFERROR(I{row_idx}/J{row_idx}; 0)"
                ])
                rows.append(total_row)
                
                rows.append([""] * 12)
            
            ws.clear()
            if rows:
                ws.update(range_name="A1", values=rows, value_input_option="USER_ENTERED")
                
                sheet_id = int(ws.id)
                requests = []
                
                for r in title_rows:
                    requests.append({
                        "mergeCells": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": r - 1,
                                "endRowIndex": r,
                                "startColumnIndex": 0,
                                "endColumnIndex": 6
                            },
                            "mergeType": "MERGE_ALL"
                        }
                    })
                    requests.append({
                        "mergeCells": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": r - 1,
                                "endRowIndex": r,
                                "startColumnIndex": 6,
                                "endColumnIndex": 12
                            },
                            "mergeType": "MERGE_ALL"
                        }
                    })
                    requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": r - 1,
                                "endRowIndex": r,
                                "startColumnIndex": 0,
                                "endColumnIndex": 12
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "backgroundColor": {"red": 0.18, "green": 0.24, "blue": 0.35},
                                    "textFormat": {
                                        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                                        "fontSize": 11,
                                        "bold": True
                                    },
                                    "horizontalAlignment": "CENTER",
                                    "verticalAlignment": "MIDDLE"
                                }
                            },
                            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
                        }
                    })
                    
                for r in header_rows:
                    requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": r - 1,
                                "endRowIndex": r,
                                "startColumnIndex": 0,
                                "endColumnIndex": 12
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "backgroundColor": {"red": 0.92, "green": 0.94, "blue": 0.96},
                                    "textFormat": {
                                        "foregroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
                                        "fontSize": 10,
                                        "bold": True
                                    },
                                    "horizontalAlignment": "CENTER",
                                    "verticalAlignment": "MIDDLE"
                                }
                            },
                            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
                        }
                    })
                    
                for r, is_even in detail_rows:
                    bg = {"red": 1.0, "green": 1.0, "blue": 1.0} if is_even else {"red": 0.97, "green": 0.98, "blue": 0.99}
                    requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": r - 1,
                                "endRowIndex": r,
                                "startColumnIndex": 0,
                                "endColumnIndex": 12
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "backgroundColor": bg,
                                    "textFormat": {
                                        "fontSize": 10
                                    },
                                    "horizontalAlignment": "CENTER",
                                    "verticalAlignment": "MIDDLE"
                                }
                            },
                            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
                        }
                    })
                    
                for r in total_rows:
                    requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": r - 1,
                                "endRowIndex": r,
                                "startColumnIndex": 0,
                                "endColumnIndex": 12
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "backgroundColor": {"red": 0.88, "green": 0.9, "blue": 0.92},
                                    "textFormat": {
                                        "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0},
                                        "fontSize": 10,
                                        "bold": True
                                    },
                                    "horizontalAlignment": "CENTER",
                                    "verticalAlignment": "MIDDLE"
                                }
                            },
                            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
                        }
                    })
                    
                requests.append({
                    "updateBorders": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": len(rows),
                            "startColumnIndex": 0,
                            "endColumnIndex": 12
                        },
                        "top": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}},
                        "bottom": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}},
                        "left": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}},
                        "right": {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}},
                        "innerHorizontal": {"style": "SOLID", "width": 1, "color": {"red": 0.85, "green": 0.85, "blue": 0.85}},
                        "innerVertical": {"style": "SOLID", "width": 1, "color": {"red": 0.85, "green": 0.85, "blue": 0.85}}
                    }
                })
                
                requests.append({
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": 0,
                            "endIndex": 12
                        },
                        "properties": {
                            "pixelSize": 90
                        },
                        "fields": "pixelSize"
                    }
                })
                
                try:
                    ss.batch_update({"requests": requests})
                    logger.info(f"Applied formatting to {stat_sheet_title}")
                except Exception as e:
                    logger.error(f"Error applying formatting to {stat_sheet_title}: {e}")


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
                if not curr or (curr["capacity"] - curr["actual"]) <= 0:
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
                if best["match_type"] == 7:
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

    async def update_branch_statistics_sheets(self):
        await asyncio.to_thread(self._sync.update_branch_statistics_sheets)

sheets_service = AsyncGoogleSheetsService()
