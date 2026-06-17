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
        # Сначала обновляем листы статистики в Google Таблицах
        try:
            await sheets_service.update_branch_statistics_sheets()
            logger.info("Branch statistics sheets updated successfully.")
        except Exception as stat_err:
            logger.error(f"Failed to update branch statistics sheets: {stat_err}")

        now_tashkent = datetime.now(pytz.timezone("Asia/Tashkent"))
        
        months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
        report_date_str = f"{now_tashkent.day} {months[now_tashkent.month-1]} {now_tashkent.year}, {now_tashkent.strftime('%H:%M')}"
        iso_date = now_tashkent.strftime("%Y-%m-%d")
        
        # Получаем статистику по всем филиалам
        branches_stats = []
        global_cap = 0
        global_act = 0
        
        for t in settings.ALL_BRANCHES:
            groups = await sheets_service.get_groups_status(t)
            if groups:
                cap = sum(g["capacity"] for g in groups)
                act = sum(g["actual"] for g in groups)
                if cap > 0:
                    percent = int((act / cap) * 100)
                    free = cap - act
                    indicator = "🟢" if percent < 80 else "🟡" if percent < 95 else "🔴"
                    
                    # Прогресс бар (10 блоков)
                    filled_blocks = min(10, int(round((act / cap) * 10)))
                    empty_blocks = 10 - filled_blocks
                    bar = "█" * filled_blocks + "░" * empty_blocks
                    
                    branches_stats.append(f"{indicator} {t.capitalize():<11} {act}/{cap:<4} {percent:>2}%  {bar}  {free} мест")
                    global_cap += cap
                    global_act += act

        # Читаем студентов, записанных и отмененных сегодня
        students_today = []
        cancelled_today = 0
        all_students = await sheets_service.get_students()
        for s in all_students:
            if len(s) > 0 and s[0].startswith(iso_date):
                if len(s) > 11 and s[11] == "[ОТМЕНЕНО]":
                    cancelled_today += 1
                else:
                    students_today.append(s)
                
        # Читаем ожидающих сегодня
        waiting_today = []
        all_waiting = await sheets_service.get_waiting()
        for w in all_waiting:
            if len(w) > 0 and w[0].startswith(iso_date) and (len(w) <= 11 or w[11] == "ожидает"):
                 waiting_today.append(w)
                 
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━",
            "📊 <b>ЕЖЕДНЕВНЫЙ ОТЧЁТ</b>",
            f"{report_date_str}",
            "━━━━━━━━━━━━━━━━━━━━━\n",
            "📝 <b>АКТИВНОСТЬ ЗА ДЕНЬ:</b>",
            f"✅ Записано: {len(students_today)} учеников",
            f"⏳ В ожидании: {len(waiting_today)} ученика",
            f"❌ Отменено: {cancelled_today}\n",
            "🏢 <b>ЗАГРУЖЕННОСТЬ ФИЛИАЛОВ:</b>"
        ]
        
        lines.extend(branches_stats)
        lines.append("─────────────────────")
        
        if global_cap > 0:
            lines.append(f"📈 Всего: {global_act}/{global_cap}  ({int((global_act/global_cap)*100)}%)\n")
        
        if students_today:
            lines.append("👥 <b>ЗАПИСАНЫ СЕГОДНЯ:</b>")
            for idx, s in enumerate(students_today, 1):
                if len(s) >= 10:
                    child, branch, lang, fmt, time, group = s[1], s[4], s[6], s[7], s[8], s[9]
                    lines.append(f"{idx}. {child} → {group} | {branch} | {lang} {fmt} {time}")

        if waiting_today:
            lines.append("\n⏳ <b>В ЛИСТЕ ОЖИДАНИЯ:</b>")
            for idx, w in enumerate(waiting_today, 1):
                if len(w) >= 9:
                    child, branch, grade, lang, fmt, time = w[1], w[4], w[5], w[6], w[7], w[8]
                    lines.append(f"{idx}. {child} | {branch} | {grade} | {lang} {fmt} {time}")

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
