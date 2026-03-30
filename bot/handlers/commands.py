import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from bot.services.google_sheets import sheets_service

router = Router()
logger = logging.getLogger(__name__)

@router.message(Command("start", "help"))
async def cmd_start(message: Message):
    text = (
        "👋 Привет! Я — Asalya, Telegram-бот для автоматизации записи в Newton Academy.\n\n"
        "📜 <b>Команды:</b>\n"
        "/groups [филиал] — посмотреть статус групп\n"
        "/cancel Имя Телефон — отменить запись ученика (освободить место)\n\n"
        "Я автоматически читаю анкеты и проверяю Google Sheets!"
    )
    await message.reply(text, parse_mode="HTML")

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

    lines = [f"📊 <b>Статус групп: {sheet_name}</b>\n"]
    for g in groups:
        indicator = "🟢" if g["free"] > 2 else "🟡" if g["free"] > 0 else "🔴"
        lines.append(
            f"{indicator} <b>{g['group']}</b> ({g['format']} {g['time']})\n"
            f"   Свободно: {g['free']} | Занято: {g['actual']}/{g['capacity']}"
        )

    await status_msg.edit_text("\n".join(lines), parse_mode="HTML")

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
