from datetime import date

WEEKDAYS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

def format_date_short_es(d: date) -> str:
    """Ej: 'Jue 24/07'. No depende del locale del sistema (que puede no tener español instalado)."""
    return f"{WEEKDAYS_ES[d.weekday()]} {d.strftime('%d/%m')}"
