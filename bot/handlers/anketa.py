import logging
import asyncio
import uuid
import json
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from bot.utils.parser import parse_anketa
from bot.utils.deduplicator import anketa_deduplicator
from bot.services.google_sheets import sheets_service

router = Router()
logger = logging.getLogger(__name__)

@router.message(F.text.contains("Анкета Newton Academy"))
async def handle_anketa(message: Message):
    """Срабатывает когда в группу приходит анкета."""
    anketa = parse_anketa(message.text or "")
    if not anketa:
        return

    report_chat_id = settings.REPORT_CHAT_ID

    if anketa.request_type.strip().lower() != "новый":
        await message.bot.send_message(
            chat_id=report_chat_id,
            text=f"⏭ <b>Пропуск анкеты</b> (Тип: {anketa.request_type})\n👤 {anketa.child}",
            parse_mode="HTML"
        )
        return

    # Защита от дубликатов сообщений (Telegram retry)
    signature = f"{anketa.child}_{anketa.branch}_{anketa.time}_{anketa.grade}"
    if anketa_deduplicator.is_duplicate(signature):
        logger.warning(f"Duplicate anketa message blocked: {signature}")
        return

    branch_key = anketa.branch.strip().lower()
    sheet_name = settings.BRANCH_MAP.get(branch_key)

    if not sheet_name:
        await message.bot.send_message(
            chat_id=report_chat_id,
            text=f"⚠️ <b>Неизвестный филиал:</b> {anketa.branch}\nУченик: {anketa.child}",
            parse_mode="HTML",
        )
        return

    # Защита от дублей по Листу Записи
    is_duplicate_db = await sheets_service.check_duplicate(anketa.child, anketa.phone)
    if is_duplicate_db:
        # Ask manager using inline buttons
        uuid_str = str(uuid.uuid4())
        await sheets_service.save_pending_request(uuid_str, "duplicate", {"anketa": anketa.__dict__})
        
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Да, записать", callback_data=f"resolve_{uuid_str}_force_enroll")
        kb.button(text="❌ Нет (отмена)", callback_data=f"resolve_{uuid_str}_cancel")
        
        await message.bot.send_message(
            chat_id=report_chat_id,
            text=f"⚠️ <b>Дубль!</b>\n\n👤 {anketa.child} ({anketa.parent})\n📞 {anketa.phone}\n\nЭтот ученик уже есть в листе ЗАПИСИ. Всё равно отправить на поиск группы?",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
        return

    # Нормальный флоу
    await _search_and_process(message.bot, report_chat_id, anketa)


async def _search_and_process(bot, chat_id, anketa):
    processing_msg = await bot.send_message(
        chat_id=chat_id,
        text=f"🔍 <b>Поиск группы...</b>\n👤 {anketa.child} | {anketa.branch} | {anketa.grade} | {anketa.language} | {anketa.fmt} {anketa.time}",
        parse_mode="HTML",
    )

    try:
        result = await sheets_service.process_anketa(anketa)
        status = result.get("status")

        if status == "waitlist_no_group":
            await sheets_service.log_waiting(anketa, "Нет подходящей группы")
            await processing_msg.edit_text(
                f"❌ <b>Подходящая группа не найдена</b>\n\n👤 {anketa.child}\n📍 {anketa.branch} | 📚 {anketa.grade} | {anketa.language}\n📋 Добавлен в <b>лист ожидания</b>",
                parse_mode="HTML",
            )
        elif status == "waitlist_full":
            match = result.get("match", {})
            group_name = match.get('group', 'Неизвестно')
            await sheets_service.log_waiting(anketa, f"Нет мест в {group_name}")
            await processing_msg.edit_text(
                f"⚠️ <b>Мест нет</b>\n\n👤 {anketa.child}\n🏫 {group_name}\n📋 Добавлен в <b>лист ожидания</b>",
                parse_mode="HTML",
            )
        elif status == "enrolled":
            match = result["match"]
            await processing_msg.edit_text(
                f"✅ <b>Успешно записан! {anketa.child}</b>\n🏫 Группа: <b>{match['group']}</b> ({match['actual']}/{match['capacity']})",
                parse_mode="HTML",
            )
            # Проверка 80% заполненности
            if (match['actual'] / match['capacity']) * 100 >= 80:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ <b>Группа {match['group']} почти заполнена!</b>\n({match['actual']}/{match['capacity']})",
                    parse_mode="HTML"
                )
        elif status == "enroll_error":
            await processing_msg.edit_text("❌ Ошибка записи (API error).", parse_mode="HTML")
        elif status.startswith("ask_manager_"):
            mtype = int(status.split("_")[-1])
            best = result["match"]
            uuid_str = str(uuid.uuid4())
            candidates = result["candidates"]
            
            await sheets_service.save_pending_request(uuid_str, status, {
                "anketa": anketa.__dict__, 
                "candidates": candidates
            })

            kb = InlineKeyboardBuilder()
            
            if mtype == 3: # Diff time + has space
                msg_text = f"🕐 <b>Точного времени нет</b>\n\n👤 {anketa.child}\nПросит: {anketa.time}\n\nДоступные:"
                for c in candidates[:5]:
                    if c["has_space"]:
                        kb.button(text=f"✅ {c['group']} ({c['time']})", callback_data=f"resc_{uuid_str}_c_{c['row_index']}")
                kb.button(text="❌ В ожидание", callback_data=f"resc_{uuid_str}_wait")
                kb.adjust(1)
            elif mtype == 4: # format diff (ПСП ВЧС)
                msg_text = f"🔄 <b>Формат изменён</b>\n\n{anketa.fmt} занят/нет, предлагается {best['format']} в группе {best['group']}. Записать?"
                kb.button(text="✅ Да", callback_data=f"resc_{uuid_str}_c_{best['row_index']}")
                kb.button(text="❌ В ожидание", callback_data=f"resc_{uuid_str}_wait")
                kb.adjust(2)
            elif mtype == 5: # MIX lang
                msg_text = f"🌐 <b>Язык МИКС</b>\n\n{anketa.language} заполнен/нет. Предлагается {best['language']} в {best['group']}. Записать?"
                kb.button(text="✅ Да", callback_data=f"resc_{uuid_str}_c_{best['row_index']}")
                kb.button(text="❌ В ожидание", callback_data=f"resc_{uuid_str}_wait")
                kb.adjust(2)
            else:
                msg_text = f"❓ <b>Найдено совпадение с изменениями</b>\nПредлагается: {best['group']}"
                kb.button(text="✅ Записать", callback_data=f"resc_{uuid_str}_c_{best['row_index']}")
                kb.button(text="❌ Отменить", callback_data=f"resc_{uuid_str}_wait")
                kb.adjust(2)
            
            await processing_msg.edit_text(msg_text, reply_markup=kb.as_markup(), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Failed to process anketa completely: {e}")
        await processing_msg.edit_text(
            f"❌ Системная ошибка. Попробуйте еще раз позже.",
            parse_mode="HTML"
        )
