from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select

from ..config import settings
from ..database import SessionLocal
from ..models import ComprovantePagamento, IntegracaoMercadoPagoVendedor, ItemCarrinho, PagamentoPedidoMercadoPago, Pedido
from ..security import csrf_token, validate_csrf
from ..services.mercadopago_oauth import MercadoPagoOAuthError, get_seller_mercadopago_credentials
from ..services.mercadopago_order_payment import MercadoPagoOrderPaymentProvider
from ..services.mercadopago_points import PaymentResult
from ..services.payment_distribution import calculate_payment_distribution
from ..session import current_user
from .point_deposits import valid_signature


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
provider = MercadoPagoOrderPaymentProvider()


PAYMENT_STATUS_LABELS = {
    "aguardando_pagamento": "Aguardando pagamento", "pago": "Pagamento aprovado",
    "pagamento_recusado": "Pagamento recusado", "pagamento_cancelado": "Pagamento cancelado",
    "reembolsado": "Pagamento reembolsado", "erro": "Pagamento indisponível",
}


def internal_status(provider_status: str | None) -> str:
    return {"approved": "pago", "rejected": "pagamento_recusado", "cancelled": "pagamento_cancelado", "refunded": "reembolsado", "charged_back": "reembolsado"}.get(provider_status or "", "aguardando_pagamento")


def reconcile_order_payment(database, record: PagamentoPedidoMercadoPago, result: PaymentResult) -> bool:
    order = database.get(Pedido, record.pedido_id)
    integration = database.scalar(select(IntegracaoMercadoPagoVendedor).where(
        IntegracaoMercadoPagoVendedor.vendedor_id == record.vendedor_id,
        IntegracaoMercadoPagoVendedor.ativo.is_(True),
    ))
    if order is None or integration is None:
        return False
    expected_amount = Decimal(record.valor_esperado_centavos) / 100
    valid = (
        result.external_reference == record.external_reference
        and result.amount == expected_amount
        and (result.currency or "").upper() == "BRL"
        and result.collector_id == integration.mercadopago_user_id
        and order.id == record.pedido_id
        and order.cliente_id == record.comprador_id
        and order.vendedor_id == record.vendedor_id
        and order.valor_total_centavos == record.valor_esperado_centavos
    )
    if settings.mercadopago_mode == "production":
        valid = valid and result.live_mode is not False
    record.provider_payment_id = result.payment_id
    record.provider_status = result.status
    record.provider_status_detail = result.status_detail
    record.atualizado_em = datetime.now(timezone.utc)
    if not valid:
        record.status_pagamento = "erro"
        return False
    record.status_pagamento = internal_status(result.status)
    if result.status != "approved":
        return False
    if not order.pago:
        order.pago = True
        order.confirmado = True
        for item in database.scalars(select(ItemCarrinho).where(ItemCarrinho.pedido_pendente_id == order.id)).all():
            database.delete(item)
    record.confirmado_em = record.confirmado_em or datetime.now(timezone.utc)
    return True


