import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken

from ..config import settings
from ..models import IntegracaoMercadoPagoVendedor


AUTHORIZE_URL = "https://auth.mercadopago.com.br/authorization"
TOKEN_URL = "https://api.mercadopago.com/oauth/token"


class MercadoPagoOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    token_type: str | None
    scope: str | None
    user_id: str
    expires_in: int


def oauth_configured() -> bool:
    redirect = urlparse(settings.mercadopago_redirect_uri)
    return bool(
        settings.mercadopago_client_id
        and settings.mercadopago_client_secret
        and redirect.scheme in {"http", "https"}
        and redirect.netloc
        and not redirect.fragment
    )


def authorization_url(state: str) -> str:
    if not oauth_configured():
        raise MercadoPagoOAuthError("OAuth do Mercado Pago não configurado.")
    return f"{AUTHORIZE_URL}?{urlencode({'client_id': settings.mercadopago_client_id, 'response_type': 'code', 'platform_id': 'mp', 'redirect_uri': settings.mercadopago_redirect_uri, 'state': state})}"


def _request_tokens(data: dict) -> OAuthTokens:
    try:
        response = httpx.post(TOKEN_URL, headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"}, data=data, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MercadoPagoOAuthError("Não foi possível concluir a autorização no Mercado Pago.") from exc
    access_token = payload.get("access_token")
    user_id = payload.get("user_id")
    if not isinstance(access_token, str) or not access_token or user_id is None:
        raise MercadoPagoOAuthError("Resposta OAuth inválida do Mercado Pago.")
    try:
        expires_in = max(0, int(payload.get("expires_in") or 0))
    except (TypeError, ValueError):
        expires_in = 0
    return OAuthTokens(access_token, payload.get("refresh_token"), payload.get("token_type"), payload.get("scope"), str(user_id), expires_in)


def exchange_code(code: str, state: str) -> OAuthTokens:
    return _request_tokens({"client_id": settings.mercadopago_client_id, "client_secret": settings.mercadopago_client_secret, "grant_type": "authorization_code", "code": code, "redirect_uri": settings.mercadopago_redirect_uri, "state": state})


def refresh_access_token(refresh_token: str) -> OAuthTokens:
    return _request_tokens({"client_id": settings.mercadopago_client_id, "client_secret": settings.mercadopago_client_secret, "grant_type": "refresh_token", "refresh_token": refresh_token})


def _cipher() -> Fernet:
    configured = settings.oauth_token_encryption_key.strip().encode()
    key = configured or base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise MercadoPagoOAuthError("Chave de criptografia OAuth inválida.") from exc


def encrypt_token(value: str | None) -> str | None:
    return _cipher().encrypt(value.encode()).decode() if value else None


def decrypt_token(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _cipher().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise MercadoPagoOAuthError("Não foi possível acessar a credencial do vendedor.") from exc


def save_tokens(database, seller_id: int, tokens: OAuthTokens) -> IntegracaoMercadoPagoVendedor:
    now = datetime.now(timezone.utc)
    integration = database.query(IntegracaoMercadoPagoVendedor).filter_by(vendedor_id=seller_id).one_or_none()
    was_connected = integration is not None and integration.ativo
    if integration is None:
        integration = IntegracaoMercadoPagoVendedor(vendedor_id=seller_id, mercadopago_user_id=tokens.user_id)
        database.add(integration)
    integration.mercadopago_user_id = tokens.user_id
    integration.access_token_criptografado = encrypt_token(tokens.access_token)
    if tokens.refresh_token:
        integration.refresh_token_criptografado = encrypt_token(tokens.refresh_token)
    elif not was_connected:
        integration.refresh_token_criptografado = None
    integration.token_type = tokens.token_type
    integration.scope = tokens.scope
    integration.expira_em = now + timedelta(seconds=tokens.expires_in) if tokens.expires_in else None
    integration.atualizado_em = now
    if not was_connected:
        integration.conectado_em = now
    integration.ativo = True
    return integration


def get_seller_mercadopago_credentials(database, seller_id: int) -> str | None:
    integration = database.query(IntegracaoMercadoPagoVendedor).filter_by(vendedor_id=seller_id, ativo=True).one_or_none()
    if integration is None:
        return None
    now = datetime.now(timezone.utc)
    expires = integration.expira_em
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires and expires <= now + timedelta(minutes=5):
        refresh_token = decrypt_token(integration.refresh_token_criptografado)
        if not refresh_token:
            return None
        tokens = refresh_access_token(refresh_token)
        if tokens.user_id != integration.mercadopago_user_id:
            raise MercadoPagoOAuthError("A renovação retornou outra conta Mercado Pago.")
        save_tokens(database, seller_id, tokens)
        database.flush()
    return decrypt_token(integration.access_token_criptografado)
