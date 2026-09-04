from dataclasses import dataclass
from decimal import Decimal

import httpx

from ..config import settings


@dataclass(frozen=True)
class CheckoutResult:
    preference_id: str
    checkout_url: str


@dataclass(frozen=True)
class PaymentResult:
    payment_id: str
    external_reference: str | None
    status: str | None
    status_detail: str | None
    amount: Decimal | None
    currency: str | None
    collector_id: str | None
    live_mode: bool | None


class MercadoPagoPointsProvider:
    API_BASE_URL = "https://api.mercadopago.com"

    def _headers(self, idempotency_key: str | None = None):
        if not settings.mercadopago_access_token:
            raise RuntimeError("Mercado Pago não configurado.")
        headers = {"Authorization": f"Bearer {settings.mercadopago_access_token}", "Content-Type": "application/json"}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        return headers

    def create_checkout(self, user, deposit, base_url: str) -> CheckoutResult:
        amount = Decimal(deposit.valor_centavos) / Decimal(100)
        payload = {
            "items": [{"id": "comedoce_points", "title": f"{deposit.quantidade_pontos} pontos Come Doce", "currency_id": "BRL", "quantity": 1, "unit_price": float(amount)}],
            "payer": {"email": user.email},
            "external_reference": deposit.external_reference,
            "back_urls": {"success": f"{base_url}/pontos/pagamento/sucesso", "pending": f"{base_url}/pontos/pagamento/pendente", "failure": f"{base_url}/pontos/pagamento/falha"},
            "notification_url": f"{base_url}/api/webhooks/mercadopago/pontos",
            "auto_return": "approved",
        }
        response = httpx.post(f"{self.API_BASE_URL}/checkout/preferences", headers=self._headers(deposit.idempotency_key), json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        url = data.get("sandbox_init_point") if settings.mercadopago_mode == "test" else data.get("init_point")
        return CheckoutResult(str(data.get("id") or ""), str(url or ""))

    def get_payment(self, payment_id: str) -> PaymentResult:
        response = httpx.get(f"{self.API_BASE_URL}/v1/payments/{payment_id}", headers=self._headers(), timeout=20)
        response.raise_for_status()
        data = response.json()
        collector = data.get("collector") if isinstance(data.get("collector"), dict) else {}
        amount = Decimal(str(data["transaction_amount"])) if data.get("transaction_amount") is not None else None
        return PaymentResult(str(data.get("id") or payment_id), data.get("external_reference"), data.get("status"), data.get("status_detail"), amount, data.get("currency_id"), str(data.get("collector_id") or collector.get("id") or "") or None, data.get("live_mode"))
