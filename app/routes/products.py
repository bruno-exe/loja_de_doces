from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from ..database import SessionLocal
from ..models import Pedido, Produto, Usuario
from ..security import csrf_token, validate_csrf
from ..services.profile_photo import ProfilePhotoError, process_seller_image
from ..session import current_user


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
PRODUCT_PHOTO_DIR = Path(__file__).resolve().parent.parent / "uploads" / "products"
PRODUCT_PHOTO_DIR.mkdir(parents=True, exist_ok=True)


def format_price(value_in_cents: int) -> str:
    units, cents = divmod(value_in_cents, 100)
    formatted_units = f"{units:,}".replace(",", ".")
    return f"R$ {formatted_units},{cents:02d}"


def product_card(product: Produto) -> dict:
    return {
        "id": product.id,
        "nome": product.nome,
        "descricao": product.descricao,
        "valor": format_price(product.valor_centavos),
        "aceita_fiado": product.aceita_fiado,
        "com_entrega": product.com_entrega,
        "imagem": product.imagem,
    }


def seller_products(seller_id: int) -> list[dict]:
    with SessionLocal() as database:
        products = database.scalars(
            select(Produto)
            .where(Produto.vendedor_id == seller_id, Produto.ativo.is_(True))
            .order_by(Produto.id.desc())
        ).all()
        return [product_card(product) for product in products]


def parse_price(value: str) -> int | None:
    normalized = value.strip().lower().replace("r$", "").replace(" ", "")
    if not normalized:
        return None
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        decimal_value = Decimal(normalized)
    except InvalidOperation:
        return None
    if decimal_value <= 0 or decimal_value > Decimal("999999.99"):
        return None
    if decimal_value.as_tuple().exponent < -2:
        return None
    return int(decimal_value * 100)


def render_new_product(
    request: Request,
    usuario,
    *,
    form: dict | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="novo_produto.html",
        context={
            "usuario": usuario,
            "csrf_token": csrf_token(request),
            "form": form or {},
            "errors": errors or [],
            "produtos": seller_products(usuario.id),
        },
        status_code=status_code,
    )


@router.get("/produtos/novo", response_class=HTMLResponse)
def new_product_page(request: Request):
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if usuario.tipo_conta != "vendedor":
        return RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)
    return render_new_product(request, usuario)


@router.post("/produtos/novo", response_class=HTMLResponse)
async def create_product(
    request: Request,
    nome: str = Form(...),
    descricao: str = Form(...),
    valor: str = Form(...),
    aceita_fiado: bool = Form(False),
    com_entrega: bool = Form(False),
    imagem: UploadFile = File(...),
    focus_x: float | None = Form(None),
    focus_y: float | None = Form(None),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if usuario.tipo_conta != "vendedor":
        return RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)

    nome = " ".join(nome.strip().split())
    descricao = descricao.strip()
    price_in_cents = parse_price(valor)
    errors: list[str] = []
    if len(nome) < 2 or len(nome) > 120:
        errors.append("O nome do produto deve ter entre 2 e 120 caracteres.")
    if len(descricao) < 2 or len(descricao) > 1000:
        errors.append("A descrição deve ter entre 2 e 1000 caracteres.")
    if price_in_cents is None:
        errors.append("Informe um valor válido maior que zero, com no máximo duas casas decimais.")
    if imagem.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        errors.append("Use uma imagem JPG, PNG ou WebP.")
    if (focus_x is None) != (focus_y is None) or (focus_x is not None and not 0 <= focus_x <= 1) or (focus_y is not None and not 0 <= focus_y <= 1):
        errors.append("O ponto de foco selecionado é inválido.")

    safe_form = {
        "nome": nome,
        "descricao": descricao,
        "valor": valor,
        "aceita_fiado": aceita_fiado,
        "com_entrega": com_entrega,
    }
    contents = await imagem.read(5 * 1024 * 1024 + 1)
    if len(contents) > 5 * 1024 * 1024:
        errors.append("A imagem deve ter no máximo 5 MB.")
    if errors:
        return render_new_product(request, usuario, form=safe_form, errors=errors, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    try:
        processed_image = process_seller_image(contents, focus_x, focus_y)
    except ProfilePhotoError:
        return render_new_product(
            request,
            usuario,
            form=safe_form,
            errors=["Não foi possível processar essa imagem."],
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    filename = f"{uuid4().hex}.webp"
    image_path = PRODUCT_PHOTO_DIR / filename
    image_path.write_bytes(processed_image)
    try:
        with SessionLocal() as database:
            database.add(
                Produto(
                    vendedor_id=usuario.id,
                    nome=nome,
                    descricao=descricao,
                    valor_centavos=price_in_cents,
                    aceita_fiado=aceita_fiado,
                    com_entrega=com_entrega,
                    imagem=filename,
                )
            )
            database.commit()
    except Exception:
        image_path.unlink(missing_ok=True)
        raise

    return RedirectResponse("/produtos/novo?criado=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/produtos/{product_id}/excluir")
def delete_product(request: Request, product_id: int, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if usuario.tipo_conta != "vendedor":
        return RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        product = database.scalar(
            select(Produto).where(Produto.id == product_id, Produto.vendedor_id == usuario.id)
        )
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produto não encontrado.")
        image_filename = product.imagem
        database.delete(product)
        database.commit()

    (PRODUCT_PHOTO_DIR / Path(image_filename).name).unlink(missing_ok=True)
    return RedirectResponse("/produtos/novo?excluido=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/produtos/{product_id}/comprar")
def buy_product(
    request: Request,
    product_id: int,
    quantidade: int = Form(...),
    pagar_depois: bool = Form(False),
    entregar_aqui: bool = Form(False),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if quantidade < 1 or quantidade > 99:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Quantidade inválida.")

    with SessionLocal() as database:
        product = database.scalar(
            select(Produto)
            .join(Usuario, Usuario.id == Produto.vendedor_id)
            .where(Produto.id == product_id, Produto.ativo.is_(True), Usuario.ativo.is_(True))
        )
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produto não encontrado.")
        if product.vendedor_id == usuario.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Você não pode comprar seu próprio produto.")
        if pagar_depois and not product.aceita_fiado:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Este produto não aceita pagamento posterior.")
        if entregar_aqui and not product.com_entrega:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Este produto não possui entrega.")

        order = Pedido(
                cliente_id=usuario.id,
                vendedor_id=product.vendedor_id,
                produto_id=product.id,
                produto_nome=product.nome,
                produto_descricao=product.descricao,
                produto_imagem=product.imagem,
                valor_unitario_centavos=product.valor_centavos,
                quantidade=quantidade,
                valor_total_centavos=product.valor_centavos * quantidade,
                pagar_depois=pagar_depois,
                entregar_aqui=entregar_aqui,
                pago=False,
                status="recebido",
            )
        database.add(order)
        seller_id = product.vendedor_id
        database.commit()
        database.refresh(order)
        order_id = order.id

    destination = f"/vendedores/{seller_id}?pedido=1" if pagar_depois else f"/pagamentos/pedidos/{order_id}"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
