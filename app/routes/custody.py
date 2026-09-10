from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..admin_access import is_admin
from ..database import SessionLocal
from ..models import LancamentoPontos, MovimentoCustodia, ObrigacaoCustodia, PerfilComprador, PerfilVendedor, SolicitacaoPontos, Usuario
from ..security import csrf_token, validate_csrf
from ..session import current_user
from .products import format_price


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
POINTS_PER_CENT = 10
MIN_WITHDRAWAL_POINTS = 5000
REDEMPTION_LOCK = Lock()


class RedemptionError(ValueError):
    pass


def _point_balance(database, user_id: int) -> int:
    return int(database.scalar(select(func.coalesce(func.sum(LancamentoPontos.quantidade), 0)).where(LancamentoPontos.usuario_id == user_id)) or 0)


def _custody_balances(database) -> list[tuple[int, int]]:
    balance = func.sum(MovimentoCustodia.custodia_centavos).label("saldo")
    rows = database.execute(select(MovimentoCustodia.vendedor_id, balance).group_by(MovimentoCustodia.vendedor_id).having(balance > 0).order_by(balance.desc(), MovimentoCustodia.vendedor_id)).all()
    return [(seller_id, int(value)) for seller_id, value in rows]


def create_points_redemption(database, *, owner_id: int, recipient_id: int, kind: str, points: int, pix_key: str) -> SolicitacaoPontos:
    value_cents = points // POINTS_PER_CENT
    balances = _custody_balances(database)
    if _point_balance(database, owner_id) < points:
        raise RedemptionError("saldo")
    if sum(value for _, value in balances) < value_cents:
        raise RedemptionError("custodia")
    redemption = SolicitacaoPontos(usuario_id=owner_id, destinatario_id=recipient_id, tipo=kind, pontos=points, valor_centavos=value_cents, chave_pix_destino=pix_key, status="pendente")
    database.add(redemption)
    database.flush()
    database.add(LancamentoPontos(usuario_id=owner_id, solicitacao_id=redemption.id, quantidade=-points, motivo="Pontos bloqueados para saque" if kind == "saque" else "Pontos usados em compra"))
    remaining = value_cents
    has_pending = False
    for custodian_id, available in balances:
        amount = min(available, remaining)
        obligation_status = "compensada" if custodian_id == recipient_id else "pendente"
        has_pending = has_pending or obligation_status == "pendente"
        obligation = ObrigacaoCustodia(solicitacao_id=redemption.id, custodiante_id=custodian_id, destinatario_id=recipient_id, valor_centavos=amount, status=obligation_status, pago_em=datetime.now(timezone.utc) if obligation_status == "compensada" else None)
        database.add(obligation)
        database.flush()
        database.add(MovimentoCustodia(vendedor_id=custodian_id, obrigacao_id=obligation.id, custodia_centavos=-amount, reserva_centavos=0, motivo="Custódia destinada a saque" if kind == "saque" else "Custódia destinada a compra com pontos"))
        remaining -= amount
        if remaining == 0:
            break
    if not has_pending:
        redemption.status = "liquidada"
        redemption.liquidado_em = datetime.now(timezone.utc)
    return redemption


@router.post("/pontos/saques")
def request_withdrawal(request: Request, pontos: int = Form(...), csrf: str = Form(...)):
    validate_csrf(request, csrf)
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if user.tipo_conta != "comprador":
        return RedirectResponse("/pontos?erro_saque=tipo", status_code=status.HTTP_303_SEE_OTHER)
    if pontos < MIN_WITHDRAWAL_POINTS or pontos % POINTS_PER_CENT:
        return RedirectResponse("/pontos?erro_saque=valor", status_code=status.HTTP_303_SEE_OTHER)

    with REDEMPTION_LOCK, SessionLocal() as database:
        profile = database.scalar(select(PerfilComprador).where(PerfilComprador.usuario_id == user.id))
        if not profile or not profile.chave_pix:
            return RedirectResponse("/pontos?erro_saque=pix", status_code=status.HTTP_303_SEE_OTHER)
        try:
            create_points_redemption(database, owner_id=user.id, recipient_id=user.id, kind="saque", points=pontos, pix_key=profile.chave_pix)
        except RedemptionError as exc:
            database.rollback()
            return RedirectResponse(f"/pontos?erro_saque={exc}", status_code=status.HTTP_303_SEE_OTHER)
        database.commit()
    return RedirectResponse("/pontos?saque=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/a-pagar", response_class=HTMLResponse)
def payable_page(request: Request):
    seller = current_user(request)
    if not seller:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if seller.tipo_conta != "vendedor" and not is_admin(seller):
        return RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        custody = int(database.scalar(select(func.coalesce(func.sum(MovimentoCustodia.custodia_centavos), 0)).where(MovimentoCustodia.vendedor_id == seller.id)) or 0)
        reserve = int(database.scalar(select(func.coalesce(func.sum(MovimentoCustodia.reserva_centavos), 0)).where(MovimentoCustodia.vendedor_id == seller.id)) or 0)
        rows = database.execute(select(ObrigacaoCustodia, Usuario, SolicitacaoPontos).join(Usuario, Usuario.id == ObrigacaoCustodia.destinatario_id).join(SolicitacaoPontos, SolicitacaoPontos.id == ObrigacaoCustodia.solicitacao_id).where(ObrigacaoCustodia.custodiante_id == seller.id).order_by(ObrigacaoCustodia.id.desc())).all()
        pending = sum(obligation.valor_centavos for obligation, _, _ in rows if obligation.status == "pendente")
        obligations = [{"id": obligation.id, "nome": recipient.nome, "foto": recipient.foto, "valor": format_price(obligation.valor_centavos), "pix": redemption.chave_pix_destino, "tipo": "Saque de pontos" if redemption.tipo == "saque" else "Compra com pontos", "status": obligation.status} for obligation, recipient, redemption in rows]
    totals = {"disponivel": format_price(custody), "pendente": format_price(pending), "reserva": format_price(reserve), "responsabilidade": format_price(custody + pending + reserve)}
    return templates.TemplateResponse(request=request, name="a_pagar.html", context={"usuario": seller, "csrf_token": csrf_token(request), "obrigacoes": obligations, "totais": totals})


@router.post("/a-pagar/{obligation_id}/confirmar")
def confirm_obligation(request: Request, obligation_id: int, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    seller = current_user(request)
    if not seller:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        obligation = database.scalar(select(ObrigacaoCustodia).where(ObrigacaoCustodia.id == obligation_id, ObrigacaoCustodia.custodiante_id == seller.id))
        if obligation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pagamento não encontrado.")
        if obligation.status == "pendente":
            obligation.status = "paga"
            obligation.pago_em = datetime.now(timezone.utc)
            pending = database.scalar(select(func.count(ObrigacaoCustodia.id)).where(ObrigacaoCustodia.solicitacao_id == obligation.solicitacao_id, ObrigacaoCustodia.id != obligation.id, ObrigacaoCustodia.status == "pendente")) or 0
            if pending == 0:
                redemption = database.get(SolicitacaoPontos, obligation.solicitacao_id)
                redemption.status = "liquidada"
                redemption.liquidado_em = datetime.now(timezone.utc)
            database.commit()
    return RedirectResponse("/a-pagar?pago=1", status_code=status.HTTP_303_SEE_OTHER)
