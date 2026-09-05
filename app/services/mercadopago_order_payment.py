from dataclasses import dataclass
from decimal import Decimal

import httpx

from .mercadopago_points import PaymentResult


@dataclass(frozen=True)
class OrderCheckoutResult:
    preference_id: str
    checkout_url: str


class MercadoPagoOrderPaymentProvider:
    API_BASE_URL = "https://api.mercadopago.com"

    @staticmethod
    def _headers(access_token: str, idempotency_key: str | None = None) -> dict[str, str]:
        if not access_token:
            raise RuntimeError("Token OAuth do vendedor indisponível.")
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "Accept": "application/json"}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        return headers

    def create_checkout(self, access_token: str, payment, order, public_base_url: str, checkout_mode: str) -> OrderCheckoutResult:
        amount = Decimal(payment.valor_esperado_centavos) / 100
        payload = {
            "items": [{"id": f"comedoce_order_{order.id}", "title": order.produto_nome[:120], "description": (order.produto_descricao or "Pedido Come Doce")[:250], "currency_id": "BRL", "quantity": 1, "unit_price": float(amount)}],
            "external_reference": payment.external_reference,
            "back_urls": {"success": f"{public_base_url}/pagamentos/mercadopago/sucesso", "pending": f"{public_base_url}/pagamentos/mercadopago/pendente", "failure": f"{public_base_url}/pagamentos/mercadopago/falha"},
            "notification_url": f"{public_base_url}/api/webhooks/mercadopago/compras?ref={payment.webhook_reference}",
            "metadata": {"order_id": order.id, "seller_id": order.vendedor_id, "buyer_id": order.cliente_id},
            "auto_return": "approved",
        }
        response = httpx.post(f"{self.API_BASE_URL}/checkout/preferences", headers=self._headers(access_token, payment.idempotency_key), json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        url = data.get("sandbox_init_point") if checkout_mode == "test" else data.get("init_point")
        return OrderCheckoutResult(str(data.get("id") or ""), str(url or ""))

    def get_payment(self, access_token: str, payment_id: str) -> PaymentResult:
        response = httpx.get(f"{self.API_BASE_URL}/v1/payments/{payment_id}", headers=self._headers(access_token), timeout=20)
        response.raise_for_status()
        data = response.json()
        collector = data.get("collector") if isinstance(data.get("collector"), dict) else {}
        amount = Decimal(str(data["transaction_amount"])) if data.get("transaction_amount") is not None else None
        return PaymentResult(str(data.get("id") or payment_id), data.get("external_reference"), data.get("status"), data.get("status_detail"), amount, data.get("currency_id"), str(data.get("collector_id") or collector.get("id") or "") or None, data.get("live_mode"))
