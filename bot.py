"""
main.py — главный файл Telegram-бота Newton Academy.

Запуск локально:
    python bot.py
"""

import asyncio
import logging
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.types import Update

from config import settings
from bot.services.google_sheets import sheets_service
from bot.services.scheduler import create_scheduler

from bot.handlers.commands import router as commands_router
from bot.handlers.anketa import router as anketa_router
from bot.handlers.callbacks import router as callbacks_router
from bot.handlers.fsm_search import router as fsm_search_router
from bot.handlers.fsm_transfer import router as fsm_transfer_router
from bot.handlers.fsm_waitlist import router as fsm_waitlist_router

logger = logging.getLogger(__name__)

class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Update, data):
        if not settings.ALLOWED_USERS:
            return await handler(event, data)
            
        user_id = None
        if event.message:
            user_id = event.message.from_user.id
        elif event.callback_query:
            user_id = event.callback_query.from_user.id
            
        if user_id and user_id not in settings.ALLOWED_USERS:
             # Бот полностью игнорирует сообщения от чужих юзеров
            return None
            
        return await handler(event, data)

async def on_startup(bot: Bot):
    logger.info("Initializing auxiliary sheets (with retry)...")
    try:
        await sheets_service.ensure_aux_sheets()
    except Exception as e:
        logger.error(f"Failed to ensure sheets on startup due to API limits. Ignoring: {e}")
        
    logger.info("Starting APScheduler...")
    scheduler = create_scheduler(bot)
    scheduler.start()
    
    # Регистрация меню команд (синяя кнопка)
    from aiogram.types import BotCommand
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="groups", description="Статус групп"),
        BotCommand(command="free", description="Свободные места"),
        BotCommand(command="group", description="Найти группу"),
        BotCommand(command="waiting", description="Лист ожидания"),
        BotCommand(command="fill", description="Рейтинг филиалов"),
        BotCommand(command="today", description="Отчёт за сегодня"),
        BotCommand(command="manager", description="Сводка по менеджеру"),
        BotCommand(command="cancel", description="Отменить запись"),
        BotCommand(command="transfer", description="Перевести ученика (FSM)"),
        BotCommand(command="resolve_wait", description="Назначить из ожидания (FSM)"),
    ]
    await bot.set_my_commands(commands)
    
    logger.info("Newton Academy Bot is starting up!")

async def main():
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    
    dp.update.outer_middleware(AccessMiddleware())
    
    dp.include_router(anketa_router)
    dp.include_router(commands_router)
    dp.include_router(callbacks_router)
    dp.include_router(fsm_search_router)
    dp.include_router(fsm_transfer_router)
    dp.include_router(fsm_waitlist_router)
    
    dp.startup.register(on_startup)
    
    try:
         await dp.start_polling(bot)
    finally:
         logger.info("Bot stopped gracefully.")

if __name__ == "__main__":
    asyncio.run(main())