@router.post("/pagamentos/pedidos/{order_id}/mercadopago")
def create_order_checkout(request: Request, order_id: int, csrf: str = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    validate_csrf(request, csrf)
    if urlparse(settings.public_base_url).scheme not in {"http", "https"}:
        return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_mp=endereco", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        order = database.scalar(select(Pedido).where(Pedido.id == order_id, Pedido.cliente_id == user.id))
        if order is None:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        if order.pago or order.status == "cancelado":
            return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_mp=indisponivel", status_code=status.HTTP_303_SEE_OTHER)
        if database.scalar(select(ComprovantePagamento.id).where(ComprovantePagamento.pedido_id == order.id)):
            return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_mp=comprovante", status_code=status.HTTP_303_SEE_OTHER)
        integration = database.scalar(select(IntegracaoMercadoPagoVendedor).where(IntegracaoMercadoPagoVendedor.vendedor_id == order.vendedor_id, IntegracaoMercadoPagoVendedor.ativo.is_(True)))
        if integration is None:
            return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_mp=sem_conta", status_code=status.HTTP_303_SEE_OTHER)
        distribution = calculate_payment_distribution(Decimal(order.valor_total_centavos) / 100, order_id=order.id, seller_id=order.vendedor_id, buyer_id=order.cliente_id, product_id=order.produto_id)
        if distribution.seller_amount != distribution.total_amount or distribution.platform_amount != Decimal("0.00"):
            return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_mp=distribuicao", status_code=status.HTTP_303_SEE_OTHER)
        record = database.scalar(select(PagamentoPedidoMercadoPago).where(PagamentoPedidoMercadoPago.pedido_id == order.id))
        if record and record.status_pagamento == "aguardando_pagamento" and record.checkout_url:
            return RedirectResponse(record.checkout_url, status_code=status.HTTP_303_SEE_OTHER)
        if record is None:
            record = PagamentoPedidoMercadoPago(pedido_id=order.id, comprador_id=order.cliente_id, vendedor_id=order.vendedor_id, valor_esperado_centavos=order.valor_total_centavos, external_reference=f"comedoce_order_{order.id}_{uuid4().hex}", webhook_reference=uuid4().hex, idempotency_key=str(uuid4()))
            database.add(record)
        else:
            record.valor_esperado_centavos = order.valor_total_centavos
            record.external_reference = f"comedoce_order_{order.id}_{uuid4().hex}"
            record.webhook_reference = uuid4().hex
            record.idempotency_key = str(uuid4())
            record.provider_preference_id = record.provider_payment_id = record.checkout_url = None
            record.status_pagamento = "aguardando_pagamento"
        database.commit()
        database.refresh(record)
        try:
            access_token = get_seller_mercadopago_credentials(database, order.vendedor_id)
            if not access_token:
                raise MercadoPagoOAuthError("Token indisponível.")
            checkout = provider.create_checkout(access_token, record, order, settings.public_base_url, settings.mercadopago_mode)
        except Exception:
            record.status_pagamento = "erro"
            database.commit()
            return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_mp=mercadopago", status_code=status.HTTP_303_SEE_OTHER)
        if not checkout.preference_id or not checkout.checkout_url:
            record.status_pagamento = "erro"
            database.commit()
            return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_mp=mercadopago", status_code=status.HTTP_303_SEE_OTHER)
        record.provider_preference_id = checkout.preference_id
        record.checkout_url = checkout.checkout_url
        database.commit()
        return RedirectResponse(checkout.checkout_url, status_code=status.HTTP_303_SEE_OTHER)


def payment_return(request: Request, state: str):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    payment_id = (request.query_params.get("payment_id") or request.query_params.get("collection_id") or "").strip()
    external_reference = (request.query_params.get("external_reference") or "").strip()
    record = None
    paid = False
    if payment_id and external_reference:
        try:
            with SessionLocal() as database:
                record = database.scalar(select(PagamentoPedidoMercadoPago).where(PagamentoPedidoMercadoPago.external_reference == external_reference, PagamentoPedidoMercadoPago.comprador_id == user.id))
                if record:
                    token = get_seller_mercadopago_credentials(database, record.vendedor_id)
                    if token:
                        paid = reconcile_order_payment(database, record, provider.get_payment(token, payment_id))
                        database.commit()
        except Exception:
            paid = False
    title = "Pagamento aprovado" if paid else ("Pagamento não concluído" if state == "falha" else "Pagamento em análise")
    message = "Seu pedido foi marcado como pago." if paid else "A confirmação oficial do Mercado Pago ainda não foi recebida."
    return templates.TemplateResponse(request=request, name="pagamento_mercadopago_retorno.html", context={"usuario": user, "csrf_token": csrf_token(request), "titulo": title, "mensagem": message, "pedido_id": record.pedido_id if record else None})


@router.get("/pagamentos/mercadopago/sucesso", response_class=HTMLResponse)
def success_return(request: Request): return payment_return(request, "sucesso")


@router.get("/pagamentos/mercadopago/pendente", response_class=HTMLResponse)
def pending_return(request: Request): return payment_return(request, "pendente")


@router.get("/pagamentos/mercadopago/falha", response_class=HTMLResponse)
def failure_return(request: Request): return payment_return(request, "falha")


@router.post("/api/webhooks/mercadopago/compras")
async def order_payment_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    body_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    payment_id = str(request.query_params.get("data.id") or body_data.get("id") or payload.get("id") or "").strip() or None
    if not valid_signature(request, payment_id):
        return JSONResponse({"success": False}, status_code=401)
    webhook_reference = request.query_params.get("ref", "")
    with SessionLocal() as database:
        record = database.scalar(select(PagamentoPedidoMercadoPago).where(PagamentoPedidoMercadoPago.webhook_reference == webhook_reference))
        if record is None or not payment_id:
            return {"success": True, "ignored": True}
        try:
            token = get_seller_mercadopago_credentials(database, record.vendedor_id)
            if not token:
                raise MercadoPagoOAuthError("Token indisponível.")
            reconcile_order_payment(database, record, provider.get_payment(token, payment_id))
            database.commit()
        except Exception:
            return JSONResponse({"success": False}, status_code=502)
    return {"success": True}
