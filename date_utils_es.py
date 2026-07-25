from datetime import date

WEEKDAYS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
WEEKDAYS_ES_FULL = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

def format_date_short_es(d: date) -> str:
    """Ej: 'Jue 24/07'. No depende del locale del sistema (que puede no tener español instalado)."""
    return f"{WEEKDAYS_ES[d.weekday()]} {d.strftime('%d/%m')}"

def format_date_full_es(d: date) -> str:
    """Ej: 'Jueves 24/07/2026'."""
    return f"{WEEKDAYS_ES_FULL[d.weekday()]} {d.strftime('%d/%m/%Y')}"
