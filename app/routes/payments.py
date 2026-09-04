from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from PIL import Image, UnidentifiedImageError

from ..database import SessionLocal
from ..models import ComprovantePagamento, ItemCarrinho, ItemPedido, LancamentoPontos, Pedido, PerfilVendedor, Produto, Usuario
from ..security import csrf_token, validate_csrf
from ..session import current_user
from ..timezone_utils import format_brasilia_datetime
from ..services.pix_receipt_ocr import PixReceiptOcrError, extract_pix_receipt
from .products import format_price


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
RECEIPT_DIR = Path(__file__).resolve().parent.parent / "private" / "payment_receipts"
RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
MAX_RECEIPT_SIZE = 10 * 1024 * 1024


def process_receipt_ocr(receipt_id: int) -> None:
    with SessionLocal() as database:
        receipt = database.get(ComprovantePagamento, receipt_id)
        if receipt is None or receipt.ocr_processado_em is not None:
            return
        path = RECEIPT_DIR / Path(receipt.arquivo).name
        try:
            data = extract_pix_receipt(path)
            receipt.ocr_valor = str(data["valor"]) if data["valor"] is not None else None
            receipt.ocr_data = data["data"]
            receipt.ocr_hora = data["hora"]
            receipt.ocr_destinatario = data["destinatario"]
            receipt.ocr_cpf_cnpj_destinatario = data["cpf_cnpj_destinatario"]
            receipt.ocr_pagador = data["pagador"]
            receipt.ocr_instituicao = data["instituicao"]
            receipt.ocr_e2e_id = data["e2e_id"]
            receipt.texto_ocr = data["texto_ocr"] or None
            receipt.ocr_erro = None if receipt.texto_ocr else "Nenhum texto foi reconhecido na imagem."
        except PixReceiptOcrError as exc:
            receipt.ocr_erro = str(exc)
        except Exception:
            receipt.ocr_erro = "O OCR encontrou um erro inesperado ao analisar esta imagem."
        receipt.ocr_processado_em = datetime.now(timezone.utc)
        database.commit()


@router.get("/pagamentos/pedidos/{order_id}", response_class=HTMLResponse)
def payment_page(request: Request, order_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        row = database.execute(
            select(Pedido, Usuario, PerfilVendedor, Produto)
            .join(Usuario, Usuario.id == Pedido.vendedor_id)
            .join(PerfilVendedor, PerfilVendedor.usuario_id == Usuario.id)
            .outerjoin(Produto, Produto.id == Pedido.produto_id)
            .where(Pedido.id == order_id, Pedido.cliente_id == user.id)
        ).first()
        receipt = database.scalar(select(ComprovantePagamento).where(ComprovantePagamento.pedido_id == order_id))
        order_items = database.scalars(select(ItemPedido).where(ItemPedido.pedido_id == order_id).order_by(ItemPedido.id)).all()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido não encontrado.")

    if receipt and receipt.ocr_processado_em is None:
        process_receipt_ocr(receipt.id)
        with SessionLocal() as database:
            receipt = database.get(ComprovantePagamento, receipt.id)
    order, seller, seller_profile, product = row
    payment = {
        "id": order.id,
        "nome": order.produto_nome,
        "descricao": order.produto_descricao or (product.descricao if product else "Descrição indisponível."),
        "imagem": order.produto_imagem or (product.imagem if product else None),
        "quantidade": order.quantidade,
        "valor_unitario": format_price(order.valor_unitario_centavos),
        "valor_total": format_price(order.valor_total_centavos),
        "desconto": format_price(order.desconto_centavos) if order.desconto_centavos else None,
        "pago": order.pago,
        "itens": [{"nome": item.variacao_nome, "quantidade": item.quantidade} for item in order_items],
        "comprovante": {"id": receipt.id, "enviado_em": format_brasilia_datetime(receipt.enviado_em), "texto_ocr": receipt.texto_ocr, "ocr_erro": receipt.ocr_erro} if receipt else None,
    }
    seller_data = {"id": seller.id, "nome": seller.nome, "foto": seller.foto, "chave_pix": seller_profile.chave_pix}
    return templates.TemplateResponse(request=request, name="pagamento_pix.html", context={"usuario": user, "csrf_token": csrf_token(request), "vendedor": seller_data, "pedido": payment})


@router.post("/pagamentos/pedidos/{order_id}/comprovante")
async def upload_payment_receipt(request: Request, order_id: int, comprovante: UploadFile = File(...), csrf: str = Form(...)):
    validate_csrf(request, csrf)
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        order = database.scalar(select(Pedido).where(Pedido.id == order_id, Pedido.cliente_id == user.id))
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido não encontrado.")
        if database.scalar(select(ComprovantePagamento.id).where(ComprovantePagamento.pedido_id == order_id)):
            return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_comprovante=ja_enviado", status_code=status.HTTP_303_SEE_OTHER)

    if comprovante.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_comprovante=tipo", status_code=status.HTTP_303_SEE_OTHER)
    contents = await comprovante.read(MAX_RECEIPT_SIZE + 1)
    if len(contents) > MAX_RECEIPT_SIZE:
        return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_comprovante=tamanho", status_code=status.HTTP_303_SEE_OTHER)
    try:
        with Image.open(BytesIO(contents)) as source:
            source.load()
            image = source.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_comprovante=invalido", status_code=status.HTTP_303_SEE_OTHER)

    filename = f"{uuid4().hex}.jpg"
    destination = RECEIPT_DIR / filename
    image.save(destination, "JPEG", quality=95, optimize=True)
    try:
        with SessionLocal() as database:
            receipt = ComprovantePagamento(pedido_id=order_id, cliente_id=user.id, arquivo=filename)
            database.add(receipt)
            database.flush()
            database.add(LancamentoPontos(
                usuario_id=user.id,
                comprovante_id=receipt.id,
                quantidade=250,
                motivo="Comprovante de pagamento enviado",
            ))
            pending_cart_items = database.scalars(
                select(ItemCarrinho).where(ItemCarrinho.cliente_id == user.id, ItemCarrinho.pedido_pendente_id == order_id)
            ).all()
            for cart_item in pending_cart_items:
                database.delete(cart_item)
            order = database.get(Pedido, order_id)
            if order:
                order.confirmado = True
            database.commit()
            database.refresh(receipt)
            receipt_id = receipt.id
    except IntegrityError:
        destination.unlink(missing_ok=True)
        return RedirectResponse(f"/pagamentos/pedidos/{order_id}?erro_comprovante=ja_enviado", status_code=status.HTTP_303_SEE_OTHER)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    process_receipt_ocr(receipt_id)
    return RedirectResponse(f"/pagamentos/pedidos/{order_id}?comprovante=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/pagamentos/comprovantes/{receipt_id}/imagem")
def payment_receipt_image(request: Request, receipt_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        row = database.execute(
            select(ComprovantePagamento, Pedido)
            .join(Pedido, Pedido.id == ComprovantePagamento.pedido_id)
            .where(ComprovantePagamento.id == receipt_id)
        ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comprovante não encontrado.")
    receipt, order = row
    if user.id not in {order.cliente_id, order.vendedor_id}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comprovante não encontrado.")
    path = RECEIPT_DIR / Path(receipt.arquivo).name
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arquivo do comprovante não encontrado.")
    return FileResponse(path, media_type="image/jpeg", filename=f"comprovante-pedido-{order.id}.jpg", content_disposition_type="inline")
