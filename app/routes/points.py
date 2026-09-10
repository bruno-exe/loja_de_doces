from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select

from ..admin_access import is_admin
from ..database import SessionLocal
from ..config import settings
from ..models import DepositoPontos, LancamentoPontos, PerfilComprador, SolicitacaoPontos, Usuario
from ..security import csrf_token, validate_csrf
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
        buyer_profile = database.scalar(select(PerfilComprador).where(PerfilComprador.usuario_id == usuario.id)) if usuario.tipo_conta == "comprador" else None
        withdrawals = database.scalars(select(SolicitacaoPontos).where(SolicitacaoPontos.usuario_id == usuario.id, SolicitacaoPontos.tipo == "saque").order_by(SolicitacaoPontos.id.desc()).limit(10)).all()

    return templates.TemplateResponse(request=request, name="pontos.html", context={
        "usuario": usuario, "csrf_token": csrf_token(request), "pontos": int(points), "depositos": deposits,
        "pagamentos_ativos": settings.real_payments_enabled and bool(settings.mercadopago_access_token),
        "minimo_reais": settings.min_points_purchase / 1000,
        "maximo_reais": settings.max_points_purchase / 1000,
        "perfil_comprador": buyer_profile,
        "saques": withdrawals,
    })


@router.get("/ranking", response_class=HTMLResponse)
def ranking_page(request: Request):
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        balance = func.coalesce(func.sum(LancamentoPontos.quantidade), 0).label("saldo")
        rows = database.execute(
            select(Usuario, balance)
            .outerjoin(LancamentoPontos, LancamentoPontos.usuario_id == Usuario.id)
            .where(or_(Usuario.ativo.is_(True), Usuario.banido.is_(True)))
            .group_by(Usuario.id)
            .order_by(Usuario.banido.asc(), balance.desc(), Usuario.nome.asc(), Usuario.id.asc())
        ).all()

    ranking = []
    for position, (ranked_user, points) in enumerate(rows, 1):
        first_name = "Usuário banido" if ranked_user.banido else (ranked_user.nome.strip().split()[0] if ranked_user.nome.strip() else "Usuário")
        ranking.append({"id": ranked_user.id, "posicao": position, "nome": first_name, "foto": ranked_user.foto, "pontos": int(points or 0), "banido": ranked_user.banido})

    return templates.TemplateResponse(request=request, name="ranking.html", context={"usuario": usuario, "csrf_token": csrf_token(request), "ranking": ranking, "administrador": is_admin(usuario)})


@router.post("/ranking/usuarios/{user_id}/banir")
def ban_ranking_user(request: Request, user_id: int, csrf: str = Form(...)):
    admin = current_user(request)
    if not admin:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not is_admin(admin):
        raise HTTPException(status_code=404, detail="Página não encontrada.")
    validate_csrf(request, csrf)
    if user_id == admin.id:
        return RedirectResponse("/ranking?erro=proprio", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        target = database.get(Usuario, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        if not target.banido:
            photo = target.foto
            balance = int(database.scalar(select(func.coalesce(func.sum(LancamentoPontos.quantidade), 0)).where(LancamentoPontos.usuario_id == target.id)) or 0)
            if balance:
                database.add(LancamentoPontos(usuario_id=target.id, quantidade=-balance, motivo="Saldo zerado por banimento administrativo"))
            target.ativo = False
            target.banido = True
            target.nome = "Usuário banido"
            target.foto = None
            database.commit()
            if photo and Path(photo).name == photo:
                (Path(__file__).resolve().parent.parent / "uploads" / "profile_photos" / photo).unlink(missing_ok=True)
    return RedirectResponse("/ranking?banido=1", status_code=status.HTTP_303_SEE_OTHER)
