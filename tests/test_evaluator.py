import pytest
from datetime import date
from models import Task, TaskCategory, TaskStatus, DayStatus, HourlyForecast
from weather_service import MockWeatherService
from evaluator import evaluate_day_feasibility

def test_ideal_sunny_day():
    target_date = date(2026, 7, 23)
    tasks = [
        Task(id=1, title="Corte de listones", estimated_hours=2.0, category=TaskCategory.CARPENTRY, status=TaskStatus.PENDING, order=1),
        Task(id=2, title="Armado de marco", estimated_hours=2.0, category=TaskCategory.CARPENTRY, status=TaskStatus.PENDING, order=2),
    ]
    weather_svc = MockWeatherService(scenario="sunny")
    forecasts = weather_svc.get_hourly_forecast(target_date)

    result = evaluate_day_feasibility(target_date, tasks, forecasts)

    assert result.status == DayStatus.DAY_VIABLE
    assert result.window is not None
    assert result.window.start_time.hour == 9
    assert result.window.net_work_hours == 4.0

def test_background_noise_rain_tolerance():
    """Verify that minor API noise (e.g., 5% probability, 0.0mm rain) does NOT block the day."""
    target_date = date(2026, 7, 23)
    tasks = [
        Task(id=1, title="Corte de listones", estimated_hours=2.0, category=TaskCategory.CARPENTRY, status=TaskStatus.PENDING, order=1),
        Task(id=2, title="Armado de marco", estimated_hours=2.0, category=TaskCategory.CARPENTRY, status=TaskStatus.PENDING, order=2),
    ]
    weather_svc = MockWeatherService(scenario="sunny")
    forecasts = weather_svc.get_hourly_forecast(target_date)

    # Inject low noise (5% prob, 0.0mm rain) at 12:00 PM
    for f in forecasts:
        if f.hour == 12:
            f.precipitation_probability = 5.0
            f.precipitation_mm = 0.0

    result = evaluate_day_feasibility(target_date, tasks, forecasts)

    assert result.status == DayStatus.DAY_VIABLE
    assert result.window is not None

def test_curing_hours_passive_extension():
    target_date = date(2026, 7, 23)
    tasks = [
        Task(id=1, title="Encolado PVA", estimated_hours=1.0, curing_hours=4.0, category=TaskCategory.PVA_GLUE, status=TaskStatus.PENDING, order=1),
        Task(id=2, title="Lijado seco", estimated_hours=2.0, curing_hours=0.0, category=TaskCategory.CARPENTRY, status=TaskStatus.PENDING, order=2)
    ]
    weather_svc = MockWeatherService(scenario="sunny")
    forecasts = weather_svc.get_hourly_forecast(target_date)

    result = evaluate_day_feasibility(target_date, tasks, forecasts)

    assert result.status == DayStatus.DAY_VIABLE
    assert result.window is not None
    assert result.window.net_work_hours == 3.0

def test_curing_task_after_12pm_dry_weather_success():
    """Flexible curing rule test: Curing task starting at 12:00 PM with dry weather is APPROVED as DAY_VIABLE."""
    target_date = date(2026, 7, 23)
    tasks = [
        Task(id=1, title="Corte preliminar", estimated_hours=2.0, category=TaskCategory.CARPENTRY, status=TaskStatus.PENDING, order=1),
        Task(id=2, title="Encolado PVA espigas", estimated_hours=1.0, curing_hours=3.0, category=TaskCategory.PVA_GLUE, status=TaskStatus.PENDING, order=2)
    ]
    weather_svc = MockWeatherService(scenario="sunny")
    forecasts = weather_svc.get_hourly_forecast(target_date)

    result = evaluate_day_feasibility(target_date, tasks, forecasts)

    assert result.status == DayStatus.DAY_VIABLE
    assert len(result.scheduled_tasks) == 2

def test_rain_during_work():
    target_date = date(2026, 7, 23)
    tasks = [
        Task(id=1, title="Lijado pesado", estimated_hours=4.0, category=TaskCategory.CARPENTRY, status=TaskStatus.PENDING, order=1)
    ]
    weather_svc = MockWeatherService(scenario="afternoon_rain")  # Rain 14:00 - 17:00
    forecasts = weather_svc.get_hourly_forecast(target_date)

    result = evaluate_day_feasibility(target_date, tasks, forecasts)

    assert result.status == DayStatus.DAY_BLOCKED
    assert "lluvia" in result.reason.lower()

def test_rain_in_post_teardown_buffer():
    target_date = date(2026, 7, 23)
    tasks = [
        Task(id=1, title="Armado de mesa", estimated_hours=4.0, category=TaskCategory.CARPENTRY, status=TaskStatus.PENDING, order=1)
    ]
    weather_svc = MockWeatherService(scenario="buffer_rain")  # Rain 16:00 - 19:00
    forecasts = weather_svc.get_hourly_forecast(target_date)

    result = evaluate_day_feasibility(target_date, tasks, forecasts)

    assert result.status == DayStatus.DAY_BLOCKED
    assert "lluvia" in result.reason.lower()

def test_insufficient_backlog_hours():
    target_date = date(2026, 7, 23)
    tasks = [
        Task(id=1, title="Ajuste rápido", estimated_hours=0.5, category=TaskCategory.CARPENTRY, status=TaskStatus.PENDING, order=1)
    ]
    weather_svc = MockWeatherService(scenario="sunny")
    forecasts = weather_svc.get_hourly_forecast(target_date)

    result = evaluate_day_feasibility(target_date, tasks, forecasts)

    assert result.status == DayStatus.DAY_BLOCKED
    assert "insuficiente" in result.reason.lower()
