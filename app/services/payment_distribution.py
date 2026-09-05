from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PaymentDistribution:
    total_amount: Decimal
    seller_amount: Decimal
    platform_amount: Decimal


def calculate_payment_distribution(total_amount: Decimal, **_context) -> PaymentDistribution:
    """Ponto único para a futura regra dinâmica; ainda não altera pagamentos."""
    return PaymentDistribution(total_amount=total_amount, seller_amount=total_amount, platform_amount=Decimal("0.00"))
