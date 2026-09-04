from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from ..database import SessionLocal
from ..models import ItemCarrinho, ItemPedido, Pedido, Produto, Usuario, VariacaoProduto
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
        "valor_centavos": product.valor_centavos,
        "quantidade_desconto": product.quantidade_desconto,
        "valor_desconto_centavos": product.valor_desconto_centavos,
        "valor_desconto": format_price(product.valor_desconto_centavos) if product.valor_desconto_centavos else None,
        "aceita_fiado": product.aceita_fiado,
        "com_entrega": product.com_entrega,
        "imagem": product.imagem,
        "variacoes": [{"id": variation.id, "nome": variation.nome} for variation in product.variacoes if variation.ativo],
    }


def seller_products(seller_id: int) -> list[dict]:
    with SessionLocal() as database:
        products = database.scalars(
            select(Produto)
            .where(Produto.vendedor_id == seller_id, Produto.ativo.is_(True))
            .order_by(Produto.id.desc())
        ).all()
        cards = [product_card(product) for product in products]
        return [card for product, card in zip(products, cards) if not product.variacoes or card["variacoes"]]


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
    quantidade_desconto: str = Form(""),
    valor_desconto: str = Form(""),
    aceita_fiado: bool = Form(False),
    com_entrega: bool = Form(False),
    subcategorias: list[str] = Form([]),
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
    discount_quantity = None
    discount_in_cents = None
    errors: list[str] = []
    if len(nome) < 2 or len(nome) > 120:
        errors.append("O nome do produto deve ter entre 2 e 120 caracteres.")
    if len(descricao) < 2 or len(descricao) > 1000:
        errors.append("A descrição deve ter entre 2 e 1000 caracteres.")
    if price_in_cents is None:
        errors.append("Informe um valor válido maior que zero, com no máximo duas casas decimais.")
    if quantidade_desconto.strip() or valor_desconto.strip():
        try:
            discount_quantity = int(quantidade_desconto.strip())
        except ValueError:
            discount_quantity = None
        discount_in_cents = parse_price(valor_desconto)
        if discount_quantity is None or not 2 <= discount_quantity <= 99 or discount_in_cents is None:
            errors.append("Para criar a promoção, informe uma quantidade entre 2 e 99 e um desconto válido.")
        elif price_in_cents is not None and discount_in_cents >= price_in_cents * discount_quantity:
            errors.append("O desconto deve ser menor que o valor total do kit.")
    if imagem.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        errors.append("Use uma imagem JPG, PNG ou WebP.")
    if (focus_x is None) != (focus_y is None) or (focus_x is not None and not 0 <= focus_x <= 1) or (focus_y is not None and not 0 <= focus_y <= 1):
        errors.append("O ponto de foco selecionado é inválido.")
    variation_names: list[str] = []
    seen_variations: set[str] = set()
    for raw_name in subcategorias:
        variation_name = " ".join(raw_name.strip().split())
        if not variation_name:
            continue
        if len(variation_name) > 120:
            errors.append("Cada subcategoria deve ter no máximo 120 caracteres.")
            continue
        normalized_name = variation_name.casefold()
        if normalized_name not in seen_variations:
            seen_variations.add(normalized_name)
            variation_names.append(variation_name)
    if len(variation_names) > 20:
        errors.append("Cadastre no máximo 20 subcategorias por produto.")

    safe_form = {
        "nome": nome,
        "descricao": descricao,
        "valor": valor,
        "quantidade_desconto": quantidade_desconto,
        "valor_desconto": valor_desconto,
        "aceita_fiado": aceita_fiado,
        "com_entrega": com_entrega,
        "subcategorias": variation_names,
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
            product = Produto(
                    vendedor_id=usuario.id,
                    nome=nome,
                    descricao=descricao,
                    valor_centavos=price_in_cents,
                    quantidade_desconto=discount_quantity,
                    valor_desconto_centavos=discount_in_cents,
                    aceita_fiado=aceita_fiado,
                    com_entrega=com_entrega,
                    imagem=filename,
                )
            database.add(product)
            database.flush()
            database.add_all(
                VariacaoProduto(produto_id=product.id, nome=variation_name)
                for variation_name in variation_names
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
async def buy_product(
    request: Request,
    product_id: int,
    quantidade: int = Form(0),
    pagar_depois: bool = Form(False),
    entregar_aqui: bool = Form(False),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
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

        active_variations = [variation for variation in product.variacoes if variation.ativo]
        selected_items: list[tuple[VariacaoProduto | None, str, int]] = []
        if active_variations:
            submitted_form = await request.form()
            for variation in active_variations:
                raw_quantity = str(submitted_form.get(f"variacao_{variation.id}", "0")).strip()
                try:
                    variation_quantity = int(raw_quantity)
                except ValueError:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Quantidade inválida.")
                if variation_quantity < 0 or variation_quantity > 99:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Quantidade inválida.")
                if variation_quantity:
                    selected_items.append((variation, variation.nome, variation_quantity))
            quantidade = sum(item[2] for item in selected_items)
        else:
            selected_items.append((None, product.nome, quantidade))
        if quantidade < 1 or quantidade > 99:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Escolha pelo menos uma unidade, com limite total de 99.")

        discount_total = (quantidade // product.quantidade_desconto) * product.valor_desconto_centavos if product.quantidade_desconto and product.valor_desconto_centavos else 0
        order = Pedido(
                cliente_id=usuario.id,
                vendedor_id=product.vendedor_id,
                produto_id=product.id,
                produto_nome=product.nome,
                produto_descricao=product.descricao,
                produto_imagem=product.imagem,
                valor_unitario_centavos=product.valor_centavos,
                quantidade=quantidade,
                valor_total_centavos=product.valor_centavos * quantidade - discount_total,
                desconto_centavos=discount_total,
                pagar_depois=pagar_depois,
                entregar_aqui=entregar_aqui,
                pago=False,
                status="recebido",
            )
        database.add(order)
        database.flush()
        database.add_all(
            ItemPedido(
                pedido_id=order.id,
                variacao_id=variation.id if variation else None,
                variacao_nome=variation_name,
                quantidade=item_quantity,
            )
            for variation, variation_name, item_quantity in selected_items
        )
        seller_id = product.vendedor_id
        database.commit()
        database.refresh(order)
        order_id = order.id

    destination = f"/vendedores/{seller_id}?pedido=1" if pagar_depois else f"/pagamentos/pedidos/{order_id}"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/produtos/{product_id}/carrinho")
async def add_product_to_cart(
    request: Request,
    product_id: int,
    quantidade: int = Form(0),
    pagar_depois: bool = Form(False),
    entregar_aqui: bool = Form(False),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        product = database.scalar(
            select(Produto).join(Usuario, Usuario.id == Produto.vendedor_id)
            .where(Produto.id == product_id, Produto.ativo.is_(True), Usuario.ativo.is_(True))
        )
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produto não encontrado.")
        if product.vendedor_id == user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Você não pode adicionar seu próprio produto ao carrinho.")
        if pagar_depois and not product.aceita_fiado:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Este produto não aceita pagamento posterior.")
        if entregar_aqui and not product.com_entrega:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Este produto não possui entrega.")

        active_variations = [variation for variation in product.variacoes if variation.ativo]
        selected: list[tuple[int | None, int]] = []
        if active_variations:
            submitted_form = await request.form()
            for variation in active_variations:
                try:
                    selected_quantity = int(str(submitted_form.get(f"variacao_{variation.id}", "0")).strip())
                except ValueError:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Quantidade inválida.")
                if selected_quantity < 0 or selected_quantity > 99:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Quantidade inválida.")
                if selected_quantity:
                    selected.append((variation.id, selected_quantity))
        else:
            selected.append((None, quantidade))
        total_quantity = sum(item[1] for item in selected)
        if total_quantity < 1 or total_quantity > 99:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Escolha pelo menos uma unidade, com limite total de 99.")

        for variation_id, selected_quantity in selected:
            filters = [ItemCarrinho.cliente_id == user.id, ItemCarrinho.produto_id == product.id, ItemCarrinho.pedido_pendente_id.is_(None)]
            filters.append(ItemCarrinho.variacao_id == variation_id if variation_id is not None else ItemCarrinho.variacao_id.is_(None))
            existing = database.scalar(select(ItemCarrinho).where(*filters))
            if existing:
                if existing.quantidade + selected_quantity > 99:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A quantidade deste item no carrinho ultrapassaria 99.")
                existing.quantidade += selected_quantity
                existing.pagar_depois = pagar_depois
                existing.entregar_aqui = entregar_aqui
            else:
                database.add(ItemCarrinho(cliente_id=user.id, vendedor_id=product.vendedor_id, produto_id=product.id, variacao_id=variation_id, quantidade=selected_quantity, pagar_depois=pagar_depois, entregar_aqui=entregar_aqui))
        database.commit()
    return RedirectResponse(f"/vendedores/{product.vendedor_id}?carrinho=1", status_code=status.HTTP_303_SEE_OTHER)
