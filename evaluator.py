from datetime import date, time
from typing import List, Optional, Dict, Any
from types import SimpleNamespace
import json
from config import (
    SETUP_HOURS,
    TEARDOWN_HOURS,
    MIN_WORK_HOURS,
    MIN_WORK_HOURS_UNLESS_FINAL,
    POST_TEARDOWN_RAIN_BUFFER_HOURS,
    MIN_RAIN_PRECIPITATION_MM,
    MIN_RAIN_PROBABILITY_PERCENT,
    AppSettings
)
from models import Task, HourlyForecast, TimeWindow, DayEvaluation, DayStatus, TaskStatus

DAYS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

def get_spanish_date(d: date) -> str:
    """Devuelve la fecha formateada en español (ej. 'Mié 22/07')."""
    day_name = DAYS_ES[d.weekday()]
    return f"{day_name} {d.day:02d}/{d.month:02d}"

def is_rainy_forecast(wf: HourlyForecast, min_rain_mm: float = MIN_RAIN_PRECIPITATION_MM) -> bool:
    precip_mm = getattr(wf, "precipitation_mm", getattr(wf, "precipitation", 0.0))
    precip_pop = getattr(wf, "precipitation_probability", getattr(wf, "pop", 0.0))
    return (
        precip_mm >= min_rain_mm or
        precip_pop >= MIN_RAIN_PROBABILITY_PERCENT
    )

def compute_hourly_climate_map(
    forecasts: List[HourlyForecast],
    start_hour: int,
    end_hour: int,
    min_rain_mm: float,
    max_humidity_percent: float
) -> List[Dict[str, Any]]:
    """Clasifica cada hora de la jornada operativa (SOLO esa jornada, no el día completo)
    en 'clear' (libre), 'rain' (lluvia) o 'humid' (humedad excesiva sin lluvia). Se usa
    para mostrar la ventana climática real independientemente de si hay tareas o no."""
    hourly_weather = {}
    for f in forecasts:
        h = getattr(f, 'hour', None)
        if h is None and hasattr(f, 'time'):
            h = f.time.hour if hasattr(f.time, 'hour') else int(f.time)
        if h is not None:
            hourly_weather[int(h)] = f

    climate_map = []
    for h in range(start_hour, end_hour):
        wf = hourly_weather.get(h)
        condition = "clear"
        if wf:
            if is_rainy_forecast(wf, min_rain_mm):
                condition = "rain"
            else:
                rel_humidity = getattr(wf, "relative_humidity", getattr(wf, "humidity", getattr(wf, "humidity_percent", 0.0)))
                if rel_humidity > max_humidity_percent:
                    condition = "humid"
        climate_map.append({"hour": h, "condition": condition})
    return climate_map

