import logging
from datetime import datetime
import pytz
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from bot.services.google_sheets import sheets_service

router = Router()
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from bot.services.scheduler import send_daily_report

async def send_lines_chunked(message: Message, status_msg: Message, lines: list, max_len: int = 4050):
    """
    Умное разбиение длинного текста на чанки.
    Так как теги находятся внутри отдельных строк в списке lines,
    обрезка по строкам гарантирует, что HTML не сломается.
    Первый чанк редактирует status_msg, остальные идут новыми сообщениями.
    """
    chunks = []
    current_chunk = ""
    for line in lines:
        if len(current_chunk) + len(line) + 1 > max_len:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = line
        else:
            current_chunk += ("\n" + line) if current_chunk else line
            
    if current_chunk:
        chunks.append(current_chunk)
        
    for i, chunk in enumerate(chunks):
        if i == 0:
            await status_msg.edit_text(chunk, parse_mode="HTML")
        else:
            await message.answer(chunk, parse_mode="HTML")

def get_main_menu() -> ReplyKeyboardMarkup:
    """Генерирует постоянное нижнее меню для менеджеров."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус групп"), KeyboardButton(text="🟢 Свободные места")],
            [KeyboardButton(text="🔍 Найти группу (Пошаговый фильтр)")],
            [KeyboardButton(text="⏳ Лист ожидания")],
            [KeyboardButton(text="📅 Отчёт за сегодня"), KeyboardButton(text="🏢 Рейтинг филиалов")],
            [KeyboardButton(text="👨‍💼 Статистика менеджера")],
            [KeyboardButton(text="➕ Назначить из ожидания"), KeyboardButton(text="🔄 Перевести ученика")],
            [KeyboardButton(text="❌ Отменить запись")]
        ],
        resize_keyboard=True
    )

@router.message(Command("start", "help"))
async def cmd_start(message: Message):
    text = (
        "👋 Привет! Я — Asalya, Telegram-бот для автоматизации записи в Newton Academy.\n\n"
        "Вы можете использовать команды меню или удобные кнопки внизу экрана."
    )
    await message.reply(text, reply_markup=get_main_menu(), parse_mode="HTML")

# Роутинг текстовых кнопок на существующие команды
@router.message(F.text == "📊 Статус групп")
async def btn_groups(message: Message):
    await message.reply("Укажите филиал. Например: /groups Ракат")

@router.message(F.text == "🟢 Свободные места")
async def btn_free(message: Message):
    await message.reply("Укажите филиал. Например: /free Ракат")


@router.message(F.text == "⏳ Лист ожидания")
async def btn_waiting(message: Message):
    await cmd_waiting(message)

@router.message(F.text == "📅 Отчёт за сегодня")
async def btn_today(message: Message):
    await cmd_today(message)

@router.message(F.text == "🏢 Рейтинг филиалов")
async def btn_fill(message: Message):
    await cmd_fill(message)

@router.message(F.text == "👨‍💼 Статистика менеджера")
async def btn_manager(message: Message):
    await message.reply("Укажите имя менеджера. Например: /manager Шабнам")

@router.message(F.text == "❌ Отменить запись")
async def btn_cancel(message: Message):
    await message.reply("Использование: /cancel Имя Телефон\nПример: /cancel Мадина +998991234567")




@router.message(Command("groups"))
async def cmd_groups(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        branches_str = ", ".join(settings.ALL_BRANCHES)
        await message.reply(f"Укажите филиал. Например: /groups Ракат\nДоступные филиалы: {branches_str}")
        return

    branch_key = args[1].strip().lower()
    sheet_name = settings.BRANCH_MAP.get(branch_key)
    
    if not sheet_name:
         await message.reply(f"❌ Неизвестный филиал.\nДоступные: {', '.join(settings.ALL_BRANCHES)}")
         return

    status_msg = await message.reply(f"⏳ Собираю статус из таблицы <b>{sheet_name}</b>...", parse_mode="HTML")
    groups = await sheets_service.get_groups_status(sheet_name)
    
    if not groups:
        await status_msg.edit_text(f"❌ Не удалось загрузить данные или нет активных групп.")
        return

    # Группировка по смене (формат + время)
    grouped = {}
    total_free = 0
    for g in groups:
        shift_key = f"{g['format']} {g['time']}"
        if shift_key not in grouped:
            grouped[shift_key] = []
        grouped[shift_key].append(g)
        total_free += g['free']

    lines = [f"🏢 <b>{sheet_name.upper()} — Статус групп</b>\n"]
    
    for shift_key, gs in grouped.items():
        lines.append(f"<b>{shift_key}</b>")
        lines.append("─────────────────────")
        for g in gs:
            indicator = "🟢" if g["free"] > 2 else "🟡" if g["free"] > 0 else "🔴"
            free_txt = "закрыта" if g["free"] == 0 else f"1 место" if g["free"] % 10 == 1 and g["free"] != 11 else f"{g['free']} места" if 1 < g["free"] % 10 < 5 and not (11 < g["free"] < 15) else f"{g['free']} мест"
            lines.append(
                f"{indicator} <code>{g['group']:<13}</code> | {g['class']} | {g['language']} | {g['actual']}/{g['capacity']}  ({free_txt})"
            )
        lines.append("")

    lines.append(f"📈 Итого свободных мест: {total_free}")
    
    # Умная отправка с разбиением на чанки (без разрыва HTML)
    await send_lines_chunked(message, status_msg, lines)

@router.message(Command("free"))
async def cmd_free(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Укажите филиал. Например: /free Ракат")
        return

    branch_key = args[1].strip().lower()
    sheet_name = settings.BRANCH_MAP.get(branch_key)
    
    if not sheet_name:
         await message.reply(f"❌ Неизвестный филиал.\nДоступные: {', '.join(settings.ALL_BRANCHES)}")
         return

    status_msg = await message.reply(f"⏳ Ищу свободные места в <b>{sheet_name}</b>...", parse_mode="HTML")
    groups = await sheets_service.get_groups_status(sheet_name)
    free_groups = [g for g in groups if g["free"] > 0]
    
    if not free_groups:
        await status_msg.edit_text(f"🔴 В филиале {sheet_name} нет свободных мест.")
        return

    grouped = {}
    total_free = 0
    for g in free_groups:
        shift_key = f"{g['format']} {g['time']}"
        if shift_key not in grouped: grouped[shift_key] = []
        grouped[shift_key].append(g)
        total_free += g['free']

    lines = [f"🟢 <b>{sheet_name.upper()} — Свободные места</b>\n"]
    for shift_key, gs in grouped.items():
        lines.append(f"<b>{shift_key}</b>")
        lines.append("─────────────────────")
        for g in gs:
            indicator = "🟢" if g["free"] > 2 else "🟡"
            free_txt = f"осталось {g['free']}"
            lines.append(
                f"{indicator} <code>{g['group']:<13}</code> | {g['class']} | {g['language']} | {g['actual']}/{g['capacity']} ({free_txt})"
            )
        lines.append("")

    lines.append(f"Всего мест: {total_free}")
    await send_lines_chunked(message, status_msg, lines)

@router.message(Command("group"))
async def cmd_group(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Укажите группу. Например: /group PRESIDENT 2")
        return
        
    target = args[1].strip().lower()
    status_msg = await message.reply(f"⏳ Ищу информацию по группе <b>{target}</b>...", parse_mode="HTML")
    
    all_students = await sheets_service.get_students()
    
    students_in_group = []
    group_info = None
    
    for s in all_students:
        if len(s) > 9 and s[9].strip().lower() == target:
            if len(s) <= 11 or s[11] != "[ОТМЕНЕНО]":
                students_in_group.append({
                    "date": s[0],
                    "child": s[1],
                    "phone": s[3] if len(s) > 3 else "",
                    "manager": s[10] if len(s) > 10 else "?"
                })
            # Попытаться извлечь инфу 
            if not group_info:
                group_info = {"branch": s[4], "grade": s[5], "lang": s[6], "fmt": s[7], "time": s[8]}
                
    if not group_info:
        await status_msg.edit_text(f"❌ Группа <b>{target.upper()}</b> не найдена в листе ЗАПИСИ (возможно она пустая).", parse_mode="HTML")
        return
        
    lines = [
        f"🏫 <b>ГРУППА: {target.upper()}</b>",
        f"📍 Филиал: {group_info['branch']}",
        f"📚 {group_info['grade']} | 🗣 {group_info['lang']}",
        f"⏰ {group_info['fmt']} {group_info['time']}",
        f"👥 Записано: {len(students_in_group)}\n",
        f"<b>СПИСОК УЧЕНИКОВ:</b>"
    ]
    
    for i, st in enumerate(students_in_group, 1):
        lines.append(f"{i}. {st['child']} ({st['phone']}) - {st['manager']}")
        
    await send_lines_chunked(message, status_msg, lines)

@router.message(Command("manager"))
async def cmd_manager(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Укажите имя менеджера. Например: /manager Шабнам")
        return
        
    target = args[1].strip().lower()
    status_msg = await message.reply(f"⏳ Собираю статистику для <b>{target.title()}</b>...", parse_mode="HTML")
    
    all_students = await sheets_service.get_students()
    records = []
    for s in all_students:
        if len(s) > 10 and target in str(s[10]).lower():
            records.append(s)
            
    if not records:
        await status_msg.edit_text(f"❌ Записи для менеджера <b>{target.title()}</b> не найдены.", parse_mode="HTML")
        return
        
    active = [r for r in records if len(r) <= 11 or r[11] != "[ОТМЕНЕНО]"]
    cancelled = len(records) - len(active)
    
    lines = [
        f"👨‍💼 <b>МЕНЕДЖЕР: {target.title()}</b>\n",
        f"📈 Всего оформлено анкет (за всё время): {len(records)}",
        f"✅ Активных записей: {len(active)}",
        f"❌ Отменённых: {cancelled}\n",
        f"<b>ПОСЛЕДНИЕ 10 ЗАПИСЕЙ:</b>"
    ]
    
    active.sort(key=lambda x: str(x[0]), reverse=True)
    for i, r in enumerate(active[:10], 1):
        child = r[1]
        group = r[9] if len(r) > 9 else "?"
        dt = r[0]
        lines.append(f"• {child} → {group} ({dt})")
        
    await send_lines_chunked(message, status_msg, lines)

@router.message(Command("fill"))
async def cmd_fill(message: Message):
    status_msg = await message.reply(f"⏳ Считаю заполненность всех филиалов...", parse_mode="HTML")
    
    stats = []
    for t in settings.ALL_BRANCHES:
        groups = await sheets_service.get_groups_status(t)
        if groups:
            cap = sum(g["capacity"] for g in groups)
            act = sum(g["actual"] for g in groups)
            if cap > 0:
                percent = (act / cap) * 100
                stats.append((t, act, cap, percent))
                
    if not stats:
        await status_msg.edit_text("❌ Нет данных.")
        return
        
    stats.sort(key=lambda x: x[3], reverse=True)
    
    lines = ["🏢 <b>РЕЙТИНГ ФИЛИАЛОВ (ЗАПОЛНЕННОСТЬ)</b>\n"]
    for idx, (t, act, cap, percent) in enumerate(stats, 1):
        indicator = "🔴" if percent >= 95 else "🟡" if percent >= 80 else "🟢"
        lines.append(f"{idx}. {indicator} <b>{t}</b>: {act}/{cap} ({int(percent)}%)")
        
    await send_lines_chunked(message, status_msg, lines)

@router.message(Command("today"))
async def cmd_today(message: Message):
    await message.reply("⏳ Генерирую отчёт за сегодня...")
    from bot.services.scheduler import send_daily_report
    await send_daily_report(message.bot)

@router.message(Command("waiting"))
async def cmd_waiting(message: Message):
    args = message.text.split(maxsplit=1)
    status_msg = await message.reply("⏳ Загружаю лист ожидания...", parse_mode="HTML")
    
    all_waiting = await sheets_service.get_waiting()
    active_wait = [w for w in all_waiting if len(w) <= 11 or w[11] == "ожидает"]
    
    filter_txt = ""
    if len(args) >= 2:
        branch = args[1].strip().lower()
        active_wait = [w for w in active_wait if len(w) > 4 and w[4].strip().lower() == branch]
        filter_txt = f" ({branch.title()})"
        
    if not active_wait:
         await status_msg.edit_text(f"✅ В листе ожидания{filter_txt} сейчас пусто!")
         return
         
    lines = [f"⏳ <b>ЛИСТ ОЖИДАНИЯ{filter_txt} — {len(active_wait)} ученика(ов)</b>\n"]
    
    for i, w in enumerate(active_wait, 1):
        if len(w) < 11: continue
        dt, child, _, phone, branch, grade, lang, fmt, time_str, reason, manager = w[:11]
        lines.append(f"{i}. 👤 <b>{child}</b>")
        lines.append(f"   📍 {branch} | {grade} | {lang} | {fmt} {time_str}")
        lines.append(f"   📞 {phone}")
        lines.append(f"   👨‍💼 Менеджер: {manager}")
        lines.append(f"   ❓ Причина: {reason}")
        lines.append(f"   🕐 Добавлен: {dt}\n")
        
    await send_lines_chunked(message, status_msg, lines)

@router.message(Command("cancel"))
async def cmd_cancel(message: Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply("Использование: /cancel Имя Телефон\nПример: /cancel Мадина +998991234567")
        return
        
    child_name = args[1].strip()
    phone = args[2].strip()
    
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, отменить", callback_data=f"cncl_yes_{child_name}_{phone}")
    kb.button(text="❌ Нет", callback_data=f"cncl_no_{child_name}_{phone}")
    
    await message.reply(
        f"❓ Вы действительно хотите отменить запись ученика:\n👤 <b>{child_name}</b> ({phone})?\n\n(Это освободит место в группе и изменит статус в листе ЗАПИСИ)",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
