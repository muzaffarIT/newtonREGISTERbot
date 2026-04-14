import asyncio
import logging

from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.services.google_sheets import sheets_service
from config import settings
from bot.handlers.fsm_search import make_reply_kb

logger = logging.getLogger(__name__)

router = Router()

class ResolveWaitlistFSM(StatesGroup):
    search_query = State()
    group = State()

@router.message(F.text == "➕ Назначить из ожидания")
async def start_waitlist_resolve(message: Message, state: FSMContext):
    await state.set_state(ResolveWaitlistFSM.search_query)
    from bot.handlers.commands import get_main_menu
    await message.reply(
        "Введите <b>Имя</b> или <b>Номер телефона</b> ученика в ЛИСТЕ ОЖИДАНИЯ.\n\n"
        "Для отмены введите /cancel_fsm",
        reply_markup=make_reply_kb(["Отмена"], adjust=1),
        parse_mode="HTML"
    )

@router.message(ResolveWaitlistFSM.search_query)
async def rw_search(message: Message, state: FSMContext):
    query = message.text.strip().lower()
    from bot.handlers.commands import get_main_menu
    if query == "отмена" or query == "/cancel_fsm":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu())
        return

    await message.reply("🔍 Поиск в Листе ожидания...", reply_markup=get_main_menu())
    
    # We iter all branches waiting lists? No, get_waiting() without branch gets all.
    waiting = await sheets_service.get_waiting()
    
    from typing import List, Dict, Any
    found: List[Dict[str, Any]] = []
    # Index format: Date(0), Child(1), Parent(2), Phone(3), Branch(4), Grade(5), Lang(6), Fmt(7), Time(8), Reason(9), Mgr(10), Status(11), RowIdx(12)
    for i, r in enumerate(waiting):
        if len(r) > 11 and r[11] == "ЗАВЕРШЕН":
             continue
        if len(r) > 3:
            child = r[1].lower()
            phone = r[3].replace(" ", "").replace("-", "").replace("+", "").lower()
            q_phone = query.replace(" ", "").replace("-", "").replace("+", "").lower()
            
            if query in child or q_phone in phone:
                found.append({"idx": i, "row": r})
                
    if not found:
        await message.reply("❌ В листе ожидания не найден ученик по такому запросу.")
        return
        
    if len(found) > 1:
         text = "⚠️ <b>Найдено несколько учеников:</b>\n\n"
         for f in found[:5]:
             r = f["row"]
             text += f"• {r[1]} ({r[3]}) — Ждал: {r[4]} / {r[5]}\n"
         await message.reply(text + "\nУточните запрос.", parse_mode="HTML")
         return
         
    target = found[0]
    r = target["row"]
    child_name = r[1]
    phone = r[3]
    branch = r[4]
    
    await state.update_data(
        wait_row=r,
        branch=branch,
        child=child_name,
        phone=phone
    )
    
    # load groups for branch
    sheet_name = settings.BRANCH_MAP.get(branch.lower())
    if not sheet_name:
         await state.clear()
         await message.reply(f"В листе ожидания указан неверный филиал: {branch}")
         return
         
    groups = await sheets_service.get_groups_status(sheet_name)
    if not groups:
         await state.clear()
         await message.reply("❌ В филиале нет групп.")
         return
         
    g_names = []
    for g in groups:
        if g['free'] > 0:
            title = f"{g['group']} ({g['actual']}/{g['capacity']})"
            g_names.append(title)
            
    if not g_names:
         await state.clear()
         await message.reply(f"❌ В филиале {branch} нет свободных мест ни в одной группе!")
         return
         
    await state.update_data(groups_cache=groups)
    await state.set_state(ResolveWaitlistFSM.group)
    
    await message.reply(
        f"✅ <b>В листе ожидания найден:</b>\n{child_name} ({phone})\n"
        f"Филиал заявки: {branch}\n\n"
        f"Выберите свободную группу для зачисления:",
        reply_markup=make_reply_kb(g_names + ["Отмена"], adjust=1),
        parse_mode="HTML"
    )

@router.message(ResolveWaitlistFSM.group)
async def rw_group(message: Message, state: FSMContext):
    text = message.text.strip()
    from bot.handlers.commands import get_main_menu
    if text == "Отмена":
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu())
        return

    data = await state.get_data()
    groups_cache = data["groups_cache"]
    wait_row = data["wait_row"]
    
    import re
    match = re.match(r"^(.*?)\s*\(\d+/\d+\)$", text)
    if match:
        target_name = match.group(1).strip()
    else:
        target_name = text
        
    target_group = next((g for g in groups_cache if g["group"] == target_name), None)
    if not target_group:
         await message.reply("❌ Группа не найдена, пожалуйста выберите из меню кнопок.")
         return
         
    # Update waitlist status — mark as ЗАВЕРШЕН in the waiting sheet
    # We use asyncio.to_thread to avoid blocking the event loop
    def _mark_wait_done():
        import gspread
        from gspread import utils as gutils
        _ws = sheets_service._sync._spreadsheet().worksheet(settings.WAITING_SHEET)
        actual_rows = _ws.get_all_values()
        found = -1
        for i, row in enumerate(actual_rows):
            if len(row) > 3 and row[1] == wait_row[1] and row[3] == wait_row[3]:
                if len(row) > 11 and row[11] != "ЗАВЕРШЕН":
                    found = i
                    break
        if found == -1:
            return False
        _ws.update_cell(found + 1, 12, "ЗАВЕРШЕН")
        return True

    resolved = await asyncio.to_thread(_mark_wait_done)
    if not resolved:
        await state.clear()
        await message.reply("Ученик уже обработан или удален из Листа Ожидания.", reply_markup=get_main_menu())
        return

    # Process enroll — build Anketa with ALL required fields
    from bot.utils.parser import Anketa
    anketa = Anketa(
        request_type="новый",
        sent_at="",
        period="",
        child=wait_row[1],
        parent=wait_row[2] if len(wait_row) > 2 else "[ОЖИДАНИЕ]",
        phone=wait_row[3],
        branch=wait_row[4],
        grade=target_group["class"],
        language=target_group["language"],
        fmt=target_group["format"],
        time=target_group["time"],
        manager=message.from_user.first_name
    )
    
    res = await sheets_service.process_anketa(anketa)
    status = res.get("status")
    await state.clear()
    
    if status == "enrolled":
         await message.reply(f"✅ Успешно переведен из ожидания в группу <b>{target_group['group']}</b>!", reply_markup=get_main_menu(), parse_mode="HTML")
    else:
         await message.reply(f"⚠️ Возникла проблема при зачислении: {status}. Возможно место заняли.", reply_markup=get_main_menu())
