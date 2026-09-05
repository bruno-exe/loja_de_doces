from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..database import SessionLocal
from ..models import ItemPedido, Pedido, Produto, Usuario
from ..security import csrf_token
from ..session import current_user


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


@router.get("/minhas-compras", response_class=HTMLResponse)
def purchases_page(request: Request):
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        latest_purchase = func.max(Pedido.criado_em).label("ultima_compra")
        rows = database.execute(
            select(Usuario, func.sum(Pedido.quantidade), latest_purchase)
            .join(Pedido, Pedido.vendedor_id == Usuario.id)
            .where(Pedido.cliente_id == usuario.id, Pedido.confirmado.is_(True))
            .group_by(Usuario.id)
            .order_by(latest_purchase.desc(), Usuario.id.desc())
        ).all()
        sellers = [
            {"id": seller.id, "nome": seller.nome, "foto": seller.foto, "quantidade": int(quantity or 0)}
            for seller, quantity, _ in rows
        ]

    return templates.TemplateResponse(request=request, name="minhas_compras.html", context={"usuario": usuario, "csrf_token": csrf_token(request), "vendedores": sellers})


@router.get("/minhas-compras/vendedores/{seller_id}", response_class=HTMLResponse)
def seller_purchases_page(request: Request, seller_id: int):
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        seller = database.scalar(select(Usuario).where(Usuario.id == seller_id, Usuario.tipo_conta == "vendedor"))
        rows = database.execute(
            select(Pedido, Produto).outerjoin(Produto, Produto.id == Pedido.produto_id)
            .where(Pedido.cliente_id == usuario.id, Pedido.vendedor_id == seller_id, Pedido.confirmado.is_(True))
            .order_by(Pedido.criado_em.desc(), Pedido.id.desc())
        ).all()
        if seller is None or not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compras não encontradas.")
        orders = []
        for order, product in rows:
            items = database.scalars(select(ItemPedido).where(ItemPedido.pedido_id == order.id).order_by(ItemPedido.id)).all()
            orders.append({"id": order.id, "nome": order.produto_nome, "imagem": order.produto_imagem or (product.imagem if product else None), "quantidade": order.quantidade, "itens": [{"nome": item.variacao_nome, "quantidade": item.quantidade} for item in items], "situacao": "Pago" if order.pago else "Pagamento pendente", "pago": order.pago})
        seller_data = {"id": seller.id, "nome": seller.nome, "foto": seller.foto}

    return templates.TemplateResponse(request=request, name="compras_vendedor.html", context={"usuario": usuario, "csrf_token": csrf_token(request), "vendedor": seller_data, "pedidos": orders})
