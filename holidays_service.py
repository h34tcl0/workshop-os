import json
import urllib.request
from datetime import date
from typing import Set, Dict

# Cache en memoria por proceso: {(year, country_code): {fechas feriadas}}
_holiday_cache: Dict[tuple, Set[date]] = {}

def get_holiday_dates(year: int, country_code: str = "CL") -> Set[date]:
    """Devuelve el set de fechas feriadas de un año/país usando Nager.Date
    (API pública gratuita, sin API key: https://date.nager.at). Cachea en
    memoria para no repetir la llamada HTTP en cada evaluación."""
    cache_key = (year, country_code)
    if cache_key in _holiday_cache:
        return _holiday_cache[cache_key]

    try:
        url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country_code}"
        req = urllib.request.Request(url, headers={"User-Agent": "WorkshopOS/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        dates = {date.fromisoformat(item["date"]) for item in data}
    except Exception as e:
        print(f"[HolidaysService] No se pudieron obtener feriados {year}/{country_code}: {e}")
        dates = set()

    _holiday_cache[cache_key] = dates
    return dates

def get_holiday_dates_for_range(start: date, end: date, country_code: str = "CL") -> Set[date]:
    """Igual que get_holiday_dates pero cubre un rango que puede cruzar de año
    (ej. 28 dic - 3 ene), uniendo los feriados de todos los años involucrados."""
    result: Set[date] = set()
    for year in range(start.year, end.year + 1):
        result |= get_holiday_dates(year, country_code)
    return result
