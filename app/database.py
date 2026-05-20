from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from sqlalchemy import text

from app.config import get_settings

settings = get_settings()
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(search)")).fetchall()
        }
        if "yc_filters_json" not in columns:
            conn.execute(
                text("ALTER TABLE search ADD COLUMN yc_filters_json TEXT DEFAULT '{}'")
            )
        if "location" not in columns:
            conn.execute(text("ALTER TABLE search ADD COLUMN location TEXT"))


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
