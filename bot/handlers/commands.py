import logging
from datetime import datetime

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from config import settings
from bot.services.google_sheets import sheets_service

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("groups"))
async def cmd_groups(message: Message):
    """
    /groups            — все филиалы
    /groups РАКАТ      — только Ракат
    """
    args = message.text.split(maxsplit=1)
    branch_arg = args[1].strip().upper() if len(args) > 1 else None
    branches = [branch_arg] if branch_arg in settings.ALL_BRANCHES else settings.ALL_BRANCHES

    lines = ["📊 <b>Статус групп</b>\n"]

    for branch in branches:
        groups = await sheets_service.get_groups_status(branch)
        if not groups:
            lines.append(f"🏢 <b>{branch}</b>: нет данных\n")
            continue

        available = [g for g in groups if g["free"] > 0]
        full      = [g for g in groups if g["free"] <= 0]

        lines.append(f"🏢 <b>{branch}</b> — групп: {len(groups)}")
        lines.append(f"   ✅ Есть места: {len(available)}  |  🔴 Закрыты: {len(full)}\n")

        for g in available:
            bar = "█" * g["actual"] + "░" * g["free"]
            lines.append(
                f"   • <b>{g['group']}</b> | {g['language']} | {g['format']} {g['time']}\n"
                f"     {bar} {g['actual']}/{g['capacity']} (свободно: {g['free']})"
            )
        lines.append("")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("waiting"))
async def cmd_waiting(message: Message):
    """
    /waiting           — весь список ожидания
    /waiting ПАРКЕНТ   — по филиалу
    """
    args = message.text.split(maxsplit=1)
    branch = args[1].strip() if len(args) > 1 else None

    waiting = await sheets_service.get_waiting(branch)

    if not waiting:
        await message.answer("📋 Лист ожидания <b>пуст</b>", parse_mode="HTML")
        return

    title = f"⏳ <b>Лист ожидания</b> — {len(waiting)} чел."
    if branch:
        title += f" ({branch.upper()})"
    lines = [title, ""]

    for i, r in enumerate(waiting[-30:], 1):   # последние 30
        lines.append(
            f"<b>{i}.</b> {r[1]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} {r[8]}\n"
            f"    📞 {r[3]}  👨‍💼 {r[10]}  🕐 {r[0]}\n"
            f"    ℹ️ {r[9]}"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("students"))
