import sys
import argparse
import json
from typing import Optional
from datetime import date, datetime, time, timedelta

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
from evaluator import evaluate_day_feasibility, compute_hourly_climate_map, compress_climate_segments, detect_new_weather_risk
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
        is_new_day = daily_log is None
        if not daily_log:
            daily_log = DailyLog(eval_date=target_date, status=eval_result.status)

        daily_log.status = eval_result.status
        daily_log.block_reason = eval_result.reason
        daily_log.updated_at = datetime.utcnow()

        # Snapshot del clima de esta mañana (para poder comparar más tarde si algo cambió)
        daily_log.morning_climate_snapshot = json.dumps(eval_result.climate_segments or [])

        # Solo resetear el estado de check-in/alerta si es la primera evaluación de este día
        # (si alguien vuelve a forzar la evaluación manualmente el mismo día, no reinicia nada)
        if is_new_day:
            daily_log.checkin_sent = False
            daily_log.checkin_resolved = False
            daily_log.weather_alert_sent = False
            daily_log.weather_alert_acknowledged = False
            daily_log.weather_alert_retry_count = 0
            daily_log.weather_alert_last_sent_at = None
            daily_log.weather_alert_message = None

        if eval_result.status == DayStatus.DAY_VIABLE and eval_result.window:
            daily_log.window_start = eval_result.window.start_time
            daily_log.window_end = eval_result.window.end_time
            daily_log.net_work_hours = eval_result.window.net_work_hours
            daily_log.tasks_summary = ", ".join([t.title for t in eval_result.scheduled_tasks])
            daily_log.scheduled_task_ids = json.dumps([t.id for t in eval_result.scheduled_tasks])
        else:
            daily_log.scheduled_task_ids = None

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

def run_checkin_tick(now: Optional[datetime] = None):
    """Revisa si ya llegó la hora FIJA de check-in de cierre (configurable en Ajustes,
    no depende de cuándo termina la ventana calculada — el trabajo real nunca es
    cronométrico) y manda la pregunta de cierre si corresponde."""
    now = now or datetime.now()
    create_db_and_tables()

    with Session(engine) as session:
        app_settings = get_app_settings(session)
        today = now.date()
        daily_log = session.exec(select(DailyLog).where(DailyLog.eval_date == today)).first()

        if not daily_log or daily_log.status != DayStatus.DAY_VIABLE:
            return  # día bloqueado: no hay nada que preguntar

        if daily_log.checkin_sent or daily_log.checkin_resolved:
            return  # ya se mandó (o ya se resolvió) el check-in de hoy

        if now.hour < app_settings.checkin_hour:
            return  # todavía no es la hora fija configurada

        task_ids = json.loads(daily_log.scheduled_task_ids or "[]")
        scheduled_tasks = [session.get(Task, tid) for tid in task_ids]
        scheduled_tasks = [t for t in scheduled_tasks if t and t.status != TaskStatus.COMPLETED]

        if not scheduled_tasks:
            daily_log.checkin_sent = True
            daily_log.checkin_resolved = True
            session.add(daily_log)
            session.commit()
            return

        telegram_svc = TelegramBotService()
        sent = telegram_svc.send_checkin_prompt(daily_log.id, scheduled_tasks)
        if sent:
            daily_log.checkin_sent = True
            session.add(daily_log)
            session.commit()
            print(f"[{now.isoformat()}] Check-in de cierre enviado para {today} ({len(scheduled_tasks)} tareas).")

