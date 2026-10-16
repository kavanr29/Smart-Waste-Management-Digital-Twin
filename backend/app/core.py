from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


class Settings(BaseSettings):
    database_url: str = "sqlite:///./wastewise.db"
    genai_api_key: str = ""
    genai_base_url: str = "https://api.openai.com/v1"
    genai_model: str = "gpt-4o-mini"
    model_path: str = ""
    cv_confidence: float = 0.10
    cv_iou: float = 0.45
    cv_image_size: int = 640
    cors_origins: str = "http://localhost:3000"
    demo_mode: bool = True
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
if settings.database_url in {"sqlite:///./wastewise.db", "sqlite:///wastewise.db"}:
    # The supplied project database lives at the project root. Resolve the legacy
    # relative URL there even when the documented command starts from backend/.
    project_database = Path(__file__).resolve().parents[2] / "wastewise.db"
    settings.database_url = f"sqlite:///{project_database}"
if not settings.model_path.strip():
    bundled_model = Path(__file__).resolve().parents[1] / "models" / "best.pt"
    if bundled_model.is_file():
        settings.model_path = str(bundled_model)

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
