import json
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta
from typing import List, Dict
from config import LATITUDE, LONGITUDE, TIMEZONE
from models import HourlyForecast

class OpenMeteoWeatherService:
    def __init__(self, lat: float = LATITUDE, lon: float = LONGITUDE, tz: str = TIMEZONE):
        self.lat = lat
        self.lon = lon
        self.tz = tz

    def _fetch(self, start_date: date, end_date: date) -> dict:
        params = {
            "latitude": self.lat,
            "longitude": self.lon,
            "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,precipitation",
            "timezone": self.tz,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d")
        }
        url = f"https://api.open-meteo.com/v1/forecast?{urllib.parse.urlencode(params)}"

        req = urllib.request.Request(url, headers={"User-Agent": "WorkshopOS/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def _parse_forecasts(self, data: dict) -> List[HourlyForecast]:
        hourly_data = data.get("hourly", {})
        times = hourly_data.get("time", [])
        temps = hourly_data.get("temperature_2m", [])
        humidities = hourly_data.get("relative_humidity_2m", [])
        precip_probs = hourly_data.get("precipitation_probability", [])
        precips = hourly_data.get("precipitation", [])

        forecasts: List[HourlyForecast] = []
        for i in range(len(times)):
            dt = datetime.fromisoformat(times[i])
            forecasts.append(HourlyForecast(
                timestamp=dt,
                hour=dt.hour,
                temperature=temps[i] if i < len(temps) else 20.0,
                relative_humidity=humidities[i] if i < len(humidities) else 50.0,
                precipitation_probability=float(precip_probs[i]) if i < len(precip_probs) and precip_probs[i] is not None else 0.0,
                precipitation_mm=float(precips[i]) if i < len(precips) and precips[i] is not None else 0.0
            ))
        return forecasts

    def get_hourly_forecast(self, target_date: date) -> List[HourlyForecast]:
        data = self._fetch(target_date, target_date)
        return self._parse_forecasts(data)

    def get_weekly_forecast(self, start_date: date, days: int = 7) -> Dict[date, List[HourlyForecast]]:
        """Pide todo el rango de días en UNA sola llamada HTTP (en vez de una por día)
        y agrupa el resultado por fecha. Reduce ~7 llamadas bloqueantes a 1."""
        end_date = start_date + timedelta(days=days - 1)
        data = self._fetch(start_date, end_date)
        forecasts = self._parse_forecasts(data)

        by_date: Dict[date, List[HourlyForecast]] = {}
        for f in forecasts:
            by_date.setdefault(f.timestamp.date(), []).append(f)
        return by_date


class MockWeatherService:
    """Mock weather service for testing and offline execution."""
    def __init__(self, scenario: str = "sunny"):
        self.scenario = scenario

    def get_hourly_forecast(self, target_date: date) -> List[HourlyForecast]:
        forecasts: List[HourlyForecast] = []
        for hour in range(24):
            dt = datetime.combine(target_date, datetime.min.time().replace(hour=hour))
            precip_prob = 0.0
            precip_mm = 0.0

            if self.scenario == "morning_rain" and 8 <= hour <= 11:
                precip_prob = 80.0
                precip_mm = 3.5
            elif self.scenario == "afternoon_rain" and 14 <= hour <= 17:
                precip_prob = 90.0
                precip_mm = 5.0
            elif self.scenario == "buffer_rain" and 16 <= hour <= 19:
                precip_prob = 75.0
                precip_mm = 2.0
            elif self.scenario == "heavy_all_day" and 9 <= hour <= 19:
                precip_prob = 100.0
                precip_mm = 10.0

            forecasts.append(HourlyForecast(
                timestamp=dt,
                hour=hour,
                temperature=22.0 - (abs(14 - hour) * 0.8),
                relative_humidity=45.0 + (precip_prob * 0.4),
                precipitation_probability=precip_prob,
                precipitation_mm=precip_mm
            ))
        return forecasts
