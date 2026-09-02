from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..database import SessionLocal
from ..models import PerfilVendedor, Produto, Usuario, VisitaPerfilVendedor
from ..security import csrf_token
from ..session import current_user
from .products import seller_products


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


@router.get("/doces", response_class=HTMLResponse)
def sweets_page(request: Request):
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        product_count = (
            select(func.count(Produto.id))
            .where(Produto.vendedor_id == Usuario.id, Produto.ativo.is_(True))
            .correlate(Usuario)
            .scalar_subquery()
        )
        rows = database.execute(
            select(Usuario, PerfilVendedor, product_count)
            .join(PerfilVendedor, PerfilVendedor.usuario_id == Usuario.id)
            .where(
                Usuario.tipo_conta == "vendedor",
                Usuario.ativo.is_(True),
                Usuario.id != usuario.id,
            )
            .order_by(Usuario.nome, Usuario.id)
        ).all()
        sellers = [
            {"id": seller.id, "nome": seller.nome, "foto": seller.foto, "frase": profile.frase_apresentacao, "quantidade_doces": quantity}
            for seller, profile, quantity in rows
        ]

    return templates.TemplateResponse(
        request=request,
        name="doces.html",
        context={"usuario": usuario, "csrf_token": csrf_token(request), "vendedores": sellers},
    )


@router.get("/vendedores/{seller_id}", response_class=HTMLResponse)
def public_seller_page(request: Request, seller_id: int):
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        row = database.execute(
            select(Usuario, PerfilVendedor)
            .join(PerfilVendedor, PerfilVendedor.usuario_id == Usuario.id)
            .where(
                Usuario.id == seller_id,
                Usuario.tipo_conta == "vendedor",
                Usuario.ativo.is_(True),
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendedor não encontrado.")

    seller, seller_profile = row
    if seller.id != usuario.id:
        with SessionLocal() as database:
            database.add(VisitaPerfilVendedor(vendedor_id=seller.id, visitante_id=usuario.id))
            database.commit()
    products = seller_products(seller.id)
    public_seller = {
        "id": seller.id,
        "nome": seller.nome,
        "foto": seller.foto,
        "frase": seller_profile.frase_apresentacao,
        "is_owner": seller.id == usuario.id,
        "reputacao": 1,
        "quantidade_doces": len(products),
    }
    return templates.TemplateResponse(
        request=request,
        name="vendedor_publico.html",
        context={"usuario": usuario, "csrf_token": csrf_token(request), "vendedor": public_seller, "doces": products},
    )
