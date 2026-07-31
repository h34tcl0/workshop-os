from fastapi import APIRouter, Request, HTTPException, Header, Body
from fastapi.responses import JSONResponse
from sqlmodel import Session, select
from typing import Optional, Dict, Any

from config import TELEGRAM_WEBHOOK_SECRET
from database import engine
from models import Project, Task, TaskCategory, TaskStatus
from telegram_bot import TelegramBotService

router = APIRouter()


@router.post("/tasks/import")
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


@router.post("/telegram/webhook")
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
