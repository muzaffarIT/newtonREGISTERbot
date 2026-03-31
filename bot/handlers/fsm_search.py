from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.services.google_sheets import sheets_service
from bot.utils.parser import normalize_time
from config import settings

router = Router()

class FindGroupFSM(StatesGroup):
    branch = State()
    grade = State()
    lang = State()
    fmt = State()
    time = State()

def make_reply_kb(items: list, adjust: int = 2) -> ReplyKeyboardMarkup:
    # simple helper to build reply keyboard
    from aiogram.utils.keyboard import ReplyKeyboardBuilder
    builder = ReplyKeyboardBuilder()
    for item in items:
        builder.button(text=str(item))
    builder.adjust(adjust)
    return builder.as_markup(resize_keyboard=True)

# Кнопки для классов
CLASSES_LIST = [
    "1", "2", "3", "4", "5", "6", "7", "8", "9", 
    "ДТМ", "6 МИРЗО УЛУГБЕК", "6 ИБН СИНО", "ПОЧЕМУЧКА", "Не важно"
]

TIMES_POCHEMUCHKA = [
    "10:00-12:00", "11:30-13:30", "12:00-14:00", 
    "16:00-18:00", "17:30-19:30", "18:00-20:00", "Не важно"
]

TIMES_STANDARD = [
    "08:30-11:30", "11:30-14:30", "14:30-17:30", "17:30-20:30", "Не важно"
]

@router.message(F.text == "🔍 Найти группу (Пошаговый фильтр)")
async def start_find_group(message: Message, state: FSMContext):
    await state.set_state(FindGroupFSM.branch)
    branches = list(settings.BRANCH_MAP.keys())
    await message.reply(
        "Выберите филиал:", 
        reply_markup=make_reply_kb([b.title() for b in branches] + ["Отмена"])
    )

@router.message(FindGroupFSM.branch)
async def fg_branch(message: Message, state: FSMContext):
    text = message.text.strip().lower()
    if text == "отмена":
        await state.clear()
        from bot.handlers.commands import get_main_menu
        await message.reply("Поиск отменен.", reply_markup=get_main_menu())
        return

    if text not in settings.BRANCH_MAP:
        await message.reply("Неизвестный филиал. Выберите из кнопок.")
        return

    await state.update_data(branch=text)
    await state.set_state(FindGroupFSM.grade)
    await message.reply(
        "Выберите класс:", 
        reply_markup=make_reply_kb(CLASSES_LIST, adjust=3)
    )

@router.message(FindGroupFSM.grade)
async def fg_grade(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        from bot.handlers.commands import get_main_menu
        await message.reply("Отменено.", reply_markup=get_main_menu())
        return
        
    await state.update_data(grade=message.text.strip())
    await state.set_state(FindGroupFSM.lang)
    await message.reply(
        "Выберите язык:",
        reply_markup=make_reply_kb(["РУС", "УЗБ", "МИКС", "Не важно", "Отмена"])
    )

@router.message(FindGroupFSM.lang)
async def fg_lang(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        from bot.handlers.commands import get_main_menu
        await message.reply("Отменено.", reply_markup=get_main_menu())
        return
        
    await state.update_data(lang=message.text.strip())
    await state.set_state(FindGroupFSM.fmt)
    await message.reply(
        "Выберите дни (формат):",
        reply_markup=make_reply_kb(["ПСП", "ВЧС", "Не важно", "Отмена"])
    )

@router.message(FindGroupFSM.fmt)
async def fg_fmt(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        from bot.handlers.commands import get_main_menu
        await message.reply("Отменено.", reply_markup=get_main_menu())
        return
        
    await state.update_data(fmt=message.text.strip())
    data = await state.get_data()
    grade = data.get("grade", "").upper()
    
    await state.set_state(FindGroupFSM.time)
    
    times = TIMES_POCHEMUCHKA if "ПОЧЕМУЧК" in grade else TIMES_STANDARD
    await message.reply(
        "Выберите время:",
        reply_markup=make_reply_kb(times + ["Отмена"])
    )

@router.message(FindGroupFSM.time)
async def fg_time(message: Message, state: FSMContext):
    if message.text == "Отмена":
        await state.clear()
        from bot.handlers.commands import get_main_menu
        await message.reply("Отменено.", reply_markup=get_main_menu())
        return
        
    await state.update_data(time=message.text.strip())
    data = await state.get_data()
    
    branch = data["branch"]
    grade = data["grade"]
    lang = data["lang"]
    fmt = data["fmt"]
    time_val = data["time"]
    
    await state.clear()
    
    from bot.handlers.commands import get_main_menu
    await message.reply("🔍 Поиск в Гугл Таблице...", reply_markup=get_main_menu())
    
    sheet_name = settings.BRANCH_MAP[branch]
    groups = await sheets_service.get_groups_status(sheet_name)
    if not groups:
        await message.reply("❌ Не удалось получить данные о филиале.")
        return
        
    filtered = []
    # apply filters
    for g in groups:
        # grade
        class_val = str(g["class"]).upper()
        if grade.lower() != "не важно":
            if grade.upper() not in class_val and class_val not in grade.upper():
                continue
        
        # lang
        if lang.lower() != "не важно" and lang.upper() not in str(g["language"]).upper():
            continue
            
        # fmt
        if fmt.lower() != "не важно" and fmt.upper() not in str(g["format"]).upper():
            continue
            
        # time
        if time_val.lower() != "не важно":
            n_t = normalize_time(time_val)
            g_t = normalize_time(g["time"])
            if n_t != g_t:
                continue
                
        filtered.append(g)
        
    if not filtered:
        await message.reply("❌ По вашим фильтрам групп не найдено.")
        return
        
    text = f"✅ <b>Найдено групп: {len(filtered)}</b>\n\n"
    for g in filtered:
        # Use squares for visual status
        status_emoji = "🟢"
        if g['free'] <= 0:
            if g['actual'] < g['capacity']:
                 status_emoji = "⚠️" # Заморозки
            else:
                 status_emoji = "🔴"
                 
        text += (
            f"{status_emoji} <b>{g['group']}</b> ({g['class']})\n"
            f"   Язык: {g['language']} | Формат: {g['format']}\n"
            f"   Время: {g['time']}\n"
            f"   Занято: {g['actual']}, Заморожено: {g['freeze']} / Из {g['capacity']} (Свободно: {g['free']})\n\n"
        )
        
    # Split text if too long
    if len(text) > 4000:
        await message.reply("⚠️ Ограничение Telegram: выведено только начало.")
        text = text[:4000]
        
    await message.reply(text, parse_mode="HTML")