def compress_climate_segments(climate_map: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Comprime el mapa hora-por-hora en tramos contiguos del mismo tipo, para dibujar la barra."""
    if not climate_map:
        return []
    segments = []
    current = {"start_h": climate_map[0]["hour"], "end_h": climate_map[0]["hour"] + 1, "condition": climate_map[0]["condition"]}
    for entry in climate_map[1:]:
        if entry["condition"] == current["condition"]:
            current["end_h"] = entry["hour"] + 1
        else:
            segments.append(current)
            current = {"start_h": entry["hour"], "end_h": entry["hour"] + 1, "condition": entry["condition"]}
    segments.append(current)
    return segments

def extract_free_windows(climate_map: List[Dict[str, Any]], min_duration_hours: float = 0.0) -> List[Dict[str, Any]]:
    """Extrae las ventanas continuas de horas 'clear' (libres de lluvia/humedad), ignorando
    tramos más cortos que min_duration_hours. Es la 'ventana climática pura', sin mirar tareas."""
    segments = compress_climate_segments(climate_map)
    windows = []
    for seg in segments:
        if seg["condition"] == "clear":
            duration = seg["end_h"] - seg["start_h"]
            if duration >= min_duration_hours:
                windows.append({
                    "start_hour": seg["start_h"],
                    "end_hour": seg["end_h"],
                    "duration_hours": duration,
                    "start_label": format_hour(float(seg["start_h"])),
                    "end_label": format_hour(float(seg["end_h"])),
                })
    return windows

def format_hour(h: float) -> str:
    hours = int(h)
    minutes = int(round((h - hours) * 60))
    if minutes >= 60:
        hours += 1
        minutes = 0
    return f"{hours:02d}:{minutes:02d}"

def format_hour_crossday(h: float) -> str:
    """Como format_hour, pero si la hora cruza medianoche (>=24) la envuelve
    a formato 24h y agrega un indicador '(+1 día)' en vez de mostrar '27:00'."""
    hours = int(h)
    minutes = int(round((h - hours) * 60))
    if minutes >= 60:
        hours += 1
        minutes = 0
    day_offset, hours_in_day = divmod(hours, 24)
    suffix = f" (+{day_offset} día{'s' if day_offset > 1 else ''})" if day_offset > 0 else ""
    return f"{hours_in_day:02d}:{minutes:02d}{suffix}"

def extract_workday_weather_summary(forecasts: List[HourlyForecast], start_hour: int, end_hour: int, min_rain_mm: float = MIN_RAIN_PRECIPITATION_MM):
    """Extrae las temperaturas mín/máx y el ícono climático leyendo múltiples formatos de objeto/dict."""
    work_forecasts = []
    
    for f in forecasts:
        h = None
        if hasattr(f, 'hour') and f.hour is not None:
            h = f.hour
        elif hasattr(f, 'time'):
            h = f.time.hour if hasattr(f.time, 'hour') else int(f.time)
        elif isinstance(f, dict):
            h = f.get('hour', f.get('time'))

        if h is not None and start_hour <= int(h) < end_hour:
            work_forecasts.append(f)

    if not work_forecasts:
        work_forecasts = forecasts

    temps = []
    for f in work_forecasts:
        t = getattr(f, 'temperature_c', getattr(f, 'temp', getattr(f, 'temperature', None)))
        if t is None and isinstance(f, dict):
            t = f.get('temperature_c', f.get('temp', f.get('temperature')))
        if t is not None:
            temps.append(float(t))

    min_temp = round(min(temps), 1) if temps else 0.0
    max_temp = round(max(temps), 1) if temps else 0.0

    max_pop = max([getattr(f, 'precipitation_probability', getattr(f, 'pop', 0.0)) for f in work_forecasts], default=0)
    max_precip = max([getattr(f, 'precipitation_mm', getattr(f, 'precipitation', 0.0)) for f in work_forecasts], default=0)
    avg_clouds = sum([getattr(f, 'cloud_cover_percent', getattr(f, 'cloud_cover', 0.0)) for f in work_forecasts]) / max(len(work_forecasts), 1)

    if max_precip >= min_rain_mm or max_pop >= MIN_RAIN_PROBABILITY_PERCENT:
        condition, label = "rain", "Lluvia"
    elif avg_clouds > 70:
        condition, label = "cloudy", "Nublado"
    elif avg_clouds > 30:
        condition, label = "partly", "Parcial"
    else:
        condition, label = "sunny", "Soleado"

    return {
        "condition": condition,
        "label": label,
        "min_temp": min_temp,
        "max_temp": max_temp
    }

def analyze_cutoff_reason(
    hourly_weather: dict,
    teardown_end_hour: float,
    next_task: Optional[Task],
    cfg: AppSettings
) -> str:
    """
    Analiza con precisión milimétrica la razón de cierre.
    """
    start_check = int(teardown_end_hour)
    
    if start_check >= cfg.operational_end_hour:
        return f"Límite de jornada operacional alcanzado ({cfg.operational_end_hour}:00 hrs)"

    needed_active_hours = next_task.estimated_hours if next_task else 1.0

    # Curado que EXIGE terminar antes del cierre (límite duro de horario, solo si está activada la opción)
    hard_curing_hours = (next_task.curing_hours if (next_task and next_task.requires_curing and cfg.require_curing_before_cutoff) else 0.0)
    total_time_needed = needed_active_hours + hard_curing_hours

    if teardown_end_hour + total_time_needed > cfg.operational_end_hour:
        if hard_curing_hours > 0 and (teardown_end_hour + needed_active_hours <= cfg.operational_end_hour):
            return (
                f"Límite de horario: La tarea requiere {needed_active_hours:.1f}h de trabajo + {hard_curing_hours:.1f}h de curado, "
                f"lo cual excede el fin de jornada a las {cfg.operational_end_hour}:00 hrs"
            )
        return f"Sin margen de tiempo suficiente para completar la tarea antes de las {cfg.operational_end_hour}:00 hrs"

    # Para la ZONA DE VIGILANCIA CLIMÁTICA, el curado siempre cuenta (así no esté obligado a
    # terminar antes del cierre), porque el motor real también la extiende siempre — si no
    # calzan estos dos rangos, este diagnóstico puede no encontrar la lluvia/humedad real que
    # causó el rechazo. Usa el mismo fallback de 2h que usa el motor cuando no hay curing_hours.
    watch_curing_hours = 0.0
    if next_task and next_task.requires_curing:
        watch_curing_hours = next_task.curing_hours if next_task.curing_hours > 0 else 2.0

    weather_watch_end = teardown_end_hour + needed_active_hours + watch_curing_hours
    end_check = min(23, int(max(weather_watch_end, teardown_end_hour + total_time_needed) + POST_TEARDOWN_RAIN_BUFFER_HOURS) + 1)
    
    for h in range(start_check, end_check):
        wf = hourly_weather.get(h)
        if not wf:
            continue

        precip_mm = getattr(wf, "precipitation_mm", getattr(wf, "precipitation", 0.0))
        precip_pop = getattr(wf, "precipitation_probability", getattr(wf, "pop", 0.0))

        if precip_mm >= getattr(cfg, "min_rain_precipitation_mm", MIN_RAIN_PRECIPITATION_MM) or precip_pop >= MIN_RAIN_PROBABILITY_PERCENT:
            return f"Riesgo de lluvia detectado a las {h:02d}:00 hrs (Probabilidad: {precip_pop:.0f}%, Precipitación: {precip_mm:.1f}mm)"

        rel_humidity = getattr(wf, "relative_humidity", getattr(wf, "humidity", getattr(wf, "humidity_percent", 0.0)))
        if rel_humidity > cfg.max_humidity_percent:
            return f"Exceso de humedad detectado a las {h:02d}:00 hrs ({rel_humidity:.0f}%, Máx permitido: {cfg.max_humidity_percent:.0f}%)"

    return (
        f"No hay una ventana continua de {needed_active_hours:.1f}h "
        f"{'+ ' + str(round(watch_curing_hours,1)) + 'h de curado ' if watch_curing_hours > 0 else ''}"
        f"libre de lluvia/humedad excesiva antes de las {cfg.operational_end_hour}:00 hrs."
    )

def slice_climate_segments(climate_segments: List[Dict[str, Any]], range_start: float, range_end: float) -> List[Dict[str, Any]]:
    """Recorta el mapa climático (tramos rain/humid/clear) a un rango [range_start, range_end).
    Se usa para pintar con clima real las horas antes/después del bloque de trabajo en la barra."""
    if range_end <= range_start:
        return []
    sliced = []
    for seg in climate_segments:
        s = max(float(seg["start_h"]), range_start)
        e = min(float(seg["end_h"]), range_end)
        if e > s:
            sliced.append({"start_h": s, "end_h": e, "condition": seg["condition"]})
    return sliced

def calculate_bar_segments(window: TimeWindow, timeline: List[Dict[str, Any]], cfg: AppSettings, climate_segments: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Calcula las proporciones exactas para la barra visual basada en el 100% de la jornada laboral.
    Las horas antes/después del bloque de trabajo (setup→curado) se pintan con el clima real
    (libre/lluvia/humedad) en vez de un genérico 'cerrado', si se provee climate_segments.
    """
    total_day_hours = float(cfg.operational_end_hour - cfg.operational_start_hour)
    if total_day_hours <= 0:
        total_day_hours = 1.0

    start_h = float(window.start_time.hour) + (window.start_time.minute / 60.0)
    closed_before_h = max(0.0, start_h - cfg.operational_start_hour)
    setup_h = getattr(cfg, "setup_hours", SETUP_HOURS)
    work_h = window.net_work_hours
    teardown_h = getattr(cfg, "teardown_hours", TEARDOWN_HOURS)

    curing_h = 0.0
    for item in timeline:
        if "Curado" in item["title"] or "Secado" in item["title"]:
            try:
                curing_h = float(item["duration"].replace("h", ""))
            except ValueError:
                curing_h = 0.0

    end_activity_h = start_h + setup_h + work_h + teardown_h + curing_h
    closed_after_h = max(0.0, float(cfg.operational_end_hour) - end_activity_h)

    before_climate = slice_climate_segments(climate_segments or [], cfg.operational_start_hour, start_h)
    after_climate = slice_climate_segments(climate_segments or [], min(end_activity_h, cfg.operational_end_hour), cfg.operational_end_hour)

    before_segments = [
        {"pct": ((seg["end_h"] - seg["start_h"]) / total_day_hours) * 100, "condition": seg["condition"], "start_h": seg["start_h"], "end_h": seg["end_h"]}
        for seg in before_climate
    ]
    after_segments = [
        {"pct": ((seg["end_h"] - seg["start_h"]) / total_day_hours) * 100, "condition": seg["condition"], "start_h": seg["start_h"], "end_h": seg["end_h"]}
        for seg in after_climate
    ]

    return {
        "closed_before_h": closed_before_h,
        "pct_closed_before": (closed_before_h / total_day_hours) * 100,
        "before_segments": before_segments,
        "setup_h": setup_h,
        "pct_setup": (setup_h / total_day_hours) * 100,
        "work_h": work_h,
        "pct_work": (work_h / total_day_hours) * 100,
        "teardown_h": teardown_h,
        "pct_teardown": (teardown_h / total_day_hours) * 100,
        "curing_h": curing_h,
        "pct_curing": (curing_h / total_day_hours) * 100,
        "closed_after_h": closed_after_h,
        "pct_closed_after": (closed_after_h / total_day_hours) * 100,
        "after_segments": after_segments,
    }

def evaluate_day_with_overrides(
    eval_date: date,
    backlog_tasks: List[Task],
    forecasts: List[HourlyForecast],
    settings: Optional[Any] = None,
    holiday_dates: Optional[set] = None,
    day_override: Optional[Any] = None,
    forced_tasks_with_hours: Optional[List[Dict[str, Any]]] = None
) -> DayEvaluation:
    """Envuelve evaluate_day_feasibility aplicando ajustes manuales de un día puntual
    (DayOverride) y agregando tareas forzadas (ForcedTask), que se saltan por completo
    el motor de evaluación climática. El motor automático nunca se modifica; esto solo
    decide QUÉ pasarle y qué hacer con el resultado."""
    forced_tasks_with_hours = forced_tasks_with_hours or []

    if day_override and day_override.force_status == "BLOCKED":
        cfg = settings if settings is not None else AppSettings()
        min_rain_mm_cfg = getattr(cfg, "min_rain_precipitation_mm", MIN_RAIN_PRECIPITATION_MM)
        min_work_h_cfg = getattr(cfg, "min_work_hours", MIN_WORK_HOURS)
        start_limit = day_override.custom_start_hour if day_override.custom_start_hour is not None else cfg.operational_start_hour
        end_limit = day_override.custom_end_hour if day_override.custom_end_hour is not None else cfg.operational_end_hour

        climate_map = compute_hourly_climate_map(forecasts, start_limit, end_limit, min_rain_mm_cfg, cfg.max_humidity_percent)
        climate_segments = compress_climate_segments(climate_map)
        free_windows = extract_free_windows(climate_map, min_duration_hours=min_work_h_cfg)

        result = DayEvaluation(
            eval_date=eval_date,
            status=DayStatus.DAY_BLOCKED,
            reason=day_override.note or "Bloqueado manualmente desde el editor de día.",
            weather_summary=extract_workday_weather_summary(forecasts, start_limit, end_limit, min_rain_mm=min_rain_mm_cfg),
            climate_segments=climate_segments,
            free_windows=free_windows,
            climate_only_status="clear" if free_windows else "blocked",
        )
        result.is_manually_blocked = True
    else:
        cfg = settings
        if day_override and (day_override.custom_start_hour is not None or day_override.custom_end_hour is not None):
            # Objeto plano independiente (NO copy.copy sobre el ORM real: eso deja el
            # objeto original marcado como "dirty" en la sesión de SQLAlchemy sin necesidad).
            base = settings if settings is not None else AppSettings()
            cfg = SimpleNamespace(**{
                field: getattr(base, field, None)
                for field in (
                    "operational_start_hour", "operational_end_hour", "max_humidity_percent",
                    "exclude_saturdays", "exclude_sundays", "exclude_holidays",
                    "require_curing_before_cutoff", "latitude", "longitude",
                    "setup_hours", "teardown_hours", "min_work_hours",
                    "min_work_hours_unless_final", "min_rain_precipitation_mm"
                )
            })
            if day_override.custom_start_hour is not None:
                cfg.operational_start_hour = day_override.custom_start_hour
            if day_override.custom_end_hour is not None:
                cfg.operational_end_hour = day_override.custom_end_hour

        result = evaluate_day_feasibility(eval_date, backlog_tasks, forecasts, settings=cfg, holiday_dates=holiday_dates)

        if day_override and day_override.removed_task_ids and result.scheduled_tasks:
            removed_ids = set(json.loads(day_override.removed_task_ids))
            result.scheduled_tasks = [t for t in result.scheduled_tasks if t.id not in removed_ids]

    # Las tareas forzadas se saltan TODAS las reglas (clima, ventana, incluso día bloqueado).
    # Se muestran aparte, nunca compiten por espacio con lo que calculó el motor.
    result.forced_tasks = forced_tasks_with_hours
    return result

def detect_new_weather_risk(
    old_segments: List[Dict[str, Any]],
    new_segments: List[Dict[str, Any]],
    range_start_hour: float,
    range_end_hour: float
) -> Optional[str]:
    """Compara dos mapas climáticos (uno de esta mañana, otro recién consultado) dentro de un
    rango de horas —normalmente tu ventana de trabajo de hoy— y devuelve una descripción del
    primer tramo que empeoró (de 'clear' a 'rain'/'humid'), o None si no cambió nada para peor.
    No le importa si algo MEJORÓ, solo si apareció un riesgo que antes no estaba."""
    def hour_condition(segments, hour):
        for seg in segments:
            if seg["start_h"] <= hour < seg["end_h"]:
                return seg["condition"]
        return "clear"

    for h in range(int(range_start_hour), int(range_end_hour)):
        old_cond = hour_condition(old_segments, h)
        new_cond = hour_condition(new_segments, h)
        if old_cond == "clear" and new_cond in ("rain", "humid"):
            label = "Lluvia" if new_cond == "rain" else "Humedad alta"
            return f"{label} nueva detectada a las {h:02d}:00 hrs (esta mañana no estaba prevista)."
    return None

def evaluate_day_feasibility(
    eval_date: date,
    backlog_tasks: List[Task],
    forecasts: List[HourlyForecast],
    settings: Optional[Any] = None,
    holiday_dates: Optional[set] = None
) -> DayEvaluation:
    cfg = settings if settings is not None else AppSettings()

    # Parámetros operativos: vienen de la configuración guardada en la BD;
    # si por algo faltaran (settings viejo/mockeado), cae a las constantes de config.py.
    setup_h_cfg = getattr(cfg, "setup_hours", SETUP_HOURS)
    teardown_h_cfg = getattr(cfg, "teardown_hours", TEARDOWN_HOURS)
    min_work_h_cfg = getattr(cfg, "min_work_hours", MIN_WORK_HOURS)
    min_work_unless_final_cfg = getattr(cfg, "min_work_hours_unless_final", MIN_WORK_HOURS_UNLESS_FINAL)
    min_rain_mm_cfg = getattr(cfg, "min_rain_precipitation_mm", MIN_RAIN_PRECIPITATION_MM)

    date_str = get_spanish_date(eval_date)
    
    hourly_weather = {}
    for f in forecasts:
        h = getattr(f, 'hour', None)
        if h is None and hasattr(f, 'time'):
            h = f.time.hour if hasattr(f.time, 'hour') else int(f.time)
        if h is not None:
            hourly_weather[int(h)] = f

    start_limit = cfg.operational_start_hour
    end_limit = cfg.operational_end_hour

    weather_summary = extract_workday_weather_summary(forecasts, start_limit, end_limit, min_rain_mm=min_rain_mm_cfg)

    # Ventana climática PURA: se calcula siempre, sin mirar tareas para nada. Sirve para
    # mostrar "hoy hay buen clima de 12 a 18" aunque no tengas nada agendado o no calce.
    climate_map = compute_hourly_climate_map(forecasts, start_limit, end_limit, min_rain_mm_cfg, cfg.max_humidity_percent)
    climate_segments = compress_climate_segments(climate_map)
    free_windows = extract_free_windows(climate_map, min_duration_hours=min_work_h_cfg)
    climate_only_status = "clear" if free_windows else "blocked"
    common_climate_fields = dict(
        climate_segments=climate_segments,
        free_windows=free_windows,
        climate_only_status=climate_only_status,
    )

    weekday = eval_date.weekday()  # 0=Lunes ... 5=Sábado, 6=Domingo
    blocked_labels = []
    if getattr(cfg, "exclude_saturdays", False) and weekday == 5:
        blocked_labels.append("sábado")
    if getattr(cfg, "exclude_sundays", False) and weekday == 6:
        blocked_labels.append("domingo")
    if getattr(cfg, "exclude_holidays", False) and holiday_dates and eval_date in holiday_dates:
        blocked_labels.append("feriado")

    if blocked_labels:
        return DayEvaluation(
            eval_date=eval_date,
            status=DayStatus.DAY_BLOCKED,
            reason=f"Día no laborable ({' / '.join(blocked_labels)}, desactivado en configuración).",
            weather_summary=weather_summary,
            **common_climate_fields
        )

    pending_tasks = [t for t in backlog_tasks if t.status != TaskStatus.COMPLETED]
    pending_tasks.sort(key=lambda t: t.order)

    if not pending_tasks:
        return DayEvaluation(
            eval_date=eval_date,
            status=DayStatus.DAY_BLOCKED,
            reason="No hay tareas pendientes en el backlog.",
            weather_summary=weather_summary,
            **common_climate_fields
        )

    total_active_pending = sum(t.estimated_hours for t in pending_tasks)
    if total_active_pending < min_work_h_cfg:
        return DayEvaluation(
            eval_date=eval_date,
            status=DayStatus.DAY_BLOCKED,
            reason=f"Trabajo insuficiente ({total_active_pending:.1f}h < {min_work_h_cfg}h mínimas).",
            weather_summary=weather_summary,
            **common_climate_fields
        )

    best_window: Optional[TimeWindow] = None
    best_scheduled_tasks: List[Task] = []
    max_work_scheduled = -1.0
    had_weather_viable_but_too_short = False
    first_weather_conflict_detail: Optional[str] = None  # se usa si NINGÚN candidato resulta viable

    min_span = int(setup_h_cfg + min_work_h_cfg)

    for start_hour in range(start_limit, end_limit - min_span + 1):
        for end_hour in range(start_hour + min_span, end_limit + 1):
            available_net_work = float(end_hour - start_hour) - setup_h_cfg

            if available_net_work < min_work_h_cfg:
                continue

            scheduled_package: List[Task] = []
            accumulated_active_hours = 0.0
            current_offset = setup_h_cfg          # dónde puede empezar la PRÓXIMA tarea (incluye huecos de curado ya ocurridos)
            last_active_end_offset = setup_h_cfg  # dónde terminó el trabajo ACTIVO de la última tarea (sin su propio curado) — esto es lo que define cuándo empieza el teardown

            for task in pending_tasks:
                if accumulated_active_hours + task.estimated_hours <= available_net_work + 0.01:
                    task_start = float(start_hour) + current_offset
                    task_active_end = task_start + task.estimated_hours

                    # El curado bloquea el inicio de la SIGUIENTE tarea (no es tiempo de
                    # trabajo activo, pero sí ocupa el reloj: no puedes empezar otra cosa
                    # mientras algo sigue curando/secando).
                    next_offset = current_offset + task.estimated_hours

                    if task.requires_curing:
                        cure_dur = task.curing_hours if task.curing_hours > 0 else 2.0
                        cure_end = task_active_end + cure_dur
                        next_offset = current_offset + task.estimated_hours + cure_dur

                        if cfg.require_curing_before_cutoff and cure_end > end_limit:
                            break

                    # Revalidar contra el reloj real: aunque las horas activas sumadas quepan
                    # en el presupuesto, el curado de una tarea anterior puede haber corrido
                    # esta tarea más tarde de lo que el punto anterior permitía. Tiene que
                    # quedar espacio para el teardown después de esta tarea.
                    if task_active_end > (float(end_hour) - teardown_h_cfg) + 0.01:
                        break

                    scheduled_package.append(task)
                    accumulated_active_hours += task.estimated_hours
                    last_active_end_offset = current_offset + task.estimated_hours
                    current_offset = next_offset

            if accumulated_active_hours < min_work_h_cfg or not scheduled_package:
                continue

            # El teardown empieza justo después del trabajo ACTIVO de la última tarea,
            # no después de su curado (guardas herramientas mientras lo último sigue secando).
            actual_teardown_end = float(start_hour) + last_active_end_offset + teardown_h_cfg

            max_curing_end = actual_teardown_end
            task_time_cursor = setup_h_cfg
            for t in scheduled_package:
                if t.requires_curing:
                    c_dur = t.curing_hours if t.curing_hours > 0 else 2.0
                    c_end = start_hour + task_time_cursor + t.estimated_hours + c_dur
                    if c_end > max_curing_end:
                        max_curing_end = c_end
                    task_time_cursor += t.estimated_hours + c_dur
                else:
                    task_time_cursor += t.estimated_hours

            buffer_end_hour = min(23, int(max(actual_teardown_end + POST_TEARDOWN_RAIN_BUFFER_HOURS, max_curing_end)))

            has_weather_conflict = False
            for h in range(start_hour, buffer_end_hour + 1):
                wf = hourly_weather.get(h)
                if wf:
                    if is_rainy_forecast(wf, min_rain_mm_cfg):
                        has_weather_conflict = True
                        if first_weather_conflict_detail is None:
                            precip_mm = getattr(wf, "precipitation_mm", getattr(wf, "precipitation", 0.0))
                            precip_pop = getattr(wf, "precipitation_probability", getattr(wf, "pop", 0.0))
                            first_weather_conflict_detail = (
                                f"Riesgo de lluvia detectado a las {h:02d}:00 hrs "
                                f"(Probabilidad: {precip_pop:.0f}%, Precipitación: {precip_mm:.1f}mm)."
                            )
                        break
                    rel_humidity = getattr(wf, "relative_humidity", getattr(wf, "humidity", getattr(wf, "humidity_percent", 0.0)))
                    if h >= start_hour + setup_h_cfg and rel_humidity > cfg.max_humidity_percent:
                        has_weather_conflict = True
                        if first_weather_conflict_detail is None:
                            first_weather_conflict_detail = (
                                f"Exceso de humedad detectado a las {h:02d}:00 hrs "
                                f"({rel_humidity:.0f}%, Máx permitido: {cfg.max_humidity_percent:.0f}%)."
                            )
                        break

            if has_weather_conflict:
                continue

            # Regla: no vale la pena abrir el taller por menos de MIN_WORK_HOURS_UNLESS_FINAL,
            # a menos que este paquete agende TODO lo que queda pendiente en el backlog
            # (es decir, es la tarea/tareas final(es), sin nada más después que agendar).
            is_final_batch = (len(scheduled_package) == len(pending_tasks))
            if accumulated_active_hours < min_work_unless_final_cfg and not is_final_batch:
                had_weather_viable_but_too_short = True
                continue

            if accumulated_active_hours > max_work_scheduled:
                max_work_scheduled = accumulated_active_hours
                best_scheduled_tasks = scheduled_package

                # Usa last_active_end_offset (respeta huecos de curado intermedios), no una
                # simple suma de horas activas — si no, la "ventana óptima" mostrada arriba
                # de la tarjeta queda corta cuando hubo curado entre dos tareas.
                actual_work_end = float(start_hour) + last_active_end_offset
                actual_teardown_end = actual_work_end + teardown_h_cfg

                start_t = time(hour=int(start_hour))
                end_t = time(hour=int(actual_teardown_end), minute=int(round((actual_teardown_end % 1) * 60)))

                best_window = TimeWindow(
                    start_time=start_t,
                    end_time=end_t,
                    total_duration_hours=float(actual_teardown_end - start_hour),
                    net_work_hours=accumulated_active_hours,
                    is_viable=True
                )

    if best_window:
        timeline = []
        curr_h = float(best_window.start_time.hour)

        setup_end = curr_h + setup_h_cfg
        timeline.append({
            "time_range": f"{format_hour(curr_h)} — {format_hour(setup_end)}",
            "title": "🛠️ Setup / Preparación de taller",
            "duration": f"{setup_h_cfg:.1f}h"
        })
        curr_h = setup_end

        max_curing_end = curr_h
        for task in best_scheduled_tasks:
            t_end = curr_h + task.estimated_hours
            timeline.append({
                "time_range": f"{format_hour(curr_h)} — {format_hour(t_end)}",
                "title": f" [#{task.order}] {task.title}",
                "duration": f"{task.estimated_hours:.1f}h"
            })
            curr_h = t_end

            if task.requires_curing:
                c_dur = task.curing_hours if task.curing_hours > 0 else 2.0
                c_end = t_end + c_dur
                if c_end > max_curing_end:
                    max_curing_end = c_end
                # El curado bloquea el inicio de la SIGUIENTE tarea/teardown — se muestra
                # como su propio tramo, no se salta el reloj en silencio.
                if task is not best_scheduled_tasks[-1]:
                    timeline.append({
                        "time_range": f"{format_hour(t_end)} — {format_hour(c_end)}",
                        "title": "🧪 Curado / Secado (bloquea el inicio de la siguiente tarea)",
                        "duration": f"{c_dur:.1f}h"
                    })
                    curr_h = c_end

        teardown_end = curr_h + teardown_h_cfg
        timeline.append({
            "time_range": f"{format_hour(curr_h)} — {format_hour(teardown_end)}",
            "title": "🧹 Teardown / Guardado de herramientas",
            "duration": f"{teardown_h_cfg:.1f}h"
        })

        if max_curing_end > teardown_end:
            timeline.append({
                "time_range": f"{format_hour(teardown_end)} — {format_hour_crossday(max_curing_end)}",
                "title": "🧪 Curado / Secado pasivo en taller",
                "duration": f"{max_curing_end - teardown_end:.1f}h"
            })

        remaining = [t for t in pending_tasks if t not in best_scheduled_tasks]
        cutoff_reason = ""
        if not remaining:
            cutoff_reason = "Todas las tareas pendientes fueron asignadas."
        else:
            next_t = remaining[0]
            needed_h = next_t.estimated_hours
            curing_h = next_t.curing_hours if (next_t.requires_curing and cfg.require_curing_before_cutoff) else 0.0

            weather_cause = analyze_cutoff_reason(
                hourly_weather=hourly_weather,
                teardown_end_hour=teardown_end,
                next_task=next_t,
                cfg=cfg
            )

            curing_str = f" + {curing_h:.1f}h curado" if curing_h > 0 else ""
            cutoff_reason = (
                f"La siguiente tarea ('{next_t.title}' - {needed_h:.1f}h activo{curing_str}) "
                f"no pudo agendarse. Cierre a las {format_hour(teardown_end)} hrs por: {weather_cause}."
            )

        bar_segments = calculate_bar_segments(best_window, timeline, cfg, climate_segments=climate_segments)

        return DayEvaluation(
            eval_date=eval_date,
            status=DayStatus.DAY_VIABLE,
            window=best_window,
            scheduled_tasks=best_scheduled_tasks,
            reason=f"Ventana viable ({best_window.start_time.strftime('%H:%M')} a {best_window.end_time.strftime('%H:%M')}).",
            timeline=timeline,
            cutoff_reason=cutoff_reason,
            bar_segments=bar_segments,
            weather_summary=weather_summary,
            **common_climate_fields
        )

    if had_weather_viable_but_too_short:
        return DayEvaluation(
            eval_date=eval_date,
            status=DayStatus.DAY_BLOCKED,
            reason=(
                f"Existían ventanas climáticamente viables, pero ninguna alcanzó el mínimo de "
                f"{min_work_unless_final_cfg:.1f}h de trabajo neto para que valga la pena abrir el taller "
                f"(y no se trataba de la tarea final del backlog)."
            ),
            weather_summary=weather_summary,
            **common_climate_fields
        )

    if first_weather_conflict_detail:
        return DayEvaluation(
            eval_date=eval_date,
            status=DayStatus.DAY_BLOCKED,
            reason=(
                f"Ninguna ventana entre {start_limit}:00 y {end_limit}:00 hrs quedó libre de interferencias "
                f"meteorológicas. {first_weather_conflict_detail}"
            ),
            weather_summary=weather_summary,
            **common_climate_fields
        )

    return DayEvaluation(
        eval_date=eval_date,
        status=DayStatus.DAY_BLOCKED,
        reason=f"Sin ventana viable entre {start_limit}:00 y {end_limit}:00 hrs debido a restricciones climáticas, humedad o límites de curado.",
        weather_summary=weather_summary,
        **common_climate_fields
    )