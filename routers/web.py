from fastapi import APIRouter, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlmodel import Session, select
from datetime import date, datetime, timedelta, timezone
from typing import Optional, List
from pydantic import BaseModel
import json

from config import get_app_settings
from database import engine
from models import Project, Task, TaskCategory, TaskStatus, DailyLog, DayStatus, FavoriteTask, DayOverride, ForcedTask
from weather_service import OpenMeteoWeatherService, MockWeatherService
from evaluator import evaluate_day_with_overrides
from holidays_service import get_holiday_dates_for_range
from date_utils_es import format_date_short_es
from scheduler import run_morning_evaluation
from routers.deps import templates, CATEGORY_LABELS, STATUS_LABELS, get_or_create_active_project

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, scenario: Optional[str] = None):
    with Session(engine) as session:
        app_settings = get_app_settings(session)
        project = get_or_create_active_project(session)

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

        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
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


@router.post("/settings/update")
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


@router.post("/tasks/add")
def add_task(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    category: TaskCategory = Form(TaskCategory.CARPENTRY),
    estimated_hours: float = Form(1.0),
    curing_hours: float = Form(0.0),
    order: int = Form(0)
):
    with Session(engine) as session:
        project = get_or_create_active_project(session)

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


@router.post("/tasks/{task_id}/favorite")
def save_task_as_favorite(task_id: int):
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


@router.post("/favorites/{favorite_id}/delete")
def delete_favorite(favorite_id: int):
    with Session(engine) as session:
        fav = session.get(FavoriteTask, favorite_id)
        if fav:
            session.delete(fav)
            session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/favorites/{favorite_id}/use")
def use_favorite(favorite_id: int):
    with Session(engine) as session:
        fav = session.get(FavoriteTask, favorite_id)
        if not fav:
            return RedirectResponse(url="/", status_code=303)

        project = get_or_create_active_project(session)

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


@router.post("/tasks/{task_id}/move-up")
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


@router.post("/tasks/{task_id}/move-down")
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


@router.post("/tasks/{task_id}/update")
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


@router.post("/tasks/{task_id}/update_status")
def update_task_status(task_id: int, status: TaskStatus = Form(...), progress: int = Form(0)):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task:
            task.status = status
            task.progress_percentage = progress
            if progress == 100:
                task.status = TaskStatus.COMPLETED
            if task.status == TaskStatus.COMPLETED and not task.completed_at:
                task.completed_at = datetime.now(timezone.utc)
            session.add(task)
            session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/tasks/{task_id}/delete")
def delete_task(task_id: int):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task:
            session.delete(task)
            session.commit()
    return RedirectResponse(url="/", status_code=303)


class ReorderPayload(BaseModel):
    task_ids: List[int]

@router.post("/tasks/reorder")
def reorder_tasks(payload: ReorderPayload):
    """Accepts an ordered list of task IDs and persists new order values."""
    with Session(engine) as session:
        for index, task_id in enumerate(payload.task_ids, start=1):
            task = session.get(Task, task_id)
            if task:
                task.order = index
                session.add(task)
        session.commit()
    return JSONResponse({"status": "ok"})


@router.post("/day-override/{override_date}/save")
def save_day_override(
    override_date: date,
    force_status: Optional[str] = Form(None),
    custom_start_hour: Optional[str] = Form(None),
    custom_end_hour: Optional[str] = Form(None),
    removed_task_ids: List[int] = Form([]),
    note: Optional[str] = Form(None)
):
    force_status = force_status if force_status in ("BLOCKED", "VIABLE") else None

    def parse_optional_int(value: Optional[str]) -> Optional[int]:
        if value is None or value.strip() == "":
            return None
        try:
            return int(value)
        except ValueError:
            return None

    custom_start_hour_int = parse_optional_int(custom_start_hour)
    custom_end_hour_int = parse_optional_int(custom_end_hour)

    with Session(engine) as session:
        override = session.exec(select(DayOverride).where(DayOverride.override_date == override_date)).first()
        if not override:
            override = DayOverride(override_date=override_date)

        has_any_setting = bool(force_status) or custom_start_hour_int is not None or custom_end_hour_int is not None or removed_task_ids or note

        if not has_any_setting:
            if override.id:
                session.delete(override)
                session.commit()
            return RedirectResponse(url="/", status_code=303)

        override.force_status = force_status
        override.custom_start_hour = custom_start_hour_int
        override.custom_end_hour = custom_end_hour_int
        override.removed_task_ids = json.dumps(removed_task_ids) if removed_task_ids else None
        override.note = note
        override.updated_at = datetime.now(timezone.utc)
        session.add(override)
        session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/day-override/{override_date}/clear")
def clear_day_override(override_date: date):
    with Session(engine) as session:
        override = session.exec(select(DayOverride).where(DayOverride.override_date == override_date)).first()
        if override:
            session.delete(override)
            session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/day-override/{override_date}/force-task")
def force_task_on_day(override_date: date, task_id: int = Form(...), forced_start_hour: float = Form(9.0)):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task:
            forced = ForcedTask(forced_date=override_date, task_id=task_id, forced_start_hour=forced_start_hour)
            session.add(forced)
            session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/day-override/forced-task/{forced_id}/delete")
def unforce_task(forced_id: int):
    with Session(engine) as session:
        forced = session.get(ForcedTask, forced_id)
        if forced:
            session.delete(forced)
            session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/evaluation/force_run")
def force_evaluation(background_tasks: BackgroundTasks, scenario: Optional[str] = Form(None)):
    background_tasks.add_task(run_morning_evaluation, date.today(), scenario)
    return RedirectResponse(url=f"/?scenario={scenario}" if scenario else "/", status_code=303)
