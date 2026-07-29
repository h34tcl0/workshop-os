from fastapi import FastAPI, Request, Form, Depends, HTTPException, BackgroundTasks, Body, Header
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
import os
import json

from config import AppSettings, get_app_settings, TELEGRAM_WEBHOOK_SECRET
from database import create_db_and_tables, engine
from models import Project, Task, TaskCategory, TaskStatus, DailyLog, DayStatus, FavoriteTask, DayOverride, ForcedTask
from weather_service import OpenMeteoWeatherService, MockWeatherService
from evaluator import evaluate_day_feasibility, evaluate_day_with_overrides
from holidays_service import get_holiday_dates_for_range
from date_utils_es import format_date_short_es
from scheduler import run_morning_evaluation, retry_pending_notifications
from telegram_bot import TelegramBotService

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    try:
        retry_pending_notifications()
    except Exception as e:
        print(f"[Startup] Retry notification check error: {e}")
    yield

app = FastAPI(title="Workshop OS - Web Dashboard", version="1.1.0", lifespan=lifespan)

# Setup Templates
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

CATEGORY_LABELS = {
    TaskCategory.CARPENTRY.value: "🛠️ Carpintería General",
    TaskCategory.PVA_GLUE.value: "🧪 Encolado PVA",
    TaskCategory.VARNISH_PAINT.value: "🎨 Barnizado / Pintura"
}

STATUS_LABELS = {
    DayStatus.DAY_VIABLE.value: "🟢 Día Viable",
    DayStatus.DAY_BLOCKED.value: "🔴 Día Suspendido"
}

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, scenario: Optional[str] = None):
    with Session(engine) as session:
        # Cargar configuración desde la BD
        app_settings = get_app_settings(session)
        
        project = session.exec(select(Project).where(Project.is_active == True)).first()
        if not project:
            project = Project(name="Taller Carpintería Al Aire Libre", description="Proyecto principal de taller")
            session.add(project)
            session.commit()
            session.refresh(project)

        tasks = session.exec(
            select(Task)
            .where(Task.project_id == project.id, Task.status != TaskStatus.COMPLETED)
            .order_by(Task.order)
        ).all()
        simulated_pending_tasks = list(tasks)

        today = date.today()
        forecast_evaluations = []

        holiday_dates = set()
        if app_settings.exclude_holidays:
            try:
                holiday_dates = get_holiday_dates_for_range(today, today + timedelta(days=6))
            except Exception as e:
                print(f"[Dashboard] Error obteniendo feriados: {e}")

        if scenario:
            weather_svc = MockWeatherService(scenario=scenario)
            weekly_forecasts = None
        else:
            weather_svc = OpenMeteoWeatherService(lat=app_settings.latitude, lon=app_settings.longitude)
            try:
                # Una sola llamada HTTP para los 7 días, en vez de 7 llamadas secuenciales
                weekly_forecasts = weather_svc.get_weekly_forecast(today, days=7)
            except Exception as e:
                print(f"[Dashboard] Error obteniendo pronóstico semanal: {e}")
                weekly_forecasts = None

        for d in range(7):
            eval_date = today + timedelta(days=d)
            log = session.exec(select(DailyLog).where(DailyLog.eval_date == eval_date)).first()
            day_override = session.exec(select(DayOverride).where(DayOverride.override_date == eval_date)).first()

            forced_rows = session.exec(select(ForcedTask).where(ForcedTask.forced_date == eval_date)).all()
            forced_tasks_with_hours = []
            for fr in forced_rows:
                ft_task = session.get(Task, fr.task_id)
                if ft_task:
                    forced_tasks_with_hours.append({"task": ft_task, "forced_start_hour": fr.forced_start_hour, "forced_id": fr.id})

            try:
                if scenario:
                    hourly = weather_svc.get_hourly_forecast(eval_date)
                elif weekly_forecasts is not None:
                    hourly = weekly_forecasts.get(eval_date)
                    if not hourly:
                        raise ValueError(f"Sin datos de pronóstico semanal para {eval_date}")
                else:
                    hourly = weather_svc.get_hourly_forecast(eval_date)
                eval_res = evaluate_day_with_overrides(eval_date, simulated_pending_tasks, hourly, settings=app_settings, holiday_dates=holiday_dates, day_override=day_override, forced_tasks_with_hours=forced_tasks_with_hours)
            except Exception as e:
                mock = MockWeatherService(scenario="sunny")
                eval_res = evaluate_day_with_overrides(eval_date, simulated_pending_tasks, mock.get_hourly_forecast(eval_date), settings=app_settings, holiday_dates=holiday_dates, day_override=day_override, forced_tasks_with_hours=forced_tasks_with_hours)

            if eval_res.status == DayStatus.DAY_VIABLE and eval_res.scheduled_tasks:
                scheduled_ids = {t.id for t in eval_res.scheduled_tasks}
                simulated_pending_tasks = [t for t in simulated_pending_tasks if t.id not in scheduled_ids]
            # Las tareas forzadas también salen del backlog simulado de los próximos días (ya están puestas)
            if forced_tasks_with_hours:
                forced_ids = {ft["task"].id for ft in forced_tasks_with_hours}
                simulated_pending_tasks = [t for t in simulated_pending_tasks if t.id not in forced_ids]

            forecast_evaluations.append({
                "date": eval_date,
                "date_str": format_date_short_es(eval_date),
                "evaluation": eval_res,
                "log": log,
                "day_override": day_override,
                "status_label": STATUS_LABELS.get(eval_res.status.value, eval_res.status.value)
            })

        # Historial: tareas completadas en los últimos 7 días (cualquier vía: Telegram, edición manual, etc.)
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        completed_history = session.exec(
            select(Task)
            .where(Task.status == TaskStatus.COMPLETED, Task.completed_at != None, Task.completed_at >= seven_days_ago)
            .order_by(Task.completed_at.desc())
        ).all()

        favorites = session.exec(select(FavoriteTask).order_by(FavoriteTask.title)).all()

    return templates.TemplateResponse("index.html", {
        "request": request,
        "project": project,
        "tasks": tasks,
        "forecast_evaluations": forecast_evaluations,
        "current_scenario": scenario or "real",
        "categories": list(TaskCategory),
        "category_labels": CATEGORY_LABELS,
        "status_labels": STATUS_LABELS,
        "app_settings": app_settings,
        "completed_history": completed_history,
        "favorites": favorites
    })

