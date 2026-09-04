from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from ..database import SessionLocal
from ..models import Produto, VariacaoProduto
from ..security import csrf_token, validate_csrf
from ..session import current_user


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


def seller_only(request: Request):
    user = current_user(request)
    if not user:
        return None, RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if user.tipo_conta != "vendedor":
        return user, RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)
    return user, None


@router.get("/pausar", response_class=HTMLResponse)
def availability_page(request: Request):
    seller, redirect = seller_only(request)
    if redirect:
        return redirect
    with SessionLocal() as database:
        products = database.scalars(select(Produto).where(Produto.vendedor_id == seller.id).order_by(Produto.nome, Produto.id)).all()
        rows = [{"id": product.id, "nome": product.nome, "ativo": product.ativo, "variacoes": [{"id": variation.id, "nome": variation.nome, "ativo": variation.ativo} for variation in product.variacoes]} for product in products]
    return templates.TemplateResponse(request=request, name="pausar.html", context={"usuario": seller, "csrf_token": csrf_token(request), "produtos": rows})


@router.post("/pausar/produtos/{product_id}")
def toggle_product(request: Request, product_id: int, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    seller, redirect = seller_only(request)
    if redirect:
        return redirect
    with SessionLocal() as database:
        product = database.scalar(select(Produto).where(Produto.id == product_id, Produto.vendedor_id == seller.id))
        if product is None or product.variacoes:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produto não encontrado.")
        product.ativo = not product.ativo
        database.commit()
    return RedirectResponse("/pausar", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/pausar/variacoes/{variation_id}")
def toggle_variation(request: Request, variation_id: int, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    seller, redirect = seller_only(request)
    if redirect:
        return redirect
    with SessionLocal() as database:
        variation = database.scalar(select(VariacaoProduto).join(Produto).where(VariacaoProduto.id == variation_id, Produto.vendedor_id == seller.id))
        if variation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subitem não encontrado.")
        variation.ativo = not variation.ativo
        database.commit()
    return RedirectResponse("/pausar", status_code=status.HTTP_303_SEE_OTHER)
