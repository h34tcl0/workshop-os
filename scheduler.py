import sys
import argparse
from typing import Optional
from datetime import date, datetime, time

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
from sqlmodel import Session, select
from database import engine, create_db_and_tables
from models import Task, TaskStatus, DailyLog, DayStatus, DayEvaluation
from config import TELEGRAM_BOT_TOKEN, get_app_settings
from weather_service import OpenMeteoWeatherService, MockWeatherService
from evaluator import evaluate_day_feasibility
from holidays_service import get_holiday_dates_for_range
from calendar_service import GoogleCalendarService
from telegram_bot import TelegramBotService

def run_morning_evaluation(target_date: Optional[date] = None, mock_scenario: Optional[str] = None):
    """Executes the 04:00 AM batch calculation and updates DailyLog, Calendar, and Telegram."""
    if not target_date:
        target_date = date.today()

    print(f"[{datetime.now().isoformat()}] Running Morning Evaluation for {target_date}...")
    create_db_and_tables()

    with Session(engine) as session:
        # Leer configuración persistida (la misma que se edita desde el modal de la web)
        app_settings = get_app_settings(session)

        # Fetch pending tasks
        statement = select(Task).where(Task.status != TaskStatus.COMPLETED).order_by(Task.order)
        pending_tasks = session.exec(statement).all()

        # Get weather forecast
        if mock_scenario:
            print(f"Using Mock Weather scenario: {mock_scenario}")
            weather_svc = MockWeatherService(scenario=mock_scenario)
        else:
            weather_svc = OpenMeteoWeatherService(lat=app_settings.latitude, lon=app_settings.longitude)

        try:
            forecasts = weather_svc.get_hourly_forecast(target_date)
        except Exception as e:
            print(f"Error fetching weather forecast: {e}. Falling back to Mock Sunny scenario.")
            weather_svc = MockWeatherService(scenario="sunny")
            forecasts = weather_svc.get_hourly_forecast(target_date)

        holiday_dates = set()
        if app_settings.exclude_holidays:
            try:
                holiday_dates = get_holiday_dates_for_range(target_date, target_date)
            except Exception as e:
                print(f"Error obteniendo feriados: {e}")

        # Run Evaluator (usando la configuración persistida, no los defaults de fábrica)
        eval_result = evaluate_day_feasibility(target_date, pending_tasks, forecasts, settings=app_settings, holiday_dates=holiday_dates)

        # Check existing DailyLog for today
        statement_log = select(DailyLog).where(DailyLog.eval_date == target_date)
        daily_log = session.exec(statement_log).first()
        if not daily_log:
            daily_log = DailyLog(eval_date=target_date, status=eval_result.status)

        daily_log.status = eval_result.status
        daily_log.block_reason = eval_result.reason
        daily_log.updated_at = datetime.utcnow()

        if eval_result.status == DayStatus.DAY_VIABLE and eval_result.window:
            daily_log.window_start = eval_result.window.start_time
            daily_log.window_end = eval_result.window.end_time
            daily_log.net_work_hours = eval_result.window.net_work_hours
            daily_log.tasks_summary = ", ".join([t.title for t in eval_result.scheduled_tasks])

        session.add(daily_log)
        session.commit()
        session.refresh(daily_log)

        # External Integrations
        calendar_svc = GoogleCalendarService()
        telegram_svc = TelegramBotService()

        # 1. Google Calendar Event
        if eval_result.status == DayStatus.DAY_VIABLE and eval_result.window and not daily_log.calendar_created:
            cal_success = calendar_svc.create_workshop_event(
                target_date,
                eval_result.window.start_time,
                eval_result.window.end_time,
                eval_result.scheduled_tasks
            )
            if cal_success:
                daily_log.calendar_created = True
                session.add(daily_log)
                session.commit()

        # 2. Telegram Notification
        if not daily_log.telegram_notified:
            tg_success = telegram_svc.send_morning_evaluation(eval_result)
            if tg_success:
                daily_log.telegram_notified = True
                session.add(daily_log)
                session.commit()

    print(f"Morning Evaluation completed for {target_date}: {eval_result.status} - {eval_result.reason}")

def run_nightly_checkin():
    """Executes the 21:00 PM check-in prompt via Telegram."""
    print(f"[{datetime.now().isoformat()}] Running Nightly Check-in (21:00 PM)...")
    create_db_and_tables()

    with Session(engine) as session:
        statement = select(Task).where(Task.status == TaskStatus.IN_PROGRESS).order_by(Task.order)
        active_tasks = session.exec(statement).all()
        if not active_tasks:
            statement_pending = select(Task).where(Task.status == TaskStatus.PENDING).order_by(Task.order).limit(3)
            active_tasks = session.exec(statement_pending).all()

        telegram_svc = TelegramBotService()
        telegram_svc.send_nightly_checkin(active_tasks)

def retry_pending_notifications():
    """Retries any unnotified DailyLog entries (resilience fallback)."""
    create_db_and_tables()
    with Session(engine) as session:
        unnotified_logs = session.exec(select(DailyLog).where(DailyLog.telegram_notified == False)).all()
        if not unnotified_logs:
            return

        telegram_svc = TelegramBotService()
        for log in unnotified_logs:
            # Reconstruct evaluation summary
            dummy_eval = DayEvaluation(
                eval_date=log.eval_date,
                status=log.status,
                reason=log.block_reason or "Sin razón especificada."
            )
            if telegram_svc.send_morning_evaluation(dummy_eval):
                log.telegram_notified = True
                session.add(log)
                session.commit()

def run_daemon():
    """Runs APScheduler background process for 04:00 AM and 21:00 PM triggers."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    # 04:00 AM Morning batch
    scheduler.add_job(run_morning_evaluation, 'cron', hour=4, minute=0)
    # 21:00 PM Nightly check-in
    scheduler.add_job(run_nightly_checkin, 'cron', hour=21, minute=0)

    print("Workshop OS Independent Scheduler daemon started. Running jobs at 04:00 AM and 21:00 PM...")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler daemon stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Workshop OS Independent Scheduler CLI")
    parser.add_argument("--job", choices=["morning_eval", "night_check", "retry", "daemon"], help="Job to run")
    parser.add_argument("--mock-weather", choices=["sunny", "morning_rain", "afternoon_rain", "buffer_rain", "heavy_all_day"], help="Mock weather scenario for morning_eval")
    args = parser.parse_args()

    if args.job == "morning_eval":
        run_morning_evaluation(mock_scenario=args.mock_weather)
    elif args.job == "night_check":
        run_nightly_checkin()
    elif args.job == "retry":
        retry_pending_notifications()
    elif args.job == "daemon":
        run_daemon()
    else:
        parser.print_help()
