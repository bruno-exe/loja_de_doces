from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..database import SessionLocal
from ..models import ItemCarrinho, ItemPedido, Pedido, Produto, Usuario, VariacaoProduto
from ..security import csrf_token, validate_csrf
from ..session import current_user
from .products import format_price


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


@router.get("/carrinho/quantidade")
def cart_quantity(request: Request):
    user = current_user(request)
    if not user:
        return {"quantidade": 0}
    with SessionLocal() as database:
        quantity = database.scalar(
            select(func.coalesce(func.sum(ItemCarrinho.quantidade), 0)).where(ItemCarrinho.cliente_id == user.id)
        )
    return {"quantidade": int(quantity or 0)}


@router.get("/carrinho", response_class=HTMLResponse)
def cart_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        rows = database.execute(
            select(ItemCarrinho, Produto, Usuario, VariacaoProduto)
            .join(Produto, Produto.id == ItemCarrinho.produto_id)
            .join(Usuario, Usuario.id == ItemCarrinho.vendedor_id)
            .outerjoin(VariacaoProduto, VariacaoProduto.id == ItemCarrinho.variacao_id)
            .where(ItemCarrinho.cliente_id == user.id)
            .order_by(ItemCarrinho.adicionado_em.desc(), ItemCarrinho.id.desc())
        ).all()
        grouped_products: dict[tuple[int, int | None], dict] = {}
        for item, product, seller, variation in rows:
            group_key = (product.id, item.pedido_pendente_id)
            item_available = product.ativo and (variation is None or variation.ativo)
            group = grouped_products.setdefault(group_key, {"id": product.id, "pedido_pendente_id": item.pedido_pendente_id, "produto": product.nome, "imagem": product.imagem, "vendedor": seller.nome, "vendedor_id": seller.id, "aceita_fiado": product.aceita_fiado, "disponivel": True, "quantidade": 0, "subtotal": 0, "quantidade_desconto": product.quantidade_desconto, "valor_desconto_centavos": product.valor_desconto_centavos, "itens": []})
            group["disponivel"] = group["disponivel"] and item_available
            group["itens"].append({"id": item.id, "variacao": variation.nome if variation else None, "quantidade": item.quantidade, "valor_unitario": format_price(product.valor_centavos), "valor_total": format_price(product.valor_centavos * item.quantidade), "entregar_aqui": item.entregar_aqui})
            group["quantidade"] += item.quantidade
            group["subtotal"] += product.valor_centavos * item.quantidade
        promotions = []
        subtotal = sum(group["subtotal"] for group in grouped_products.values())
        total_discount = 0
        for group in grouped_products.values():
            threshold = group["quantidade_desconto"]
            discount_value = group["valor_desconto_centavos"]
            if not threshold or not discount_value:
                continue
            kits = group["quantidade"] // threshold
            applied_discount = kits * discount_value
            group["desconto_centavos"] = applied_discount
            group["desconto"] = format_price(applied_discount)
            group["total"] = format_price(group["subtotal"] - applied_discount)
            total_discount += applied_discount
            remainder = group["quantidade"] % threshold
            missing = 0 if remainder == 0 and group["quantidade"] > 0 else threshold - remainder
            promotions.append({"produto": group["produto"], "desconto": format_price(applied_discount) if applied_discount else None, "faltam": missing, "desconto_kit": format_price(discount_value), "quantidade_kit": threshold})
        for group in grouped_products.values():
            if "desconto_centavos" not in group:
                group["desconto_centavos"] = 0
                group["desconto"] = format_price(0)
                group["total"] = format_price(group["subtotal"])
    summary = {"subtotal": format_price(subtotal), "desconto": format_price(total_discount), "total": format_price(subtotal - total_discount)}
    return templates.TemplateResponse(request=request, name="carrinho.html", context={"usuario": user, "csrf_token": csrf_token(request), "produtos": list(grouped_products.values()), "promocoes": promotions, "resumo": summary})