async def cmd_students(message: Message):
    """
    /students          — все записанные
    /students ГАНГА    — по филиалу
    """
    args = message.text.split(maxsplit=1)
    branch = args[1].strip() if len(args) > 1 else None

    students = await sheets_service.get_students(branch)

    if not students:
        await message.answer("📋 Записанных учеников нет", parse_mode="HTML")
        return

    title = f"👥 <b>Записанные ученики</b> — {len(students)} чел."
    if branch:
        title += f" ({branch.upper()})"
    lines = [title, ""]

    for i, r in enumerate(students[-30:], 1):
        lines.append(
            f"<b>{i}.</b> <b>{r[1]}</b> → гр. <b>{r[9]}</b>\n"
            f"    {r[4]} | {r[5]} | {r[6]} | {r[7]} {r[8]}\n"
            f"    📞 {r[3]}  👨‍💼 {r[10]}  🕐 {r[0]}"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Полная статистика по всем филиалам."""
    today = datetime.now().strftime("%Y-%m-%d")

    lines = ["📈 <b>Полная статистика</b>\n"]

    total_cap = total_act = 0

    for branch in settings.ALL_BRANCHES:
        groups = await sheets_service.get_groups_status(branch)
        if not groups:
            lines.append(f"🏢 <b>{branch}</b>: нет данных")
            continue

        cap = sum(g["capacity"] for g in groups)
        act = sum(g["actual"] for g in groups)
        free = cap - act
        pct = act / cap * 100 if cap else 0

        total_cap += cap
        total_act += act

        emoji = "🔴" if pct > 90 else "🟡" if pct > 70 else "🟢"
        lines.append(
            f"{emoji} <b>{branch}</b>: {act}/{cap} ({pct:.0f}%)  свободно: {free}"
        )

    students = await sheets_service.get_students()
    waiting  = await sheets_service.get_waiting()

    today_s = [s for s in students if s[0].startswith(today)]
    today_w = [w for w in waiting  if w[0].startswith(today)]

    total_pct = total_act / total_cap * 100 if total_cap else 0

    lines += [
        "",
        f"📊 <b>Итого:</b> {total_act}/{total_cap} ({total_pct:.0f}%)",
        f"✅ Записано сегодня: <b>{len(today_s)}</b>",
        f"⏳ В ожидании сегодня: <b>{len(today_w)}</b>",
        f"📁 Всего записей: {len(students)}  |  Всего ожидающих: {len(waiting)}",
    ]

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("search"))
async def cmd_search(message: Message):
    """
    /search Мадина   — поиск по имени ребёнка или родителя
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer("Использование: <code>/search Имя</code>", parse_mode="HTML")
        return

    q = args[1].strip().lower()
    
    students = await sheets_service.get_students()
    waiting = await sheets_service.get_waiting()

    found_s = [s for s in students if q in s[1].lower() or q in s[2].lower()]
    found_w = [w for w in waiting  if q in w[1].lower() or q in w[2].lower()]

    lines = [f"🔍 <b>Поиск:</b> «{args[1]}»\n"]

    if found_s:
        lines.append(f"✅ <b>Записан(а)</b> — {len(found_s)} рез.")
        for r in found_s:
            lines.append(
                f"• <b>{r[1]}</b> ({r[2]}) | {r[4]} | гр. {r[9]} | {r[7]} {r[8]}\n"
                f"  📞 {r[3]}  🕐 {r[0]}"
            )

    if found_w:
        lines.append(f"\n⏳ <b>В ожидании</b> — {len(found_w)} рез.")
        for r in found_w:
            lines.append(
                f"• <b>{r[1]}</b> ({r[2]}) | {r[4]} | {r[5]} {r[6]} | {r[7]} {r[8]}\n"
                f"  📞 {r[3]}  🕐 {r[0]}"
            )

    if not found_s and not found_w:
        lines.append("Ничего не найдено.")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("report"))
async def cmd_report(message: Message):
    """Отчёт за сегодня."""
    today = datetime.now().strftime("%Y-%m-%d")
    date_display = datetime.now().strftime("%d.%m.%Y")

    students = await sheets_service.get_students()
    waiting  = await sheets_service.get_waiting()

    today_s = [s for s in students if s[0].startswith(today)]
    today_w = [w for w in waiting  if w[0].startswith(today)]

    lines = [
        f"📋 <b>Ежедневный отчёт — {date_display}</b>\n",
        f"✅ Записано: <b>{len(today_s)}</b>",
        f"⏳ В ожидании: <b>{len(today_w)}</b>",
        "",
    ]

    if today_s:
        lines.append("<b>Записаны сегодня:</b>")
        for r in today_s:
            lines.append(f"• {r[1]} → <b>{r[9]}</b> ({r[4]}) | {r[6]} | {r[7]} {r[8]}  👨‍💼 {r[10]}")

    if today_w:
        lines.append("\n<b>Добавлены в ожидание:</b>")
        for r in today_w:
            lines.append(f"• {r[1]} | {r[4]} | {r[5]} {r[6]} | {r[9]}")

    if not today_s and not today_w:
        lines.append("Сегодня активности не было.")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "🤖 <b>Newton Academy Bot</b>\n\n"
        "<b>Команды:</b>\n"
        "/groups [филиал]   — статус групп с заполненностью\n"
        "/students [филиал] — список записанных учеников\n"
        "/waiting [филиал]  — лист ожидания\n"
        "/stats             — полная статистика по всем филиалам\n"
        "/search [имя]      — найти ученика по имени\n"
        "/report            — отчёт за сегодня\n"
        "/help              — эта справка\n\n"
        "<b>Филиалы:</b> РАКАТ, ПАРКЕНТ, ГАНГА, СЕРГЕЛИ, ЧИЛАНЗАР\n\n"
        "Бот автоматически обрабатывает каждую анкету, находит подходящую группу, "
        "обновляет таблицу и отправляет отчёт в чат."
    )
    await message.answer(text, parse_mode="HTML")
