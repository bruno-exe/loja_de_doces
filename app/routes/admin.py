from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import aliased

from ..admin_access import is_admin
from ..database import SessionLocal
from ..models import ComprovantePagamento, Pedido, Usuario
from ..security import csrf_token
from ..session import current_user


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


@router.get("/adm", response_class=HTMLResponse)
def admin_receipts(request: Request, loja: str = "", comprador: str = "", situacao: str = ""):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not is_admin(user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Página não encontrada.")

    seller = aliased(Usuario)
    buyer = aliased(Usuario)
    with SessionLocal() as database:
        rows = database.execute(
            select(Pedido, seller, buyer, ComprovantePagamento)
            .join(seller, seller.id == Pedido.vendedor_id)
            .join(buyer, buyer.id == Pedido.cliente_id)
            .outerjoin(ComprovantePagamento, ComprovantePagamento.pedido_id == Pedido.id)
            .where(Pedido.confirmado.is_(True))
            .order_by(Pedido.criado_em.desc(), Pedido.id.desc())
        ).all()

    items = []
    for order, store, customer, receipt in rows:
        state = "Pago" if order.pago else ("Processando" if receipt and receipt.ocr_processado_em is None else "Pendente")
        if loja and loja.casefold() not in store.nome.casefold():
            continue
        if comprador and comprador.casefold() not in customer.nome.casefold():
            continue
        if situacao and situacao.casefold() != state.casefold():
            continue
        items.append({"pedido_id": order.id, "loja": store.nome, "comprador": customer.nome, "situacao": state, "tem_comprovante": receipt is not None})
    return templates.TemplateResponse(request=request, name="adm.html", context={"usuario": user, "csrf_token": csrf_token(request), "itens": items, "filtros": {"loja": loja, "comprador": comprador, "situacao": situacao}})