# ═══════════════════ CONFIGURACIÓN ═══════════════════

@app.post("/settings/update")
def update_settings(
    operational_start_hour: int = Form(9),
    operational_end_hour: int = Form(18),
    max_humidity_percent: float = Form(80.0),
    exclude_saturdays: bool = Form(False),
    exclude_sundays: bool = Form(False),
    exclude_holidays: bool = Form(False),
    require_curing_before_cutoff: bool = Form(False),
    latitude: float = Form(-32.99),
    longitude: float = Form(-71.27),
    setup_hours: float = Form(1.0),
    teardown_hours: float = Form(1.0),
    min_work_hours: float = Form(1.0),
    min_work_hours_unless_final: float = Form(4.0),
    min_rain_precipitation_mm: float = Form(0.2),
    checkin_hour: int = Form(19),
    morning_eval_lead_hours: int = Form(1)
):
    with Session(engine) as session:
        settings = get_app_settings(session)
        settings.operational_start_hour = operational_start_hour
        settings.operational_end_hour = operational_end_hour
        settings.max_humidity_percent = max_humidity_percent
        settings.exclude_saturdays = exclude_saturdays
        settings.exclude_sundays = exclude_sundays
        settings.exclude_holidays = exclude_holidays
        settings.require_curing_before_cutoff = require_curing_before_cutoff
        settings.latitude = latitude
        settings.longitude = longitude
        settings.setup_hours = setup_hours
        settings.teardown_hours = teardown_hours
        settings.min_work_hours = min_work_hours
        settings.min_work_hours_unless_final = min_work_hours_unless_final
        settings.min_rain_precipitation_mm = min_rain_precipitation_mm
        settings.checkin_hour = checkin_hour
        settings.morning_eval_lead_hours = morning_eval_lead_hours

        session.add(settings)
        session.commit()

    return RedirectResponse(url="/", status_code=303)

# ═══════════════════ TAREAS ═══════════════════

@app.post("/tasks/add")
def add_task(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    category: TaskCategory = Form(TaskCategory.CARPENTRY),
    estimated_hours: float = Form(1.0),
    curing_hours: float = Form(0.0),
    order: int = Form(0)
):
    with Session(engine) as session:
        project = session.exec(select(Project).where(Project.is_active == True)).first()
        if not project:
            project = Project(name="Taller Carpintería Al Aire Libre", description="Proyecto principal de taller")
            session.add(project)
            session.commit()
            session.refresh(project)

        if order == 0:
            max_order_task = session.exec(select(Task).where(Task.project_id == project.id).order_by(Task.order.desc())).first()
            order = (max_order_task.order + 1) if max_order_task else 1

        new_task = Task(
            project_id=project.id,
            title=title,
            description=description,
            category=category,
            estimated_hours=estimated_hours,
            curing_hours=curing_hours,
            order=order,
            status=TaskStatus.PENDING,
            progress_percentage=0
        )
        session.add(new_task)
        session.commit()
    return RedirectResponse(url="/", status_code=303)

