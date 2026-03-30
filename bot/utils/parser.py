"""
parser.py — разбор анкеты Newton Academy из Telegram-сообщения.
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Anketa:
    request_type: str
    sent_at: str
    manager: str
    branch: str
    parent: str
    child: str
    grade: str          # Класс (Почемучка / 2 / 3 / ...)
    period: str
    fmt: str            # ПСП или ВЧС
    time: str           # 18:00-20:00
    language: str       # РУС / УЗБ / МИКС
    phone: str
    math_level: Optional[str] = None
    english_level: Optional[str] = None
    comment: Optional[str] = None


def normalize_time(t: str) -> str:
    t = re.sub(r"[–—−‒]", "-", t)   # любые тире → дефис
    t = re.sub(r"\s*-\s*", "-", t)  # убрать пробелы вокруг дефиса
    return t.strip()


def _get(pattern: str, text: str, default: str = "") -> str:
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else default


def parse_anketa(text: str) -> Optional[Anketa]:
    """Вернёт Anketa или None если это не анкета."""
    if "Анкета Newton Academy" not in text:
        return None

    branch   = _get(r"Филиал:\s*(.+)", text)
    grade    = _get(r"Класс:\s*(.+)", text)
    parent   = _get(r"Родитель:\s*(.+)", text)
    child    = _get(r"Ребёнок:\s*(.+)", text)
    manager  = _get(r"Менеджер:\s*(.+)", text)
    sent_at  = _get(r"Отправлена:\s*(.+)", text)
    req_type = _get(r"Тип заявки:\s*(.+)", text)
    period   = _get(r"Период обучения:\s*\n(.+)", text)
    language = _get(r"Язык:\s*(.+)", text)
    comment  = _get(r"Комментарий:\s*\n(.+)", text)

    # Формат и время: "• ПСП | 18:00-20:00"
    ft = re.search(r"•\s*(ПСП|ВЧС|ВНС)\s*\|\s*(\d{1,2}:\d{2}-\d{1,2}:\d{2})", text)
    fmt  = ft.group(1).replace("ВНС", "ВЧС") if ft else ""
    time = ft.group(2) if ft else ""
    time = normalize_time(time)

    # Телефон
    ph = re.search(r"Контакты:\s*\n•\s*(\+?\d[\d\s\-]+)", text)
    phone = ph.group(1).strip() if ph else ""

    math_m = re.search(r"Математика:\s*(.+)", text)
    eng_m  = re.search(r"Английский:\s*(.+)", text)

    return Anketa(
        request_type=req_type,
        sent_at=sent_at,
        manager=manager,
        branch=branch,
        parent=parent,
        child=child,
        grade=grade,
        period=period,
        fmt=fmt,
        time=time,
        language=language.upper(),
        phone=phone,
        math_level=math_m.group(1).strip() if math_m else None,
        english_level=eng_m.group(1).strip() if eng_m else None,
        comment=comment if comment.lower() != "нет" else None,
    )
