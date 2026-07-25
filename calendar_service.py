import os
import json
from datetime import date, datetime, time, timedelta
from typing import Optional, List
from config import GOOGLE_CALENDAR_ID, TIMEZONE
from models import Task

GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]

class GoogleCalendarService:
    def __init__(self, calendar_id: str = GOOGLE_CALENDAR_ID):
        self.calendar_id = calendar_id
        self.service = None
        self._init_service()

    def _init_service(self):
        creds_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not creds_file:
            return
        if not os.path.exists(creds_file):
            print(f"[GoogleCalendarService] GOOGLE_APPLICATION_CREDENTIALS apunta a '{creds_file}', pero el archivo no existe.")
            return

        try:
            with open(creds_file, "r", encoding="utf-8") as f:
                creds_data = json.load(f)
        except Exception as e:
            print(f"[GoogleCalendarService] No se pudo leer/parsear el archivo de credenciales: {e}")
            return

        try:
            from googleapiclient.discovery import build

            # El JSON de credenciales de Google tiene forma distinta según el tipo:
            # Service Account: {"type": "service_account", ...}
            # OAuth de usuario final (el que genera `gcloud auth` o un flujo OAuth interactivo): tiene
            # "refresh_token"/"client_id"/"client_secret" (con o sin "type": "authorized_user").
            cred_type = creds_data.get("type")

            if cred_type == "service_account":
                from google.oauth2.service_account import Credentials as ServiceAccountCredentials
                credentials = ServiceAccountCredentials.from_service_account_file(creds_file, scopes=GOOGLE_CALENDAR_SCOPES)
            elif "refresh_token" in creds_data or cred_type == "authorized_user":
                from google.oauth2.credentials import Credentials as UserCredentials
                credentials = UserCredentials.from_authorized_user_file(creds_file, scopes=GOOGLE_CALENDAR_SCOPES)
            else:
                print(
                    f"[GoogleCalendarService] No se reconoce el tipo de credencial en '{creds_file}' "
                    f"(type='{cred_type}'). Se esperaba una Service Account o un token OAuth de usuario."
                )
                return

            self.service = build("calendar", "v3", credentials=credentials)
        except Exception as e:
            print(f"[GoogleCalendarService] Warning: Could not initialize Google Calendar API: {e}")
            self.service = None

    def create_workshop_event(
        self,
        eval_date: date,
        start_time: time,
        end_time: time,
        scheduled_tasks: List[Task]
    ) -> bool:
        if not self.service:
            print(f"[MockCalendarService] Google Calendar Service not initialized. Event simulated for {eval_date} {start_time}-{end_time}.")
            return True

        start_dt = datetime.combine(eval_date, start_time)
        end_dt = datetime.combine(eval_date, end_time)

        task_titles = "\n".join([f"- {t.title} ({t.estimated_hours}h)" for t in scheduled_tasks])
        description = f"🔨 WORKSHOP OS - Bloque Macro de Trabajo\n\nTareas Agendadas:\n{task_titles}"

        event_body = {
            "summary": f"🔨 Taller Carpintería ({start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')})",
            "description": description,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": TIMEZONE,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": TIMEZONE,
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 30},  # Alarma 30 mins antes del setup
                    {"method": "popup", "minutes": 60},
                ],
            },
        }

        try:
            self.service.events().insert(calendarId=self.calendar_id, body=event_body).execute()
            print(f"[GoogleCalendarService] Event created successfully in Google Calendar for {eval_date}.")
            return True
        except Exception as e:
            print(f"[GoogleCalendarService] Error creating event: {e}")
            return False