# ═══════════════════ TAREAS FAVORITAS ═══════════════════

@app.post("/tasks/{task_id}/favorite")
def save_task_as_favorite(task_id: int):
    """Guarda una tarea del backlog como plantilla favorita (para agregar rápido después)."""
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task:
            existing = session.exec(
                select(FavoriteTask).where(
                    FavoriteTask.title == task.title,
                    FavoriteTask.category == task.category,
                    FavoriteTask.estimated_hours == task.estimated_hours,
                    FavoriteTask.curing_hours == task.curing_hours
                )
            ).first()
            if not existing:
                fav = FavoriteTask(
                    title=task.title,
                    category=task.category,
                    estimated_hours=task.estimated_hours,
                    curing_hours=task.curing_hours
                )
                session.add(fav)
                session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/favorites/{favorite_id}/delete")
def delete_favorite(favorite_id: int):
    with Session(engine) as session:
        fav = session.get(FavoriteTask, favorite_id)
        if fav:
            session.delete(fav)
            session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/favorites/{favorite_id}/use")
def use_favorite(favorite_id: int):
    """Crea una tarea nueva en el backlog directo desde una plantilla favorita."""
    with Session(engine) as session:
        fav = session.get(FavoriteTask, favorite_id)
        if not fav:
            return RedirectResponse(url="/", status_code=303)

        project = session.exec(select(Project).where(Project.is_active == True)).first()
        if not project:
            project = Project(name="Taller Carpintería Al Aire Libre", description="Proyecto principal de taller")
            session.add(project)
            session.commit()
            session.refresh(project)

        max_order_task = session.exec(select(Task).where(Task.project_id == project.id).order_by(Task.order.desc())).first()
        order = (max_order_task.order + 1) if max_order_task else 1

        new_task = Task(
            project_id=project.id,
            title=fav.title,
            category=fav.category,
            estimated_hours=fav.estimated_hours,
            curing_hours=fav.curing_hours,
            order=order,
            status=TaskStatus.PENDING,
            progress_percentage=0
        )
        session.add(new_task)
        session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/tasks/import")
def import_tasks_json(payload: Dict[str, Any] = Body(...)):
    """Endpoint para importar tareas generadas por IA en formato JSON."""
    project_name = payload.get("project_name", "Proyecto Importado IA")
    task_list = payload.get("tasks", [])

    if not task_list:
        raise HTTPException(status_code=400, detail="La lista 'tasks' es requerida y no puede estar vacía.")

    with Session(engine) as session:
        project = session.exec(select(Project).where(Project.name == project_name)).first()
        if not project:
            project = Project(name=project_name, description="Proyecto creado vía Importación IA")
            session.add(project)
            session.commit()
            session.refresh(project)

        all_projects = session.exec(select(Project)).all()
        for p in all_projects:
            p.is_active = (p.id == project.id)
            session.add(p)

        max_order_task = session.exec(select(Task).where(Task.project_id == project.id).order_by(Task.order.desc())).first()
        current_order = (max_order_task.order if max_order_task else 0)

        imported_tasks = []
        for idx, tdata in enumerate(task_list):
            cat_val = tdata.get("category", "carpentry")
            try:
                cat_enum = TaskCategory(cat_val)
            except ValueError:
                cat_enum = TaskCategory.CARPENTRY

            current_order += 1
            task = Task(
                project_id=project.id,
                title=tdata.get("title", f"Tarea {idx+1}"),
                description=tdata.get("description"),
                category=cat_enum,
                estimated_hours=float(tdata.get("estimated_hours", 1.0)),
                curing_hours=float(tdata.get("curing_hours", 0.0)),
                order=current_order,
                status=TaskStatus.PENDING,
                progress_percentage=0
            )
            session.add(task)
            imported_tasks.append(task)

        session.commit()

    return JSONResponse(content={
        "status": "success",
        "message": f"Se importaron {len(imported_tasks)} tareas exitosamente en el proyecto '{project_name}'.",
        "imported_count": len(imported_tasks)
    })

@app.post("/tasks/{task_id}/move-up")
def move_task_up(task_id: int):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task:
            prev_task = session.exec(
                select(Task)
                .where(Task.project_id == task.project_id, Task.order < task.order)
                .order_by(Task.order.desc())
            ).first()
            if prev_task:
                task.order, prev_task.order = prev_task.order, task.order
                session.add(task)
                session.add(prev_task)
                session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/tasks/{task_id}/move-down")
