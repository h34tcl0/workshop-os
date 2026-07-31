import json
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from sqlmodel import Session, select
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from database import engine
from models import DayEvaluation, DayStatus, Task, TaskStatus, DailyLog

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

    def send_weather_alert_burst(self, daily_log_id: int, alert_text: str) -> bool:
        """Manda 3 mensajes seguidos (para que sea inconfundible incluso con ruido de fondo),
        cada uno con botón de confirmación. Se repite cada 10 min desde el scheduler hasta que
        se aprieta el botón o se agota el máximo de reintentos."""
        message = (
            f"🚨🚨🚨 *CAMBIÓ EL CLIMA* 🚨🚨🚨\n\n"
            f"{alert_text}\n\n"
            f"Estás dentro de tu ventana de trabajo de hoy — revisa si conviene guardar herramientas y material."
        )
        inline_keyboard = [[{"text": "✅ OK, ya lo vi", "callback_data": f"wxack:{daily_log_id}"}]]

        all_ok = True
        for _ in range(3):
            ok = self._send_request("sendMessage", {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": inline_keyboard}
            })
            all_ok = all_ok and ok
        return all_ok

    def send_checkin_prompt(self, daily_log_id: int, scheduled_tasks: List[Task]) -> bool:
        """Pregunta binaria de cierre de jornada: ¿se completaron todas las tareas agendadas hoy?"""
        if not scheduled_tasks:
            return True  # nada que preguntar si no había tareas agendadas ese día

        task_list_str = "\n".join([f"  • {t.title} ({t.estimated_hours}h)" for t in scheduled_tasks])
        message = (
            f"🌙 *CIERRE DE JORNADA*\n\n"
            f"Tareas agendadas hoy:\n{task_list_str}\n\n"
            f"¿Se completaron *todas*?"
        )
        inline_keyboard = [
            [
                {"text": "✅ Sí, todas", "callback_data": f"chkall:{daily_log_id}"},
                {"text": "✍️ No, marcar cuáles", "callback_data": f"chkpick:{daily_log_id}"}
            ]
        ]
        return self._send_request("sendMessage", {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": inline_keyboard}
        })

    def _build_picker_keyboard(self, daily_log_id: int, scheduled_tasks: List[Task], checked_ids: Optional[set] = None) -> list:
        checked_ids = checked_ids or set()
        rows = []
        for t in scheduled_tasks:
            mark = "✅" if t.id in checked_ids else "⬜"
            rows.append([{"text": f"{mark} {t.title}", "callback_data": f"chk:{daily_log_id}:{t.id}"}])
        rows.append([{"text": "Confirmar", "callback_data": f"chkconfirm:{daily_log_id}"}])
        return rows

    def _edit_message_keyboard(self, chat_id, message_id, keyboard: list) -> bool:
        return self._send_request("editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": keyboard}
        })

    def _edit_message_text(self, chat_id, message_id, text: str, keyboard: Optional[list] = None) -> bool:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        if keyboard is not None:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return self._send_request("editMessageText", payload)

    def process_callback_query(self, callback_query: dict) -> dict:
        """Handles Telegram callback query for button clicks safely and updates DB."""
        cb_id = callback_query.get("id")
        data = callback_query.get("data", "")
        message = callback_query.get("message", {}) or {}
        message_id = message.get("message_id")
        chat_id = (message.get("chat") or {}).get("id", self.chat_id)

        response_text = "Actualizado"

        if data.startswith("task:"):
            # Formato legado (mensajes de check-in viejos que quedaron pendientes de responder)
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
                            if not task.completed_at:
                                task.completed_at = datetime.now(timezone.utc)
                        elif status_code == 50:
                            task.status = TaskStatus.IN_PROGRESS
                        else:
                            task.status = TaskStatus.PENDING
                        session.add(task)
                        session.commit()
                        response_text = f"✅ Tarea '{task.title}' actualizada a {status_code}%"

        elif data.startswith("chkall:"):
            daily_log_id = int(data.split(":")[1])
            with Session(engine) as session:
                daily_log = session.get(DailyLog, daily_log_id)
                if daily_log and not daily_log.checkin_resolved:
                    task_ids = json.loads(daily_log.scheduled_task_ids or "[]")
                    now = datetime.now(timezone.utc)
                    for tid in task_ids:
                        t = session.get(Task, tid)
                        if t and t.status != TaskStatus.COMPLETED:
                            t.status = TaskStatus.COMPLETED
                            t.progress_percentage = 100
                            t.completed_at = now
                            session.add(t)
                    daily_log.checkin_resolved = True
                    session.add(daily_log)
                    session.commit()
            response_text = "✅ Día completo. ¡Buen trabajo!"
            if message_id:
                self._edit_message_text(chat_id, message_id, f"✅ *Día completo.* ¡Buen trabajo!")

        elif data.startswith("chkpick:"):
            daily_log_id = int(data.split(":")[1])
            with Session(engine) as session:
                daily_log = session.get(DailyLog, daily_log_id)
                task_ids = json.loads(daily_log.scheduled_task_ids or "[]") if daily_log else []
                scheduled_tasks = [session.get(Task, tid) for tid in task_ids]
                scheduled_tasks = [t for t in scheduled_tasks if t]
            response_text = "Marca cuáles se completaron"
            if message_id and scheduled_tasks:
                keyboard = self._build_picker_keyboard(daily_log_id, scheduled_tasks, checked_ids=set())
                self._edit_message_text(
                    chat_id, message_id,
                    "🌙 *¿Cuáles tareas se completaron?*\nTócalas para marcar/desmarcar, luego *Confirmar*.",
                    keyboard=keyboard
                )

        elif data.startswith("chk:"):
            parts = data.split(":")
            daily_log_id, task_id = int(parts[1]), int(parts[2])
            current_keyboard = (message.get("reply_markup") or {}).get("inline_keyboard", [])
            checked_ids = set()
            scheduled_ids_in_order = []
            for row in current_keyboard:
                for btn in row:
                    cb = btn.get("callback_data", "")
                    if cb.startswith("chk:"):
                        p = cb.split(":")
                        tid = int(p[2])
                        scheduled_ids_in_order.append(tid)
                        if btn.get("text", "").startswith("✅"):
                            checked_ids.add(tid)
            # Alternar el estado de la tarea tocada
            if task_id in checked_ids:
                checked_ids.discard(task_id)
            else:
                checked_ids.add(task_id)

            with Session(engine) as session:
                scheduled_tasks = [session.get(Task, tid) for tid in scheduled_ids_in_order]
                scheduled_tasks = [t for t in scheduled_tasks if t]
            response_text = "Marcado"
            if message_id:
                keyboard = self._build_picker_keyboard(daily_log_id, scheduled_tasks, checked_ids=checked_ids)
                self._edit_message_keyboard(chat_id, message_id, keyboard)

        elif data.startswith("chkconfirm:"):
            daily_log_id = int(data.split(":")[1])
            current_keyboard = (message.get("reply_markup") or {}).get("inline_keyboard", [])
            checked_ids = set()
            all_ids = []
            for row in current_keyboard:
                for btn in row:
                    cb = btn.get("callback_data", "")
                    if cb.startswith("chk:"):
                        tid = int(cb.split(":")[2])
                        all_ids.append(tid)
                        if btn.get("text", "").startswith("✅"):
                            checked_ids.add(tid)

            with Session(engine) as session:
                daily_log = session.get(DailyLog, daily_log_id)
                now = datetime.now(timezone.utc)
                completed_titles = []
                pending_titles = []
                for tid in all_ids:
                    t = session.get(Task, tid)
                    if not t:
                        continue
                    if tid in checked_ids:
                        t.status = TaskStatus.COMPLETED
                        t.progress_percentage = 100
                        t.completed_at = now
                        session.add(t)
                        completed_titles.append(t.title)
                    else:
                        pending_titles.append(t.title)
                if daily_log:
                    daily_log.checkin_resolved = True
                    session.add(daily_log)
                session.commit()

            response_text = "Confirmado"
            if message_id:
                summary = f"✅ *Completadas:* {', '.join(completed_titles) if completed_titles else 'ninguna'}\n"
                if pending_titles:
                    summary += f"↩️ *Vuelven al backlog:* {', '.join(pending_titles)}"
                self._edit_message_text(chat_id, message_id, summary)

        elif data.startswith("wxack:"):
            daily_log_id = int(data.split(":")[1])
            with Session(engine) as session:
                daily_log = session.get(DailyLog, daily_log_id)
                if daily_log:
                    daily_log.weather_alert_acknowledged = True
                    session.add(daily_log)
                    session.commit()
            response_text = "✅ Confirmado, no se insiste más."
            if message_id:
                self._edit_message_text(chat_id, message_id, "✅ *Confirmado.* No se insiste más con esta alerta.")

        # Always answer callback query to clear Telegram button loading spinner
        self._send_request("answerCallbackQuery", {
            "callback_query_id": cb_id,
            "text": response_text
        })
        return {"status": "ok", "message": response_text}