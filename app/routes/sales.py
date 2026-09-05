from collections import Counter
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from ..database import SessionLocal
from ..models import ItemPedido, Pedido, Produto, Usuario
from ..security import csrf_token
from ..session import current_user
from ..timezone_utils import brasilia_datetime, format_brasilia_datetime


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
WEEKDAYS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")


def seller_only(request: Request):
    user = current_user(request)
    if not user:
        return None, RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if user.tipo_conta != "vendedor":
        return user, RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)
    return user, None


@router.get("/vendas", response_class=HTMLResponse)
def sales_page(request: Request):
    seller, redirect = seller_only(request)
    if redirect:
        return redirect

    with SessionLocal() as database:
        rows = database.execute(
            select(Pedido, Usuario)
            .join(Usuario, Usuario.id == Pedido.cliente_id)
            .where(Pedido.vendedor_id == seller.id, Pedido.confirmado.is_(True))
            .order_by(Pedido.criado_em.desc(), Pedido.id.desc())
        ).all()

    customers: dict[int, dict] = {}
    for order, customer in rows:
        summary = customers.setdefault(customer.id, {"id": customer.id, "nome": customer.nome, "foto": customer.foto, "comprados": 0, "pagos": 0, "produtos": Counter()})
        summary["comprados"] += order.quantidade
        if order.pago:
            summary["pagos"] += order.quantidade
        summary["produtos"][order.produto_nome] += order.quantidade
    for summary in customers.values():
        summary["mais_comprado"] = summary["produtos"].most_common(1)[0][0]

    return templates.TemplateResponse(request=request, name="vendas.html", context={"usuario": seller, "csrf_token": csrf_token(request), "clientes": list(customers.values())})


@router.get("/vendas/clientes/{customer_id}", response_class=HTMLResponse)
def customer_sales_page(request: Request, customer_id: int):
    seller, redirect = seller_only(request)
    if redirect:
        return redirect

    with SessionLocal() as database:
        customer = database.get(Usuario, customer_id)
        rows = database.execute(
            select(Pedido, Produto)
            .outerjoin(Produto, Produto.id == Pedido.produto_id)
            .where(Pedido.vendedor_id == seller.id, Pedido.cliente_id == customer_id, Pedido.confirmado.is_(True))
            .order_by(Pedido.criado_em.desc(), Pedido.id.desc())
        ).all()
    if customer is None or not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente não encontrado nas suas vendas.")

    product_counts = Counter()
    weekday_counts = Counter()
    delivery_counts = Counter()
    payment_counts = Counter()
    history = []
    total_units = paid_units = 0
    for order, product in rows:
        total_units += order.quantidade
        if order.pago:
            paid_units += order.quantidade
        product_counts[order.produto_nome] += order.quantidade
        order_time = brasilia_datetime(order.criado_em)
        weekday_counts[WEEKDAYS[order_time.weekday()]] += 1
        delivery_counts["Entrega"] += 1 if order.entregar_aqui else 0
        delivery_counts["Retirada"] += 0 if order.entregar_aqui else 1
        payment_counts["Pagar depois"] += 1 if order.pagar_depois else 0
        payment_counts["Pagamento imediato"] += 0 if order.pagar_depois else 1
        with SessionLocal() as database:
            items = database.scalars(select(ItemPedido).where(ItemPedido.pedido_id == order.id).order_by(ItemPedido.id)).all()
        history.append({"id": order.id, "nome": order.produto_nome, "imagem": order.produto_imagem or (product.imagem if product else None), "quantidade": order.quantidade, "itens": [{"nome": item.variacao_nome, "quantidade": item.quantidade} for item in items], "data": format_brasilia_datetime(order.criado_em), "entrega": "Entregar aqui" if order.entregar_aqui else "Retirada", "pagamento": "Pagar depois" if order.pagar_depois else "Pagamento imediato", "situacao": "Pago" if order.pago else "Pagamento pendente", "pago": order.pago})

    def preference(counts: Counter, tie_text: str) -> str:
        top = counts.most_common()
        return tie_text if len(top) > 1 and top[0][1] == top[1][1] else top[0][0]

    estimates = {"mais_comprado": product_counts.most_common(1)[0][0], "dia_preferido": weekday_counts.most_common(1)[0][0], "entrega_preferida": preference(delivery_counts, "Sem preferência definida"), "pagamento_preferido": preference(payment_counts, "Sem preferência definida"), "pedidos": len(rows), "unidades": total_units, "pagas": paid_units}
    customer_data = {"id": customer.id, "nome": customer.nome, "foto": customer.foto}
    return templates.TemplateResponse(request=request, name="vendas_cliente.html", context={"usuario": seller, "csrf_token": csrf_token(request), "cliente": customer_data, "estimativas": estimates, "pedidos": history})
