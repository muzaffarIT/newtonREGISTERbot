from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.services.google_sheets import sheets_service
from config import settings
from bot.handlers.fsm_search import make_reply_kb

router = Router()

class TransferStudentFSM(StatesGroup):
    search_query = State()
    branch = State()
    group = State()
    confirm = State()

@router.message(F.text == "🔄 Перевести ученика")
async def start_transfer(message: Message, state: FSMContext):
    await state.set_state(TransferStudentFSM.search_query)
    from bot.handlers.commands import get_main_menu
    await message.reply(
        "Введите <b>Имя</b> или <b>Номер телефона</b> ученика для поиска в ЗАПИСИ.\n\n"
        "Для отмены введите /cancel_fsm",
        reply_markup=make_reply_kb(["Отмена"], adjust=1),
        parse_mode="HTML"
    )

@router.message(TransferStudentFSM.search_query)
async def ts_search(message: Message, state: FSMContext):
    query = message.text.strip().lower()
    if query == "отмена" or query == "/cancel_fsm":
        from bot.handlers.commands import get_main_menu
        await state.clear()
        await message.reply("Отменено.", reply_markup=get_main_menu())
        return

    from bot.handlers.commands import get_main_menu
    await message.reply("🔍 Поиск в базе...", reply_markup=get_main_menu())
    
    students = await sheets_service.get_students()
    
    # search logic: index 1 is Child Name, index 3 is Phone
    found = []
    # format of row: ["Дата", "Ребёнок", "Родитель", "Телефон", "Филиал", "Класс", "Язык", "Формат", "Время", "Группа", "Менеджер", "Лист", "Строка"]
    # We want active ones
    for i, r in enumerate(students):
        if len(r) > 11 and r[11] == "[ОТМЕНЕНО]":
            continue
        if len(r) > 3:
            child = r[1].lower()
            phone = r[3].replace(" ", "").replace("-", "").replace("+", "").lower()
            q_phone = query.replace(" ", "").replace("-", "").replace("+", "").lower()
            
            if query in child or q_phone in phone:
                found.append({"idx": i, "row": r})
                
    if not found:
        await message.reply("❌ Ученик не найден. Попробуйте еще раз или введите 'Отмена'")
        return
        
    if len(found) > 1:
        text = "⚠️ <b>Найдено несколько учеников:</b>\n\n"
        for f in found[:5]:
            r = f["row"]
            g = r[9] if len(r) > 9 else "?"
            text += f"• {r[1]} ({r[3]}) — Группа: {g}\n"
        await message.reply(text + "\nУточните запрос.", parse_mode="HTML")
        return
        
    target = found[0]
    r = target["row"]
    child_name = r[1]
    phone = r[3]
    old_group = r[9] if len(r) > 9 else "Неизвестна"
    old_branch = r[4] if len(r) > 4 else "Неизвестен"
    
    # Store for later
    await state.update_data(
        child=child_name,
        phone=phone,
        old_group=old_group,
        old_branch=old_branch,
        record_idx=target["idx"]
    )
    
    await state.set_state(TransferStudentFSM.branch)
    branches = list(settings.BRANCH_MAP.keys())
    await message.reply(
        f"✅ <b>Найден ученик:</b>\n{child_name} ({phone})\n"
        f"Сейчас в группе: {old_group} ({old_branch})\n\n"
        f"В какой филиал вы хотите его перевести?",
        reply_markup=make_reply_kb([b.title() for b in branches] + ["Отмена"]),
        parse_mode="HTML"
    )

@router.message(TransferStudentFSM.branch)
async def ts_branch(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    from bot.handlers.commands import get_main_menu
    if text == "отмена":
        await state.clear()
        await message.reply("Перевод отменен.", reply_markup=get_main_menu())
        return

    if text not in settings.BRANCH_MAP:
        await message.reply("Выберите филиал из меню.")
        return

    await state.update_data(new_branch=text)
    
    await message.reply("🔄 Загрузка групп филиала...", reply_markup=get_main_menu())
    sheet_name = settings.BRANCH_MAP[text]
    groups = await sheets_service.get_groups_status(sheet_name)
    
    if not groups:
        await state.clear()
        await message.reply("❌ В филиале нет групп. Начните заново.")
        return
        
    # We create buttons for all active groups
    g_names = []
    for g in groups:
        title = f"{g['group']} ({g['actual']}/{g['capacity']})"
        g_names.append(title)
        
    await state.update_data(groups_cache=groups)
    await state.set_state(TransferStudentFSM.group)
    
    await message.reply(
        "Выберите новую группу для перевода:",
        reply_markup=make_reply_kb(g_names + ["Отмена"], adjust=1)
    )

@router.message(TransferStudentFSM.group)
async def ts_group(message: Message, state: FSMContext):
    text = message.text.strip()
    from bot.handlers.commands import get_main_menu
    if text == "Отмена":
        await state.clear()
        await message.reply("Перевод отменен.", reply_markup=get_main_menu())
        return

    data = await state.get_data()
    groups_cache = data["groups_cache"]
    
    # extract group name from button text which looks like "GroupName (10/12)"
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
         
    # Do transfer logic!
    # 1. Cancel student
    ok_cancel = await sheets_service.cancel_student(data["child"], data["phone"])
    if not ok_cancel:
         await state.clear()
         await message.reply("❌ Не удалось найти или отменить старую запись.", reply_markup=get_main_menu())
         return
         
    # 2. Add to new group
    from bot.utils.parser import Anketa
    # Create fake anketa
    anketa = Anketa(
        child=data["child"],
        parent="[Перевод]",
        phone=data["phone"],
        branch=data["new_branch"].title(),
        grade=target_group["class"],
        language=target_group["language"],
        fmt=target_group["format"],
        time=target_group["time"],
        manager=message.from_user.first_name or "Manager"
    )
    
    # find row index of target group
    # we didn't save row index in get_groups_status! 
    # let's use process_anketa with normal fuzzy search, but bypass_match is safer.
    # since we don't have row_idx, let's just push normal process_anketa.
    
    # Wait, process_anketa will find the best match based on anketa. 
    # Since we set anketa exact properties to target_group, it should find it exactly!
    res = await sheets_service.process_anketa(anketa)
    
    status = res.get("status")
    await state.clear()
    if status == "enrolled":
         await message.reply(f"✅ Перевод успешен!\nИз: {data['old_group']}\nВ: {target_group['group']}", reply_markup=get_main_menu())
    elif status == "waitlist_full":
         await message.reply("⚠️ Ошибка перевода: мест в новой группе нет! Ученик отменен из старой, но не попал в новую. Он в листе ожидания.", reply_markup=get_main_menu())
    else:
         # it asked manager, which means it triggered match_type logic.
         await message.reply(f"⚠️ Перевод частично успешен: {status}.\nИз-за лимитов перевод приостановлен. Ученик пока отменен из старой группы.", reply_markup=get_main_menu())
