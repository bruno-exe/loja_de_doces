import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent


class Settings:
    def __init__(self) -> None:
        default_database = (PROJECT_DIR / "comedoce.db").as_posix()
        self.database_url = os.getenv("DATABASE_URL", f"sqlite:///{default_database}")
        self.secret_key = os.getenv(
            "APP_SECRET",
            "desenvolvimento-local-comedoce-trocar-antes-de-publicar",
        )
        self.cookie_secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"


settings = Settings()
