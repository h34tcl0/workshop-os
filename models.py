from datetime import datetime, date, time
from typing import List, Optional, Dict, Any
from enum import Enum
from sqlmodel import SQLModel, Field, Relationship
from pydantic import BaseModel

# Enums
class TaskCategory(str, Enum):
    CARPENTRY = "carpentry"       # Carpintería general (corte, lijado, ensamblado seco)
    PVA_GLUE = "pva_glue"         # Encolado PVA (requiere secado/curado diurno)
    VARNISH_PAINT = "varnish_paint" # Barnizado / Pintura (requiere secado/curado diurno)

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

class DayStatus(str, Enum):
    DAY_VIABLE = "DAY_VIABLE"
    DAY_BLOCKED = "DAY_BLOCKED"

# SQLModel DB Models
class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = Field(default=True)

    tasks: List["Task"] = Relationship(back_populates="project")

class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    title: str
    description: Optional[str] = None
    category: TaskCategory = Field(default=TaskCategory.CARPENTRY)
    estimated_hours: float = Field(default=1.0)  # Tiempo activo con ruido
    curing_hours: float = Field(default=0.0)     # Tiempo pasivo de secado/curado sin ruido
    order: int = Field(default=0, index=True)
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    progress_percentage: int = Field(default=0)  # 0, 50, 100
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None  # se setea automáticamente al pasar a COMPLETED (historial de 7 días)

    project: Optional[Project] = Relationship(back_populates="tasks")

    @property
    def requires_curing(self) -> bool:
        return self.curing_hours > 0 or self.category in (TaskCategory.PVA_GLUE, TaskCategory.VARNISH_PAINT)

class DailyLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    eval_date: date = Field(index=True, unique=True)
    status: DayStatus
    window_start: Optional[time] = None
    window_end: Optional[time] = None
    net_work_hours: float = Field(default=0.0)
    block_reason: Optional[str] = None
    tasks_summary: Optional[str] = None
    scheduled_task_ids: Optional[str] = None  # JSON list de IDs de tareas agendadas ese día (para el check-in de Telegram)
    telegram_notified: bool = Field(default=False)
    calendar_created: bool = Field(default=False)
    checkin_sent: bool = Field(default=False)      # ya se mandó la pregunta de check-in nocturno
    checkin_resolved: bool = Field(default=False)  # el usuario ya respondió (sí/no importa cuál)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class FavoriteTask(SQLModel, table=True):
    """Plantilla de tarea frecuente, para agregar rápido desde el form manual."""
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    category: TaskCategory = Field(default=TaskCategory.CARPENTRY)
    estimated_hours: float = Field(default=1.0)
    curing_hours: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class DayOverride(SQLModel, table=True):
    """Ajustes manuales para un día puntual del cronograma, por sobre lo que calcula el motor."""
    id: Optional[int] = Field(default=None, primary_key=True)
    override_date: date = Field(index=True, unique=True)
    force_status: Optional[str] = None  # "BLOCKED" | "VIABLE" | None (=automático)
    custom_start_hour: Optional[int] = None
    custom_end_hour: Optional[int] = None
    removed_task_ids: Optional[str] = None  # JSON list de IDs a excluir del cálculo automático ese día
    note: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class ForcedTask(SQLModel, table=True):
    """Una tarea 'forzada' a un día/hora puntual, saltándose por completo el motor de evaluación."""
    id: Optional[int] = Field(default=None, primary_key=True)
    forced_date: date = Field(index=True)
    task_id: int = Field(foreign_key="task.id")
    forced_start_hour: float = Field(default=9.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

# Non-DB Data Transfer Objects (DTOs)
class HourlyForecast(BaseModel):
    timestamp: datetime
    hour: int
    temperature: float
    relative_humidity: float
    precipitation_probability: float
    precipitation_mm: float

class TimeWindow(BaseModel):
    start_time: time
    end_time: time
    total_duration_hours: float
    net_work_hours: float
    is_viable: bool
    rejection_reason: Optional[str] = None

class DayEvaluation(BaseModel): # Mantén la clase base que tengas (BaseModel o SQLModel)
    eval_date: date
    status: DayStatus
    reason: str
    window: Optional[TimeWindow] = None
    scheduled_tasks: List[Task] = []
    timeline: List[dict] = []
    cutoff_reason: Optional[str] = None
    weather_summary: Optional[Dict[str, Any]] = None  # <--- AGREGAR ESTA LÍNEA
    bar_segments: Optional[Dict[str, Any]] = None
    forced_tasks: List[Dict[str, Any]] = []  # [{"task": Task, "forced_start_hour": float}] — se saltan el motor por completo
    is_manually_blocked: bool = False  # True si el día fue forzado a bloqueado desde el editor