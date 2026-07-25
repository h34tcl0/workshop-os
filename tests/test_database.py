import pytest
from sqlmodel import Session, select, SQLModel, create_engine
from models import Project, Task, TaskCategory, TaskStatus, DailyLog, DayStatus

@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

def test_create_project_and_task(session: Session):
    project = Project(name="Taller Test", description="Taller de pruebas")
    session.add(project)
    session.commit()
    session.refresh(project)

    assert project.id is not None

    task = Task(
        project_id=project.id,
        title="Barnizado de silla",
        category=TaskCategory.VARNISH_PAINT,
        estimated_hours=2.5,
        status=TaskStatus.PENDING
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    assert task.id is not None
    assert task.requires_curing is True

def test_daily_log_persistence(session: Session):
    from datetime import date
    log = DailyLog(
        eval_date=date(2026, 7, 23),
        status=DayStatus.DAY_VIABLE,
        net_work_hours=4.0,
        telegram_notified=True,
        calendar_created=False
    )
    session.add(log)
    session.commit()
    session.refresh(log)

    fetched = session.exec(select(DailyLog).where(DailyLog.eval_date == date(2026, 7, 23))).first()
    assert fetched is not None
    assert fetched.status == DayStatus.DAY_VIABLE
    assert fetched.telegram_notified is True
