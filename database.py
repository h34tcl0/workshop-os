from sqlmodel import SQLModel, create_engine, Session, text
from config import DATABASE_URL, DATABASE_PATH

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False}
)

def _ensure_column(conn, table: str, column: str, sqlite_type: str, default) -> bool:
    """Agrega `column` a `table` si aún no existe (migración simple para SQLite).
    Devuelve True si la columna se acaba de agregar (útil para disparar migraciones de datos)."""
    try:
        conn.execute(text(f"SELECT {column} FROM {table} LIMIT 1"))
        return False
    except Exception:
        try:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqlite_type} DEFAULT {default}"))
            conn.commit()
            print(f"[Database] Migrated schema: Added '{column}' column to '{table}' table.")
            return True
        except Exception as e:
            print(f"[Database] Migration note ({table}.{column}): {e}")
            return False

def create_db_and_tables():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)

    with engine.connect() as conn:
        _ensure_column(conn, "task", "curing_hours", "FLOAT", 0.0)
        _ensure_column(conn, "appsettings", "setup_hours", "FLOAT", 1.0)
        _ensure_column(conn, "appsettings", "teardown_hours", "FLOAT", 1.0)
        _ensure_column(conn, "appsettings", "min_work_hours", "FLOAT", 1.0)
        _ensure_column(conn, "appsettings", "min_work_hours_unless_final", "FLOAT", 4.0)
        just_added_saturdays = _ensure_column(conn, "appsettings", "exclude_saturdays", "BOOLEAN", 0)
        just_added_sundays = _ensure_column(conn, "appsettings", "exclude_sundays", "BOOLEAN", 0)
        _ensure_column(conn, "appsettings", "exclude_holidays", "BOOLEAN", 0)
        _ensure_column(conn, "appsettings", "min_rain_precipitation_mm", "FLOAT", 0.2)
        _ensure_column(conn, "task", "completed_at", "DATETIME", "NULL")
        _ensure_column(conn, "dailylog", "scheduled_task_ids", "TEXT", "NULL")
        _ensure_column(conn, "dailylog", "checkin_sent", "BOOLEAN", 0)
        _ensure_column(conn, "dailylog", "checkin_resolved", "BOOLEAN", 0)
        _ensure_column(conn, "dailylog", "morning_climate_snapshot", "TEXT", "NULL")
        _ensure_column(conn, "dailylog", "weather_alert_sent", "BOOLEAN", 0)
        _ensure_column(conn, "dailylog", "weather_alert_message", "TEXT", "NULL")
        _ensure_column(conn, "dailylog", "weather_alert_acknowledged", "BOOLEAN", 0)
        _ensure_column(conn, "dailylog", "weather_alert_retry_count", "INTEGER", 0)
        _ensure_column(conn, "dailylog", "weather_alert_last_sent_at", "DATETIME", "NULL")
        _ensure_column(conn, "appsettings", "checkin_hour", "INTEGER", 19)
        _ensure_column(conn, "appsettings", "morning_eval_lead_hours", "INTEGER", 1)

        # Migración de datos (una sola vez): si ya tenías 'exclude_weekends' activado,
        # trasládalo a las 2 columnas nuevas para no perder el comportamiento silenciosamente.
        if just_added_saturdays and just_added_sundays:
            try:
                conn.execute(text(
                    "UPDATE appsettings SET exclude_saturdays = 1, exclude_sundays = 1 "
                    "WHERE exclude_weekends = 1"
                ))
                conn.commit()
                print("[Database] Migración de datos: 'exclude_weekends' -> 'exclude_saturdays' + 'exclude_sundays'.")
            except Exception as e:
                print(f"[Database] Migration note (exclude_weekends -> granular): {e}")

def get_session():
    with Session(engine) as session:
        yield session
