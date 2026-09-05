import secrets
import time

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..database import SessionLocal
from ..models import IntegracaoMercadoPagoVendedor
from ..security import validate_csrf
from ..services.mercadopago_oauth import MercadoPagoOAuthError, authorization_url, exchange_code, oauth_configured, save_tokens
from ..session import current_user


router = APIRouter(prefix="/integracoes/mercadopago")
OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60


def seller_user(request: Request):
    user = current_user(request)
    if not user:
        return None, RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if user.tipo_conta != "vendedor":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Integração disponível apenas para vendedores.")
    return user, None


@router.get("/conectar")
def connect_mercadopago(request: Request):
    seller, redirect = seller_user(request)
    if redirect:
        return redirect
    if not oauth_configured():
        return RedirectResponse("/conta/editar?oauth_erro=configuracao", status_code=status.HTTP_303_SEE_OTHER)
    state = secrets.token_urlsafe(32)
    request.session["mercadopago_oauth"] = {"state": state, "seller_id": seller.id, "created_at": int(time.time())}
    try:
        url = authorization_url(state)
    except MercadoPagoOAuthError:
        return RedirectResponse("/conta/editar?oauth_erro=configuracao", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/callback")
def mercadopago_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    seller, redirect = seller_user(request)
    if redirect:
        return redirect
    pending = request.session.pop("mercadopago_oauth", None)
    valid_state = isinstance(pending, dict) and state and secrets.compare_digest(str(pending.get("state", "")), state)
    valid_owner = isinstance(pending, dict) and pending.get("seller_id") == seller.id
    state_age = int(time.time()) - int(pending.get("created_at", 0)) if isinstance(pending, dict) else -1
    fresh = 0 <= state_age <= OAUTH_STATE_MAX_AGE_SECONDS
    if not valid_state or not valid_owner or not fresh:
        return RedirectResponse("/conta/editar?oauth_erro=state", status_code=status.HTTP_303_SEE_OTHER)
    if error or not code:
        return RedirectResponse("/conta/editar?oauth_erro=cancelado", status_code=status.HTTP_303_SEE_OTHER)
    try:
        tokens = exchange_code(code, state)
        with SessionLocal() as database:
            save_tokens(database, seller.id, tokens)
            database.commit()
    except MercadoPagoOAuthError:
        return RedirectResponse("/conta/editar?oauth_erro=conexao", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/conta/editar?oauth_conectado=1#mercado-pago", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/desconectar")
def disconnect_mercadopago(request: Request, csrf: str = Form(...)):
    seller, redirect = seller_user(request)
    if redirect:
        return redirect
    validate_csrf(request, csrf)
    with SessionLocal() as database:
        integration = database.scalar(select(IntegracaoMercadoPagoVendedor).where(IntegracaoMercadoPagoVendedor.vendedor_id == seller.id))
        if integration:
            integration.ativo = False
            integration.access_token_criptografado = None
            integration.refresh_token_criptografado = None
            database.commit()
    return RedirectResponse("/conta/editar?oauth_desconectado=1#mercado-pago", status_code=status.HTTP_303_SEE_OTHER)
