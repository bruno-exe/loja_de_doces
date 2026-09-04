import hashlib
import hmac
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import or_, select

from ..config import settings
from ..database import SessionLocal
from ..models import DepositoPontos, LancamentoPontos, Usuario
from ..security import csrf_token, validate_csrf
from ..services.mercadopago_points import MercadoPagoPointsProvider, PaymentResult
from ..session import current_user


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
provider = MercadoPagoPointsProvider()


def parse_brl(value: str) -> int:
    normalized = value.strip().replace("R$", "").replace(" ", "")
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        decimal = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Valor inválido.") from exc
    cents = int((decimal * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents <= 0 or decimal != Decimal(cents) / 100:
        raise ValueError("Valor inválido.")
    return cents


def credit_confirmed_payment(database, payment: PaymentResult):
    deposit = database.scalar(select(DepositoPontos).where(or_(
        DepositoPontos.provider_payment_id == payment.payment_id,
        DepositoPontos.external_reference == payment.external_reference,
    )))
    if deposit is None:
        return None, False
    deposit.provider_payment_id = payment.payment_id
    deposit.provider_status = payment.status
    deposit.provider_status_detail = payment.status_detail
    expected = Decimal(deposit.valor_centavos) / 100
    valid = payment.external_reference == deposit.external_reference and payment.amount == expected and (payment.currency or "").upper() == "BRL"
    if settings.mercadopago_user_id:
        valid = valid and payment.collector_id == str(settings.mercadopago_user_id)
    if settings.mercadopago_mode == "production":
        valid = valid and payment.live_mode is not False
    if not valid:
        deposit.status = "failed"
        return deposit, False
    if payment.status != "approved":
        deposit.status = "pending" if payment.status in {"pending", "in_process", "authorized"} else "failed"
        return deposit, False
    existing = database.scalar(select(LancamentoPontos).where(LancamentoPontos.deposito_id == deposit.id))
    if existing is None:
        database.add(LancamentoPontos(usuario_id=deposit.usuario_id, deposito_id=deposit.id, quantidade=deposit.quantidade_pontos, motivo="Compra de pontos pelo Mercado Pago"))
    deposit.status = "paid"
    deposit.confirmado_em = deposit.confirmado_em or datetime.now(timezone.utc)
    return deposit, True


@router.post("/pontos/deposito")
def create_points_deposit(request: Request, valor: str = Form(...), csrf: str = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    validate_csrf(request, csrf)
    if not settings.real_payments_enabled or not settings.mercadopago_access_token:
        return RedirectResponse("/pontos?erro=configuracao", status_code=status.HTTP_303_SEE_OTHER)
    base_url = settings.public_base_url
    if urlparse(base_url).scheme not in {"http", "https"}:
        return RedirectResponse("/pontos?erro=endereco", status_code=status.HTTP_303_SEE_OTHER)
    try:
        cents = parse_brl(valor)
    except ValueError:
        return RedirectResponse("/pontos?erro=valor", status_code=status.HTTP_303_SEE_OTHER)
    points = cents * 10
    if points < settings.min_points_purchase or points > settings.max_points_purchase:
        return RedirectResponse("/pontos?erro=limite", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        db_user = database.get(Usuario, user.id)
        deposit = DepositoPontos(usuario_id=user.id, valor_centavos=cents, quantidade_pontos=points, external_reference=f"comedoce_points_{uuid4().hex}", idempotency_key=str(uuid4()))
        database.add(deposit)
        database.commit()
        database.refresh(deposit)
        try:
            checkout = provider.create_checkout(db_user, deposit, base_url)
        except Exception:
            deposit.status = "failed"
            database.commit()
            return RedirectResponse("/pontos?erro=mercadopago", status_code=status.HTTP_303_SEE_OTHER)
        if not checkout.preference_id or not checkout.checkout_url:
            deposit.status = "failed"
            database.commit()
            return RedirectResponse("/pontos?erro=mercadopago", status_code=status.HTTP_303_SEE_OTHER)
        deposit.provider_preference_id = checkout.preference_id
        deposit.checkout_url = checkout.checkout_url
        database.commit()
        return RedirectResponse(checkout.checkout_url, status_code=status.HTTP_303_SEE_OTHER)


def return_page(request: Request, title: str, message: str, paid: bool = False):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request=request, name="pontos_pagamento_retorno.html", context={"usuario": user, "csrf_token": csrf_token(request), "titulo": title, "mensagem": message, "pago": paid})


@router.get("/pontos/pagamento/sucesso", response_class=HTMLResponse)
def payment_success(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    payment_id = (request.query_params.get("payment_id") or request.query_params.get("collection_id") or "").strip()
    if not payment_id:
        return return_page(request, "Pagamento em análise", "Ainda não recebemos o identificador do pagamento.")
    try:
        with SessionLocal() as database:
            deposit, paid = credit_confirmed_payment(database, provider.get_payment(payment_id))
            if deposit and deposit.usuario_id != user.id:
                raise HTTPException(status_code=404, detail="Pagamento não encontrado.")
            database.commit()
        return return_page(request, "Pagamento confirmado" if paid else "Pagamento em análise", "Os pontos foram adicionados ao seu saldo." if paid else "O Mercado Pago ainda não confirmou o pagamento.", paid)
    except HTTPException:
        raise
    except Exception:
        return return_page(request, "Pagamento em análise", "Não foi possível consultar o Mercado Pago agora.")


@router.get("/pontos/pagamento/pendente", response_class=HTMLResponse)
def payment_pending(request: Request):
    return return_page(request, "Pagamento pendente", "Os pontos serão adicionados somente após a confirmação do Mercado Pago.")


@router.get("/pontos/pagamento/falha", response_class=HTMLResponse)
def payment_failure(request: Request):
    return return_page(request, "Pagamento não concluído", "O Mercado Pago informou que o pagamento não foi concluído.")


def valid_signature(request: Request, data_id: str | None) -> bool:
    parts = {key.strip().lower(): value.strip() for item in request.headers.get("x-signature", "").split(",") if "=" in item for key, value in [item.split("=", 1)]}
    ts, expected = parts.get("ts"), parts.get("v1")
    if not settings.mercadopago_webhook_secret or not ts or not expected:
        return False
    manifest = (f"id:{data_id};" if data_id else "") + (f"request-id:{request.headers.get('x-request-id')};" if request.headers.get("x-request-id") else "") + f"ts:{ts};"
    digest = hmac.new(settings.mercadopago_webhook_secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


@router.post("/api/webhooks/mercadopago/pontos")
async def points_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    body_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    data_id = str(request.query_params.get("data.id") or body_data.get("id") or payload.get("id") or "").strip() or None
    if not valid_signature(request, data_id):
        return JSONResponse({"success": False}, status_code=401)
    if not data_id:
        return {"success": True}
    try:
        payment = provider.get_payment(data_id)
        with SessionLocal() as database:
            credit_confirmed_payment(database, payment)
            database.commit()
    except Exception:
        return JSONResponse({"success": False}, status_code=502)
    return {"success": True}
