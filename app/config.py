import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")


class Settings:
    def __init__(self) -> None:
        default_database = (PROJECT_DIR / "comedoce.db").as_posix()
        self.database_url = os.getenv("DATABASE_URL", f"sqlite:///{default_database}")
        self.secret_key = os.getenv(
            "APP_SECRET",
            "desenvolvimento-local-comedoce-trocar-antes-de-publicar",
        )
        self.cookie_secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
        self.real_payments_enabled = os.getenv("REAL_PAYMENTS_ENABLED", "false").lower() == "true"
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        self.mercadopago_mode = os.getenv("MERCADOPAGO_MODE", "test").strip().lower()
        self.mercadopago_access_token = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
        self.mercadopago_webhook_secret = os.getenv("MERCADOPAGO_WEBHOOK_SECRET", "")
        self.mercadopago_user_id = os.getenv("MERCADOPAGO_USER_ID", "")
        self.min_points_purchase = int(os.getenv("MIN_POINTS_PURCHASE", "1000"))
        self.max_points_purchase = int(os.getenv("MAX_POINTS_PURCHASE", "500000"))


settings = Settings()