def move_task_down(task_id: int):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task:
            next_task = session.exec(
                select(Task)
                .where(Task.project_id == task.project_id, Task.order > task.order)
                .order_by(Task.order.asc())
            ).first()
            if next_task:
                task.order, next_task.order = next_task.order, task.order
                session.add(task)
                session.add(next_task)
                session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/tasks/{task_id}/update")
def quick_update_task(
    task_id: int,
    title: str = Form(...),
    estimated_hours: float = Form(...),
    curing_hours: float = Form(...),
    category: TaskCategory = Form(...)
):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task:
            task.title = title
            task.estimated_hours = estimated_hours
            task.curing_hours = curing_hours
            task.category = category
            session.add(task)
            session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/tasks/{task_id}/update_status")
def update_task_status(task_id: int, status: TaskStatus = Form(...), progress: int = Form(0)):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task:
            task.status = status
            task.progress_percentage = progress
            if progress == 100:
                task.status = TaskStatus.COMPLETED
            if task.status == TaskStatus.COMPLETED and not task.completed_at:
                task.completed_at = datetime.utcnow()
            session.add(task)
            session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/tasks/{task_id}/delete")
def delete_task(task_id: int):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task:
            session.delete(task)
            session.commit()
    return RedirectResponse(url="/", status_code=303)

# ═══════════════════ EDITOR DE DÍA (overrides manuales) ═══════════════════

@app.post("/day-override/{override_date}/save")
def save_day_override(
    override_date: date,
    force_status: Optional[str] = Form(None),
    custom_start_hour: Optional[str] = Form(None),  # <--- Cambiado de Optional[int] a Optional[str]
    custom_end_hour: Optional[str] = Form(None),    # <--- Cambiado de Optional[int] a Optional[str]
    removed_task_ids: List[int] = Form([]),
    note: Optional[str] = Form(None)
):
    # Convertir cadenas vacías "" recibidas del HTML a None o entero según corresponda
    custom_start_hour = int(custom_start_hour) if custom_start_hour and custom_start_hour.strip() else None
    custom_end_hour = int(custom_end_hour) if custom_end_hour and custom_end_hour.strip() else None

    force_status = force_status if force_status in ("BLOCKED", "VIABLE") else None
    with Session(engine) as session:
        override = session.exec(select(DayOverride).where(DayOverride.override_date == override_date)).first()
        if not override:
            override = DayOverride(override_date=override_date)

        # Si no quedó ningún ajuste real, mejor borrar el override que dejar una fila vacía
        has_any_setting = bool(force_status) or custom_start_hour is not None or custom_end_hour is not None or removed_task_ids or note

        if not has_any_setting:
            if override.id:
                session.delete(override)
                session.commit()
            return RedirectResponse(url="/", status_code=303)

        override.force_status = force_status
        override.custom_start_hour = custom_start_hour
        override.custom_end_hour = custom_end_hour
        override.removed_task_ids = json.dumps(removed_task_ids) if removed_task_ids else None
        override.note = note
        override.updated_at = datetime.utcnow()
        session.add(override)
        session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/day-override/{override_date}/clear")
def clear_day_override(override_date: date):
    with Session(engine) as session:
        override = session.exec(select(DayOverride).where(DayOverride.override_date == override_date)).first()
        if override:
            session.delete(override)
            session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/day-override/{override_date}/force-task")
def force_task_on_day(override_date: date, task_id: int = Form(...), forced_start_hour: float = Form(9.0)):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task:
            forced = ForcedTask(forced_date=override_date, task_id=task_id, forced_start_hour=forced_start_hour)
            session.add(forced)
            session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/day-override/forced-task/{forced_id}/delete")
def unforce_task(forced_id: int):
    with Session(engine) as session:
        forced = session.get(ForcedTask, forced_id)
        if forced:
            session.delete(forced)
            session.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/evaluation/force_run")
def force_evaluation(background_tasks: BackgroundTasks, scenario: Optional[str] = Form(None)):
    background_tasks.add_task(run_morning_evaluation, date.today(), scenario)
    return RedirectResponse(url=f"/?scenario={scenario}" if scenario else "/", status_code=303)

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)):
    if TELEGRAM_WEBHOOK_SECRET:
        if x_telegram_bot_api_secret_token != TELEGRAM_WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Token de webhook inválido.")
    else:
        print("[Telegram Webhook] ADVERTENCIA: TELEGRAM_WEBHOOK_SECRET no está configurado. El webhook no está protegido.")

    try:
        data = await request.json()
        if "callback_query" in data:
            bot_svc = TelegramBotService()
            result = bot_svc.process_callback_query(data["callback_query"])
            return JSONResponse(content=result)
    except Exception as e:
        print(f"[Telegram Webhook Error] {e}")
    return JSONResponse(content={"status": "ok"})