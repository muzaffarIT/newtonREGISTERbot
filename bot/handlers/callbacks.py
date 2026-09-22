import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery
from bot.services.google_sheets import sheets_service
from bot.utils.parser import Anketa

router = Router()
logger = logging.getLogger(__name__)

@router.callback_query(F.data.startswith("resolve_"))
async def handle_duplicate_resolution(callback: CallbackQuery):
    """Answers duplicate warning buttons"""
    data_parts = callback.data.split("_")
    if len(data_parts) < 3: return
    uuid_str = data_parts[1]
    action = "_".join(data_parts[2:])

    await callback.message.edit_reply_markup(reply_markup=None)

    pending = await sheets_service.get_pending_request(uuid_str)
    if not pending:
        await callback.message.reply("❌ Данные устарели или заявка уже обработана.")
        return

    anketa_data = pending["data"]["anketa"]
    anketa = Anketa(**anketa_data)

    if action == "cancel":
        await sheets_service.resolve_pending_request(uuid_str, "отменено")
        await callback.message.reply(f"❌ Запись дубликата отменена ({anketa.child}).")
    elif action == "force_enroll":
        await callback.message.reply(f"⏳ Отправляю {anketa.child} на поиск группы (Игнорируем дубль)...")
        from bot.handlers.anketa import _search_and_process
        await sheets_service.resolve_pending_request(uuid_str, "выполнено")
        await _search_and_process(callback.bot, callback.message.chat.id, anketa)


@router.callback_query(F.data.startswith("resc_"))
async def handle_match_resolution(callback: CallbackQuery):
    """Answers match type 2,3,4,5 buttons"""
    data_parts = callback.data.split("_")
    if len(data_parts) < 3: return
    uuid_str = data_parts[1]
    action = data_parts[2]

    await callback.message.edit_reply_markup(reply_markup=None)

    pending = await sheets_service.get_pending_request(uuid_str)
    if not pending:
        await callback.message.reply("❌ Заявка устарела или уже обработана.")
        return

    anketa_data = pending["data"]["anketa"]
    candidates = pending["data"]["candidates"]
    anketa = Anketa(**anketa_data)

    if action == "wait":
        await sheets_service.log_waiting(anketa, "Менеджер отправил в ожидание")
        await sheets_service.resolve_pending_request(uuid_str, "в_ожидании")
        await callback.message.reply(f"📋 {anketa.child} отправлен в лист ожидания.")
    elif action == "c":
        # Force enroll in a specific candidate index
        row_idx = int(data_parts[3])
        target_candidate = next((c for c in candidates if c["row_index"] == row_idx), None)
        if not target_candidate:
            await callback.message.reply("❌ Ошибка: кандидат не найден в кэше.")
            return
            
        await callback.message.reply(f"⏳ Записываем {anketa.child} в {target_candidate['group']}...")
        result = await sheets_service.process_anketa(anketa, bypass_match=target_candidate)
        
        status = result.get("status")
        if status == "waitlist_full":
            reason = f"Нет мест в {target_candidate['group']}"
            await sheets_service.log_waiting(anketa, reason)
            await sheets_service.resolve_pending_request(uuid_str, "в_ожидании")
            from bot.utils.messages import msg_waitlist
            await callback.message.reply(msg_waitlist(anketa, reason), parse_mode="HTML")
        elif status == "enrolled":
            await sheets_service.resolve_pending_request(uuid_str, "выполнено")
            from bot.utils.messages import msg_enrolled, msg_80_percent
            await callback.message.reply(
                msg_enrolled(anketa, target_candidate['group'], result['match']['actual'], target_candidate['capacity']),
                parse_mode="HTML"
            )
            # Проверка 80% заполненности (если нужно отправлять алерт и отсюда)
            if target_candidate['capacity'] > 0 and (result['match']['actual'] / target_candidate['capacity']) * 100 >= 80:
                await callback.bot.send_message(
                    chat_id=callback.message.chat.id,
                    text=msg_80_percent(
                        target_candidate['group'], anketa.branch, result['match']['actual'], target_candidate['capacity'],
                        result['match'].get('class', anketa.grade), result['match'].get('language', anketa.language),
                        result['match'].get('format', anketa.fmt), result['match'].get('time', anketa.time)
                    ),
                    parse_mode="HTML"
                )
        else:
            await callback.message.reply("❌ Ошибка при записи (API error).")

@router.callback_query(F.data.startswith("cncl_"))
async def handle_cancel_confirmation(callback: CallbackQuery):
    """Confirms student cancellation (UUID-based, payload stored in PENDING_DB)."""
    data_parts = callback.data.split("_", 2)
    if len(data_parts) < 3:
        return
    action = data_parts[1]
    uuid_str = data_parts[2]

    # Снимаем «часики» с кнопки сразу: дальше идут обращения к Google Sheets,
    # которые занимают несколько секунд.
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)

    if action == "no":
        await callback.message.reply("🔙 Отмена прервана.")
        return

    if action == "yes":
        pending = await sheets_service.get_pending_request(uuid_str)
        if not pending:
            await callback.message.reply("❌ Заявка устарела или уже обработана.")
            return
        child_name = pending["data"].get("child", "")
        phone = pending["data"].get("phone", "")

        await callback.message.reply(f"⏳ Отменяю запись {child_name}...")
        ok = await sheets_service.cancel_student(child_name, phone)
        await sheets_service.resolve_pending_request(uuid_str, "выполнено" if ok else "не_найдено")
        if ok:
            await callback.message.reply(
                f"✅ Ученик <b>{child_name}</b> ({phone}) успешно отменён.\nМесто освобождено.",
                parse_mode="HTML"
            )
        else:
            await callback.message.reply(
                f"❌ Не удалось отменить {child_name} ({phone}). Возможно он уже отменен или данные устарели.",
                parse_mode="HTML"
            )