def run_weather_alert_tick(now: Optional[datetime] = None):
    """Mientras estás DENTRO de tu ventana de trabajo de hoy, vuelve a chequear el clima
    (pensada para correr cada 60 min) y lo compara contra lo que se sabía esta mañana.
    Si aparece un riesgo nuevo (lluvia/humedad que no estaba prevista), dispara una alerta
    que se repite cada 10 min hasta que la confirmes o se agoten 6 reintentos (1 hora).
    Una vez detectado el problema, no vuelve a evaluar cambios el resto del día — la
    decisión de resguardar ya está tomada, no tiene sentido "des-alertar" si mejora."""
    now = now or datetime.now()
    create_db_and_tables()

    with Session(engine) as session:
        app_settings = get_app_settings(session)
        today = now.date()
        daily_log = session.exec(select(DailyLog).where(DailyLog.eval_date == today)).first()

        if not daily_log or daily_log.status != DayStatus.DAY_VIABLE or not daily_log.window_start or not daily_log.window_end:
            return  # día bloqueado o sin ventana: no hay nada que proteger

        if daily_log.weather_alert_acknowledged:
            return  # ya confirmaste, no se insiste más hoy

        window_start_h = daily_log.window_start.hour + daily_log.window_start.minute / 60.0
        window_end_h = daily_log.window_end.hour + daily_log.window_end.minute / 60.0
        now_h = now.hour + now.minute / 60.0

        if not (window_start_h <= now_h <= window_end_h):
            return  # fuera de tu ventana de trabajo de hoy, no hay nada que vigilar ahora mismo

        telegram_svc = TelegramBotService()

        if daily_log.weather_alert_sent:
            # Ya se detectó el problema antes; solo falta reintentar si no ha confirmado
            if daily_log.weather_alert_retry_count >= 6:
                return  # se agotó el máximo de reintentos (1 hora) — queda en tus manos
            last_sent = daily_log.weather_alert_last_sent_at
            if last_sent and (now - last_sent) < timedelta(minutes=10):
                return  # todavía no toca el próximo reintento

            sent = telegram_svc.send_weather_alert_burst(daily_log.id, daily_log.weather_alert_message or "Cambio de clima detectado.")
            if sent:
                daily_log.weather_alert_retry_count += 1
                daily_log.weather_alert_last_sent_at = now
                session.add(daily_log)
                session.commit()
                print(f"[{now.isoformat()}] Reintento {daily_log.weather_alert_retry_count}/6 de alerta de clima para {today}.")
            return

        # Todavía no se ha detectado nada: recalcular el clima ahora y comparar contra esta mañana
        try:
            weather_svc = OpenMeteoWeatherService(lat=app_settings.latitude, lon=app_settings.longitude)
            forecasts = weather_svc.get_hourly_forecast(today)
        except Exception as e:
            print(f"[WeatherAlert] Error obteniendo clima actualizado: {e}")
            return

        new_map = compute_hourly_climate_map(
            forecasts, int(window_start_h), int(window_end_h) + 1,
            app_settings.min_rain_precipitation_mm, app_settings.max_humidity_percent
        )
        new_segments = compress_climate_segments(new_map)
        old_segments = json.loads(daily_log.morning_climate_snapshot or "[]")

        risk = detect_new_weather_risk(old_segments, new_segments, window_start_h, window_end_h)
        if not risk:
            return  # todo sigue como esta mañana, nada que avisar

        sent = telegram_svc.send_weather_alert_burst(daily_log.id, risk)
        if sent:
            daily_log.weather_alert_sent = True
            daily_log.weather_alert_message = risk
            daily_log.weather_alert_retry_count = 1
            daily_log.weather_alert_last_sent_at = now
            session.add(daily_log)
            session.commit()
            print(f"[{now.isoformat()}] ALERTA de cambio de clima disparada para {today}: {risk}")

def run_morning_eval_tick(now: Optional[datetime] = None):
    """Revisa si ya llegó la hora de correr la evaluación matutina de hoy — calculada como
    (hora de inicio de tu jornada configurada) - (margen configurable, por defecto 1h antes),
    en vez de una hora fija como las 4 AM. Si ya existe un DailyLog para hoy, no hace nada
    (ya se evaluó, sea por este tick o por el botón manual de la web)."""
    now = now or datetime.now()
    create_db_and_tables()

    with Session(engine) as session:
        app_settings = get_app_settings(session)
        today = now.date()

        existing_log = session.exec(select(DailyLog).where(DailyLog.eval_date == today)).first()
        if existing_log:
            return  # hoy ya se evaluó (por este tick antes, o manualmente desde la web)

        trigger_hour = (app_settings.operational_start_hour - app_settings.morning_eval_lead_hours) % 24
        if now.hour < trigger_hour:
            return  # todavía no toca

    run_morning_evaluation(target_date=today)

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
    """Corre un único chequeo periódico (cada 5 min) que internamente decide si toca:
    evaluación matutina (según tu jornada configurada), check-in de cierre (hora fija),
    o revisión de cambio de clima (cada 60 min, solo dentro de tu ventana de trabajo)."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_job(run_morning_eval_tick, 'interval', minutes=5)
    scheduler.add_job(run_checkin_tick, 'interval', minutes=5)
    scheduler.add_job(run_weather_alert_tick, 'interval', minutes=60)

    print("Workshop OS Independent Scheduler daemon started (tick cada 5 min para eval/check-in, cada 60 min para clima).")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler daemon stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Workshop OS Independent Scheduler CLI")
    parser.add_argument("--job", choices=["morning_eval", "night_check", "weather_check", "retry", "daemon"], help="Job to run")
    parser.add_argument("--mock-weather", choices=["sunny", "morning_rain", "afternoon_rain", "buffer_rain", "heavy_all_day"], help="Mock weather scenario for morning_eval")
    args = parser.parse_args()

    if args.job == "morning_eval":
        run_morning_evaluation(mock_scenario=args.mock_weather)
    elif args.job == "night_check":
        run_checkin_tick()
    elif args.job == "weather_check":
        run_weather_alert_tick()
    elif args.job == "retry":
        retry_pending_notifications()
    elif args.job == "daemon":
        run_daemon()
    else:
        parser.print_help()
