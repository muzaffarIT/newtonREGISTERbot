from datetime import datetime
import pytz
from bot.utils.parser import Anketa

def _now_str() -> str:
    return datetime.now(pytz.timezone("Asia/Tashkent")).strftime("%d.%m.%Y в %H:%M")

def msg_enrolled(anketa: Anketa, group_name: str, actual: int, capacity: int) -> str:
    free = capacity - actual
    dt = _now_str()
    return (
        f"✅ <b>УЧЕНИК УСПЕШНО ЗАПИСАН</b>\n\n"
        f"👤 Ребёнок: {anketa.child}\n"
        f"👨‍👩‍👧 Родитель: {anketa.parent}\n"
        f"📞 Телефон: {anketa.phone}\n\n"
        f"📍 Филиал: {anketa.branch}\n"
        f"🏫 Группа: {group_name}\n"
        f"📚 Класс: {anketa.grade}  |  🗣 Язык: {anketa.language}\n"
        f"⏰ Формат: {anketa.fmt}  |  Время: {anketa.time}\n"
        f"📊 Мест занято: {actual} из {capacity} (осталось {free})\n"
        f"👨‍💼 Менеджер: {anketa.manager}\n"
        f"🕐 Дата: {dt}"
    )

def msg_waitlist(anketa: Anketa, reason: str) -> str:
    return (
        f"⏳ <b>ДОБАВЛЕН В ЛИСТ ОЖИДАНИЯ</b>\n\n"
        f"👤 Ребёнок: {anketa.child}\n"
        f"📍 Филиал: {anketa.branch}\n"
        f"📚 Класс: {anketa.grade}  |  🗣 Язык: {anketa.language}\n"
        f"⏰ Формат: {anketa.fmt}  |  Время: {anketa.time}\n"
        f"👨‍💼 Менеджер: {anketa.manager}\n"
        f"❓ Причина: {reason}\n\n"
        f"ℹ️ Бот автоматически запишет ученика\n"
        f"как только появится свободное место."
    )

def msg_80_percent(group_name: str, branch: str, actual: int, capacity: int, grade: str, lang: str, fmt: str, time: str) -> str:
    free = capacity - actual
    percent = int((actual / capacity) * 100) if capacity else 0
    return (
        f"⚠️ <b>ВНИМАНИЕ — ГРУППА ПОЧТИ ЗАПОЛНЕНА!</b>\n\n"
        f"🏫 {group_name} ({branch})\n"
        f"📊 Занято: {actual} из {capacity} мест ({percent}%)\n"
        f"📚 {grade} | {lang} | {fmt} {time}\n"
        f"🔴 Осталось всего {free} места!"
    )
