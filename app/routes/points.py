from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..database import SessionLocal
from ..config import settings
from ..models import DepositoPontos, LancamentoPontos
from ..security import csrf_token
from ..session import current_user


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


@router.get("/pontos", response_class=HTMLResponse)
def points_page(request: Request):
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        points = database.scalar(
            select(func.coalesce(func.sum(LancamentoPontos.quantidade), 0)).where(LancamentoPontos.usuario_id == usuario.id)
        ) or 0
        deposits = database.scalars(select(DepositoPontos).where(DepositoPontos.usuario_id == usuario.id).order_by(DepositoPontos.id.desc()).limit(10)).all()

    return templates.TemplateResponse(request=request, name="pontos.html", context={
        "usuario": usuario, "csrf_token": csrf_token(request), "pontos": int(points), "depositos": deposits,
        "pagamentos_ativos": settings.real_payments_enabled and bool(settings.mercadopago_access_token),
        "minimo_reais": settings.min_points_purchase / 1000,
        "maximo_reais": settings.max_points_purchase / 1000,
    })
