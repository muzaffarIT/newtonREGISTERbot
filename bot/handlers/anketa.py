import logging
import asyncio
from aiogram import Router, F
from aiogram.types import Message

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

    # Защита от дубликатов (например, дважды переслали одно сообщение)
    signature = f"{anketa.child}_{anketa.branch}_{anketa.time}_{anketa.grade}"
    if anketa_deduplicator.is_duplicate(signature):
        logger.warning(f"Duplicate anketa blocked: {signature}")
        await message.reply("⚠️ Эта анкета уже недавно обрабатывалась.")
        return

    branch_key = anketa.branch.strip().lower()
    sheet_name = settings.BRANCH_MAP.get(branch_key)

    # ── Неизвестный филиал ──────────────────────────────
    if not sheet_name:
        await message.reply(
            f"⚠️ <b>Неизвестный филиал:</b> {anketa.branch}\n"
            f"Доступные: {', '.join(settings.ALL_BRANCHES)}",
            parse_mode="HTML",
        )
        return

    processing_msg = await message.reply(
        f"🔍 Обрабатываю анкету (защита от гонки включена)...\n"
        f"👤 <b>{anketa.child}</b> | {anketa.branch} | {anketa.grade} | {anketa.language} | {anketa.fmt} {anketa.time}",
        parse_mode="HTML",
    )

    try:
        # Умное зачисление через сервис
        result = await sheets_service.process_anketa(anketa)
        status = result.get("status")

        if status == "waitlist_no_group":
            await processing_msg.edit_text(
                f"❌ <b>Подходящая группа не найдена</b>\n\n"
                f"👤 {anketa.child} ({anketa.parent})\n"
                f"📍 Филиал: {anketa.branch}\n"
                f"📚 Класс: {anketa.grade}  |  🗣 {anketa.language}\n"
                f"⏰ {anketa.fmt} | {anketa.time}\n"
                f"📞 {anketa.phone}\n\n"
                f"📋 Добавлен в <b>лист ожидания</b>",
                parse_mode="HTML",
            )
        elif status == "waitlist_full":
            match = result["match"]
            await processing_msg.edit_text(
                f"⚠️ <b>Группа найдена, но мест нет</b>\n\n"
                f"👤 {anketa.child} ({anketa.parent})\n"
                f"🏫 Группа: <b>{match['group']}</b>\n"
                f"📊 Заполненность: {match['actual']}/{match['capacity']} (мест нет)\n"
                f"⏰ {match['format']} | {match['time']}\n\n"
                f"📋 Добавлен в <b>лист ожидания</b>",
                parse_mode="HTML",
            )
        elif status == "enrolled":
            match = result["match"]
            time_note = "" if match["time_exact"] else f"\n⚠️ Точное время не совпало, записан в <b>{match['time']}</b>"
            await processing_msg.edit_text(
                f"✅ <b>Ученик успешно записан!</b>\n\n"
                f"👤 <b>{anketa.child}</b> ({anketa.parent})\n"
                f"📞 {anketa.phone}\n"
                f"📍 Филиал: {anketa.branch}\n"
                f"🏫 Группа: <b>{match['group']}</b>\n"
                f"📚 Класс: {anketa.grade}  |  🗣 {match['language']}\n"
                f"⏰ {match['format']} | {match['time']}\n"
                f"📊 Мест занято: {match['actual']}/{match['capacity']}\n"
                f"👨‍💼 Менеджер: {anketa.manager}"
                f"{time_note}",
                parse_mode="HTML",
            )
        elif status == "enroll_error":
            await processing_msg.edit_text(
                "❌ <b>Ошибка записи в таблицу.</b>\n"
                "Проверьте права доступа сервисного аккаунта или доступность Google Sheets API.",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Failed to process anketa completely: {e}")
        await processing_msg.edit_text(
            f"❌ <b>Системная ошибка Google API:</b>\n"
            f"Мы пытались отправить запрос несколько раз, но Google не ответил. Попробуйте еще раз позже.",
            parse_mode="HTML"
        )
