import logging
from datetime import datetime
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from config import settings
from bot.services.google_sheets import sheets_service

logger = logging.getLogger(__name__)

async def send_daily_report(bot: Bot):
    """Отправка ежедневного отчёта в 18:00 по Ташкенту."""
    try:
        report_date = datetime.now(pytz.timezone("Asia/Tashkent")).strftime("%Y-%m-%d")
        
        # Получаем статистику по всем филиалам
        lines = [f"📋 <b>Ежедневный отчёт — {report_date}</b>\n"]
        total_enrolled = 0
        
        # Статистика по филиалам
        # В реальной системе тут нужно пройтись по всем листам и подсчитать общие актуальные данные.
        for t in settings.ALL_BRANCHES:
            groups = await sheets_service.get_groups_status(t)
            if groups:
                total_capacity = sum(g["capacity"] for g in groups)
                total_actual = sum(g["actual"] for g in groups)
                if total_capacity > 0:
                    percent = int((total_actual / total_capacity) * 100)
                    free = total_capacity - total_actual
                    
                    indicator = "🟢" if percent < 80 else "🟡" if percent < 95 else "🔴"
                    lines.append(f"{indicator} <b>{t}</b>: {total_actual}/{total_capacity} ({percent}%)  свободно: {free}")
        
        # Читаем студентов, записанных сегодня
        students_today = []
        all_students = await sheets_service.get_students()
        for s in all_students:
            if len(s) > 0 and s[0].startswith(report_date):
                students_today.append(s)
                
        total_enrolled = len(students_today)
        
        # Читаем ожидающих сегодня
        waiting_today = []
        all_waiting = await sheets_service.get_waiting()
        for w in all_waiting:
            if len(w) > 0 and w[0].startswith(report_date) and (len(w) <= 11 or w[11] == "ожидает"):
                 waiting_today.append(w)
                 
        total_waiting = len(waiting_today)
        
        lines.insert(1, f"✅ Записано сегодня: {total_enrolled}")
        lines.insert(2, f"⏳ В ожидании сегодня: {total_waiting}\n")
        
        if total_enrolled > 0:
            lines.append("\n<b>Записаны сегодня:</b>")
            for s in students_today:
                if len(s) >= 10:
                    child, branch, lang, fmt, time, group = s[1], s[4], s[6], s[7], s[8], s[9]
                    lines.append(f"• {child} → {group} ({branch}) | {lang} | {fmt} {time}")

        report_text = "\n".join(lines)
        
        await bot.send_message(
            chat_id=settings.REPORT_CHAT_ID,
            text=report_text,
            parse_mode="HTML"
        )
        logger.info("Daily report sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send daily report: {e}")

def create_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Tashkent"))
    # Запуск каждый день в 18:00
    scheduler.add_job(
        send_daily_report, 
        'cron', 
        hour=settings.DAILY_REPORT_HOUR, 
        minute=settings.DAILY_REPORT_MINUTE, 
        args=[bot]
    )
    return scheduler
