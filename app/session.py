from fastapi import Request
from sqlalchemy import select

from .database import SessionLocal
from .models import Usuario


def current_user(request: Request) -> Usuario | None:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        return None

    with SessionLocal() as database:
        return database.scalar(
            select(Usuario).where(Usuario.id == user_id, Usuario.ativo.is_(True))
        )
