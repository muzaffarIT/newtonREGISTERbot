"""
main.py — главный файл Telegram-бота Newton Academy.

Запуск локально:
    python bot.py
"""

import asyncio
import logging
from aiogram import Bot, Dispatcher

from config import settings
from bot.services.google_sheets import sheets_service
from bot.services.scheduler import create_scheduler

from bot.handlers.commands import router as commands_router
from bot.handlers.anketa import router as anketa_router
from bot.handlers.callbacks import router as callbacks_router

logger = logging.getLogger(__name__)

async def on_startup(bot: Bot):
    logger.info("Initializing auxiliary sheets (with retry)...")
    try:
        await sheets_service.ensure_aux_sheets()
    except Exception as e:
        logger.error(f"Failed to ensure sheets on startup due to API limits. Ignoring: {e}")
        
    logger.info("Starting APScheduler...")
    scheduler = create_scheduler(bot)
    scheduler.start()
    
    logger.info("Newton Academy Bot is starting up!")

async def main():
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    
    dp.include_router(anketa_router)
    dp.include_router(commands_router)
    dp.include_router(callbacks_router)
    
    dp.startup.register(on_startup)
    
    try:
         await dp.start_polling(bot)
    finally:
         logger.info("Bot stopped gracefully.")

if __name__ == "__main__":
    asyncio.run(main())
