import os
from pathlib import Path
from typing import Optional
from sqlmodel import SQLModel, Field, Session, select

# Paths y Base de Datos
BASE_DIR = Path(__file__).resolve().parent

def _default_data_dir() -> Path:
    """/data en Docker (volumen persistente), ./data en desarrollo local."""
    if Path("/.dockerenv").is_file():
        return Path("/data")
    return BASE_DIR / "data"


DATA_DIR = Path(os.getenv("DATA_DIR", str(_default_data_dir())))
DATABASE_PATH = DATA_DIR / "workshop.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATABASE_PATH}")

# Ubicación y Zona Horaria
LATITUDE = float(os.getenv("LATITUDE", "-32.99"))
LONGITUDE = float(os.getenv("LONGITUDE", "-71.27"))
TIMEZONE = os.getenv("TIMEZONE", "America/Santiago")

# Integraciones Externas
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# Parámetros Operativos — ahora son los DEFAULTS de AppSettings (configurables desde la web).
# Se mantienen como constantes porque evaluator.py las usa de fallback si algo llega sin settings.
SETUP_HOURS = 1.0
TEARDOWN_HOURS = 1.0
MIN_WORK_HOURS = 1.0
POST_TEARDOWN_RAIN_BUFFER_HOURS = 2.0

# No vale la pena abrir el taller por menos de esto, salvo que sea la última
# tarea pendiente de todo el backlog (ahí sí se agenda aunque sea corta).
MIN_WORK_HOURS_UNLESS_FINAL = 4.0

MIN_RAIN_PRECIPITATION_MM = 0.2
MIN_RAIN_PROBABILITY_PERCENT = 30.0

# ── Modelo Dinámico en Base de Datos ──
class AppSettings(SQLModel, table=True):
    id: Optional[int] = Field(default=1, primary_key=True)
    operational_start_hour: int = 9
    operational_end_hour: int = 18
    max_humidity_percent: float = 80.0
    exclude_weekends: bool = False  # legado: reemplazado por exclude_saturdays/exclude_sundays, se mantiene por compatibilidad de esquema
    exclude_saturdays: bool = False
    exclude_sundays: bool = False
    exclude_holidays: bool = False
    require_curing_before_cutoff: bool = False
    latitude: float = Field(default=-32.99)
    longitude: float = Field(default=-71.27)
    setup_hours: float = Field(default=SETUP_HOURS)
    teardown_hours: float = Field(default=TEARDOWN_HOURS)
    min_work_hours: float = Field(default=MIN_WORK_HOURS)
    min_work_hours_unless_final: float = Field(default=MIN_WORK_HOURS_UNLESS_FINAL)
    min_rain_precipitation_mm: float = Field(default=MIN_RAIN_PRECIPITATION_MM)
    checkin_hour: int = Field(default=19)  # hora fija del check-in de cierre (no depende de cuándo cierra la ventana calculada)
    morning_eval_lead_hours: int = Field(default=1)  # cuántas horas ANTES de operational_start_hour correr la evaluación matutina

def get_app_settings(session: Session) -> AppSettings:
    """Recupera la configuración actual de la BD o la crea con valores por defecto."""
    settings = session.get(AppSettings, 1)
    if not settings:
        settings = AppSettings(id=1)
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings