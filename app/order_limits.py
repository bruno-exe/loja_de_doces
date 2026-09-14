MAX_SWEETS_PER_ORDER = 10


def validate_order_quantity(quantity: int) -> None:
    from fastapi import HTTPException

    if not 1 <= quantity <= MAX_SWEETS_PER_ORDER:
        raise HTTPException(status_code=422, detail="Escolha de 1 a 10 doces por pedido, somando todos os sabores.")