@router.post("/carrinho/produtos/{product_id}/finalizar")
def finish_cart_product(request: Request, product_id: int, forma_pagamento: str = Form(...), csrf: str = Form(...)):
    validate_csrf(request, csrf)
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if forma_pagamento not in {"agora", "depois"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Forma de pagamento inválida.")
    with SessionLocal() as database:
        product = database.get(Produto, product_id)
        cart_items = database.scalars(select(ItemCarrinho).where(ItemCarrinho.cliente_id == user.id, ItemCarrinho.produto_id == product_id, ItemCarrinho.pedido_pendente_id.is_(None)).order_by(ItemCarrinho.id)).all()
        if product is None or not cart_items:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produto não encontrado no carrinho.")
        variation_ids = {item.variacao_id for item in cart_items if item.variacao_id is not None}
        variation_rows = database.scalars(select(VariacaoProduto).where(VariacaoProduto.id.in_(variation_ids))).all() if variation_ids else []
        variations = {variation.id: variation for variation in variation_rows}
        if not product.ativo or any(item.variacao_id is not None and (item.variacao_id not in variations or not variations[item.variacao_id].ativo) for item in cart_items):
            return RedirectResponse("/carrinho?indisponivel=1", status_code=status.HTTP_303_SEE_OTHER)
        if forma_pagamento == "depois" and not product.aceita_fiado:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Este produto não aceita pagamento posterior.")
        total_quantity = sum(item.quantidade for item in cart_items)
        discount = (total_quantity // product.quantidade_desconto) * product.valor_desconto_centavos if product.quantidade_desconto and product.valor_desconto_centavos else 0
        order = Pedido(cliente_id=user.id, vendedor_id=product.vendedor_id, produto_id=product.id, produto_nome=product.nome, produto_descricao=product.descricao, produto_imagem=product.imagem, valor_unitario_centavos=product.valor_centavos, quantidade=total_quantity, valor_total_centavos=product.valor_centavos * total_quantity - discount, desconto_centavos=discount, pagar_depois=forma_pagamento == "depois", entregar_aqui=any(item.entregar_aqui for item in cart_items), pago=False, status="recebido", confirmado=forma_pagamento == "depois")
        database.add(order)
        database.flush()
        variation_names = {variation.id: variation.nome for variation in variation_rows}
        database.add_all(ItemPedido(pedido_id=order.id, variacao_id=item.variacao_id, variacao_nome=variation_names.get(item.variacao_id, product.nome), quantidade=item.quantidade) for item in cart_items)
        if forma_pagamento == "agora":
            for item in cart_items:
                item.pedido_pendente_id = order.id
        else:
            for item in cart_items:
                database.delete(item)
        database.commit()
        database.refresh(order)
        order_id = order.id
        seller_id = product.vendedor_id
    destination = f"/pagamentos/pedidos/{order_id}" if forma_pagamento == "agora" else "/carrinho?pedido=1"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/carrinho/pedidos/{order_id}/pagar-depois")
def change_pending_order_to_pay_later(request: Request, order_id: int, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        row = database.execute(
            select(Pedido, Produto).join(Produto, Produto.id == Pedido.produto_id)
            .where(Pedido.id == order_id, Pedido.cliente_id == user.id, Pedido.confirmado.is_(False))
        ).first()
        cart_items = database.scalars(
            select(ItemCarrinho).where(ItemCarrinho.cliente_id == user.id, ItemCarrinho.pedido_pendente_id == order_id)
        ).all()
        if row is None or not cart_items:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pagamento pendente não encontrado no carrinho.")
        order, product = row
        variation_ids = {item.variacao_id for item in cart_items if item.variacao_id is not None}
        variations = database.scalars(select(VariacaoProduto).where(VariacaoProduto.id.in_(variation_ids))).all() if variation_ids else []
        active_variation_ids = {variation.id for variation in variations if variation.ativo}
        if not product.ativo or any(item.variacao_id is not None and item.variacao_id not in active_variation_ids for item in cart_items):
            return RedirectResponse("/carrinho?indisponivel=1", status_code=status.HTTP_303_SEE_OTHER)
        if not product.aceita_fiado:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Este produto não aceita pagamento posterior.")
        order.pagar_depois = True
        order.confirmado = True
        for item in cart_items:
            database.delete(item)
        database.commit()
    return RedirectResponse("/carrinho?pedido=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/carrinho/itens/{item_id}/remover")
def remove_cart_item(request: Request, item_id: int, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        item = database.scalar(select(ItemCarrinho).where(ItemCarrinho.id == item_id, ItemCarrinho.cliente_id == user.id))
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item do carrinho não encontrado.")
        pending_order_id = item.pedido_pendente_id
        if pending_order_id is not None:
            related_items = database.scalars(select(ItemCarrinho).where(ItemCarrinho.pedido_pendente_id == pending_order_id, ItemCarrinho.id != item.id)).all()
            for related_item in related_items:
                related_item.pedido_pendente_id = None
            pending_order = database.get(Pedido, pending_order_id)
            if pending_order:
                database.delete(pending_order)
        database.delete(item)
        database.commit()
    return RedirectResponse("/carrinho?removido=1", status_code=status.HTTP_303_SEE_OTHER)
