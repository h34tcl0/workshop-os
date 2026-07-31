from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from database import engine
from models import Project, TaskCategory, DayStatus

templates = Jinja2Templates(directory="templates")

CATEGORY_LABELS = {
    TaskCategory.CARPENTRY.value: "🛠️ Carpintería General",
    TaskCategory.PVA_GLUE.value: "🧪 Encolado PVA",
    TaskCategory.VARNISH_PAINT.value: "🎨 Barnizado / Pintura",
}

STATUS_LABELS = {
    DayStatus.DAY_VIABLE.value: "🟢 Día Viable",
    DayStatus.DAY_BLOCKED.value: "🔴 Día Suspendido",
}


def get_or_create_active_project(session: Session) -> Project:
    project = session.exec(select(Project).where(Project.is_active == True)).first()
    if not project:
        project = Project(name="Taller Carpintería Al Aire Libre", description="Proyecto principal de taller")
        session.add(project)
        session.commit()
        session.refresh(project)
    return project
