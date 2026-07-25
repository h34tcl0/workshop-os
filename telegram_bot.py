import json
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta
from typing import List, Optional
from sqlmodel import Session, select
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from database import engine
from models import DayEvaluation, DayStatus, Task, TaskStatus

class TelegramBotService:
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.token = token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{token}"

    def _send_request(self, method: str, payload: dict) -> bool:
        if not self.token or not self.chat_id:
            text_preview = payload.get("text", "")[:60].replace("\n", " ")
            try:
                print(f"[TelegramBotService] Token/ChatID missing. Simulated '{method}': {text_preview}")
            except Exception:
                print(f"[TelegramBotService] Token/ChatID missing. Simulated '{method}'.")
            return True

        url = f"{self.api_url}/{method}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result.get("ok", False)
        except Exception as e:
            print(f"[TelegramBotService] Failed request to Telegram ({method}): {e}")
            return False

    def send_morning_evaluation(self, eval_result: DayEvaluation) -> bool:
        """Sends morning weather evaluation summary with structured Markdown and emojis."""
        date_str = eval_result.eval_date.strftime("%d/%m/%Y")

        if eval_result.status == DayStatus.DAY_VIABLE and eval_result.window:
            w = eval_result.window
            start_str = w.start_time.strftime("%H:%M")
            end_str = w.end_time.strftime("%H:%M")

            dummy_date = date.today()
            dt_start = datetime.combine(dummy_date, w.start_time)
            dt_end = datetime.combine(dummy_date, w.end_time)

            setup_end_str = (dt_start + timedelta(hours=1)).strftime("%H:%M")
            teardown_start_str = (dt_end - timedelta(hours=1)).strftime("%H:%M")

            if eval_result.scheduled_tasks:
                task_list_str = "\n".join([f"  • *{t.title}* ({t.estimated_hours}h)" for t in eval_result.scheduled_tasks])
            else:
                task_list_str = "  • Sin tareas asignadas"

            message = (
                f"☀️ *PLAN DE TALLER DE HOY ({date_str})* ☀️\n\n"
                f"✅ *Día Viable (DAY_VIABLE)*\n"
                f"🕒 *Jornada:* {start_str} - {end_str} ({w.total_duration_hours:.1f}h total)\n\n"
                f"📋 *Desglose del Bloque Macro:*\n"
                f"  🔧 *01h Setup:* {start_str} - {setup_end_str}\n"
                f"  🪵 *{w.net_work_hours:.1f}h Trabajo Neto:* {setup_end_str} - {teardown_start_str}\n"
                f"  🧹 *01h Teardown:* {teardown_start_str} - {end_str}\n\n"
                f"🎯 *Tareas Agendadas:*\n{task_list_str}\n\n"
                f"⏰ *Alarma de Google Calendar agendada a las {start_str} hrs.*"
            )
        else:
            message = (
                f"🌧️ *REPORTE CLIMÁTICO DE HOY ({date_str})* 🌧️\n\n"
                f"🛑 *Día Suspendido (DAY_BLOCKED)*\n\n"
                f"📝 *Causa Climática:*\n{eval_result.reason}\n\n"
                f"🛋️ *Acción:* La alarma de taller permanecerá en silencio. ¡Aprovecha para planificación o descanso!"
            )

        return self._send_request("sendMessage", {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        })

    def send_nightly_checkin(self, active_tasks: List[Task]) -> bool:
        """Sends 21:00 PM check-in prompt with inline buttons for progress update."""
        if not active_tasks:
            return self._send_request("sendMessage", {
                "chat_id": self.chat_id,
                "text": "🌙 *Check-in Nocturno (21:00 PM)*\n\nNo hay tareas registradas en ejecución hoy.",
                "parse_mode": "Markdown"
            })

        for task in active_tasks:
            inline_keyboard = [
                [
                    {"text": "🟢 Completado 100%", "callback_data": f"task:{task.id}:100"},
                    {"text": "🟡 En Progreso 50%", "callback_data": f"task:{task.id}:50"},
                    {"text": "🔴 No Iniciado 0%", "callback_data": f"task:{task.id}:0"}
                ]
            ]
            message = (
                f"🌙 *CHECK-IN NOCTURNO DE TALLER*\n\n"
                f"📌 *Tarea:* {task.title}\n"
                f"⏱️ *Duración Est.:* {task.estimated_hours}h\n"
                f"📊 *Estado Actual:* {task.progress_percentage}%\n\n"
                f"¿Cuál es el avance real al cierre de hoy?"
            )
            self._send_request("sendMessage", {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": inline_keyboard}
            })
        return True

    def process_callback_query(self, callback_query: dict) -> dict:
        """Handles Telegram callback query for button clicks safely and updates DB."""
        cb_id = callback_query.get("id")
        data = callback_query.get("data", "")  # Expected format: "task:{task_id}:{status_code}"

        response_text = "Actualizado"
        if data.startswith("task:"):
            parts = data.split(":")
            if len(parts) == 3:
                task_id = int(parts[1])
                status_code = int(parts[2])

                with Session(engine) as session:
                    task = session.get(Task, task_id)
                    if task:
                        task.progress_percentage = status_code
                        if status_code == 100:
                            task.status = TaskStatus.COMPLETED
                        elif status_code == 50:
                            task.status = TaskStatus.IN_PROGRESS
                        else:
                            task.status = TaskStatus.PENDING
                        session.add(task)
                        session.commit()
                        response_text = f"✅ Tarea '{task.title}' actualizada a {status_code}%"

        # Always answer callback query to clear Telegram button loading spinner
        self._send_request("answerCallbackQuery", {
            "callback_query_id": cb_id,
            "text": response_text
        })
        return {"status": "ok", "message": response_text}