from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from threading import Lock
import unicodedata
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from PIL import Image, UnidentifiedImageError

from ..database import SessionLocal
from ..admin_access import is_admin
from ..models import ComprovantePagamento, IntegracaoMercadoPagoVendedor, ItemCarrinho, ItemPedido, LancamentoPontos, MovimentoCustodia, PagamentoPedidoMercadoPago, Pedido, PerfilVendedor, Produto, Usuario
from ..security import csrf_token, validate_csrf
from ..session import current_user
from ..timezone_utils import brasilia_datetime, format_brasilia_datetime
from ..services.pix_receipt_ocr import PixReceiptOcrError, extract_pix_receipt
from .products import format_price


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
RECEIPT_DIR = Path(__file__).resolve().parent.parent / "private" / "payment_receipts"
RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
MAX_RECEIPT_SIZE = 10 * 1024 * 1024
OCR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="receipt-ocr")
OCR_QUEUE_LOCK = Lock()
OCR_QUEUED_IDS: set[int] = set()


def format_receipt_datetime(date_value: str | None, time_value: str | None) -> str | None:
    formatted_date = None
    if date_value:
        try:
            formatted_date = datetime.strptime(date_value, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            formatted_date = date_value
    if formatted_date and time_value:
        return f"{formatted_date} às {time_value}"
    return formatted_date or time_value


def _first_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = unicodedata.normalize("NFKD", value.strip())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    words = [word.casefold() for word in normalized.split() if word]
    return words[0] if words else None


def _receipt_matches_order(data: dict, order: Pedido, seller_profile: PerfilVendedor | None) -> bool:
    try:
        paid_value = Decimal(str(data.get("valor")))
        paid_date = datetime.strptime(str(data.get("data")), "%Y-%m-%d").date()
    except (InvalidOperation, TypeError, ValueError):
        return False
    expected_value = Decimal(order.valor_total_centavos) / 100
    expected_recipient = _first_name(seller_profile.nome_recebedor_pix if seller_profile else None)
    extracted_recipient = _first_name(data.get("destinatario"))
    purchase_date = brasilia_datetime(order.criado_em).date()
    return bool(
        paid_value == expected_value
        and paid_date >= purchase_date
        and expected_recipient
        and extracted_recipient == expected_recipient
    )


def process_receipt_ocr(receipt_id: int, *, force: bool = False) -> None:
    with SessionLocal() as database:
        receipt = database.get(ComprovantePagamento, receipt_id)
        if receipt is None or (receipt.ocr_processado_em is not None and not force):
            return
        path = RECEIPT_DIR / Path(receipt.arquivo).name
        receipt.ocr_valor = None
        receipt.ocr_data = None
        receipt.ocr_hora = None
        receipt.ocr_destinatario = None
        receipt.ocr_cpf_cnpj_destinatario = None
        receipt.ocr_pagador = None
        receipt.ocr_instituicao = None
        receipt.ocr_e2e_id = None
        receipt.texto_ocr = None
        receipt.ocr_erro = None
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
            order = database.get(Pedido, receipt.pedido_id)
            seller_profile = database.scalar(select(PerfilVendedor).where(PerfilVendedor.usuario_id == order.vendedor_id)) if order else None
            if order and _receipt_matches_order(data, order, seller_profile):
                order.pago = True
                order.confirmado = True
                if order.desconto_centavos > 0 and not database.scalar(select(LancamentoPontos.id).where(LancamentoPontos.comprovante_id == receipt.id)):
                    database.add(LancamentoPontos(usuario_id=order.cliente_id, comprovante_id=receipt.id, quantidade=250, motivo="Pagamento promocional validado"))
                if order.desconto_centavos > 0 and not database.scalar(select(MovimentoCustodia.id).where(MovimentoCustodia.comprovante_id == receipt.id)):
                    database.add(MovimentoCustodia(vendedor_id=order.vendedor_id, comprovante_id=receipt.id, custodia_centavos=25, reserva_centavos=1, motivo="Custódia de promoção validada"))
        except PixReceiptOcrError as exc:
            receipt.ocr_erro = str(exc)
        except Exception:
            receipt.ocr_erro = "O OCR encontrou um erro inesperado ao analisar esta imagem."
        receipt.ocr_processado_em = datetime.now(timezone.utc)
        database.commit()


def _finish_queued_receipt(receipt_id: int) -> None:
    try:
        process_receipt_ocr(receipt_id)
    finally:
        with OCR_QUEUE_LOCK:
            OCR_QUEUED_IDS.discard(receipt_id)


def queue_receipt_ocr(receipt_id: int, *, force: bool = False) -> bool:
    if force:
        with SessionLocal() as database:
            receipt = database.get(ComprovantePagamento, receipt_id)
            if receipt is None:
                return False
            receipt.ocr_processado_em = None
            receipt.ocr_erro = None
            database.commit()
    with OCR_QUEUE_LOCK:
        if receipt_id in OCR_QUEUED_IDS:
            return False
        OCR_QUEUED_IDS.add(receipt_id)
    OCR_EXECUTOR.submit(_finish_queued_receipt, receipt_id)
    return True


def queue_pending_receipts() -> None:
    with SessionLocal() as database:
        pending_ids = database.scalars(select(ComprovantePagamento.id).where(ComprovantePagamento.ocr_processado_em.is_(None))).all()
    for receipt_id in pending_ids:
        queue_receipt_ocr(receipt_id)


@router.get("/pagamentos/pedidos/{order_id}", response_class=HTMLResponse)
def payment_page(request: Request, order_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        query = (
            select(Pedido, Usuario, PerfilVendedor, Produto)
            .join(Usuario, Usuario.id == Pedido.vendedor_id)
            .join(PerfilVendedor, PerfilVendedor.usuario_id == Usuario.id)
            .outerjoin(Produto, Produto.id == Pedido.produto_id)
            .where(Pedido.id == order_id)
        )
        if not is_admin(user):
            query = query.where(or_(Pedido.cliente_id == user.id, Pedido.vendedor_id == user.id))
        row = database.execute(query).first()
        receipt = database.scalar(select(ComprovantePagamento).where(ComprovantePagamento.pedido_id == order_id))
        receipt_points = database.scalar(select(LancamentoPontos.quantidade).where(LancamentoPontos.comprovante_id == receipt.id)) if receipt else None
        seller_mp = database.scalar(select(IntegracaoMercadoPagoVendedor.id).where(
            IntegracaoMercadoPagoVendedor.vendedor_id == row[0].vendedor_id,
            IntegracaoMercadoPagoVendedor.ativo.is_(True),
        )) if row else None
        mp_payment = database.scalar(select(PagamentoPedidoMercadoPago).where(PagamentoPedidoMercadoPago.pedido_id == order_id))
        order_items = database.scalars(select(ItemPedido).where(ItemPedido.pedido_id == order_id).order_by(ItemPedido.id)).all()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido não encontrado.")

    if receipt and receipt.ocr_processado_em is None:
        queue_receipt_ocr(receipt.id)
    order, seller, seller_profile, product = row
    is_seller_view = user.id == order.vendedor_id
    is_admin_view = is_admin(user) and user.id not in {order.cliente_id, order.vendedor_id}
    with SessionLocal() as database:
        buyer = database.get(Usuario, order.cliente_id)
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
        "comprovante": {"id": receipt.id, "enviado_em": format_brasilia_datetime(receipt.enviado_em), "processando": receipt.ocr_processado_em is None, "pontos_recebidos": int(receipt_points or 0), "texto_ocr": receipt.texto_ocr, "ocr_erro": receipt.ocr_erro, "valor": f"R$ {receipt.ocr_valor.replace('.', ',')}" if receipt.ocr_valor else None, "pagador": receipt.ocr_pagador, "destinatario": receipt.ocr_destinatario, "data_hora": format_receipt_datetime(receipt.ocr_data, receipt.ocr_hora)} if receipt else None,
    }
    seller_data = {"id": seller.id, "nome": seller.nome, "foto": seller.foto, "chave_pix": seller_profile.chave_pix}
    buyer_data = {"id": buyer.id, "nome": buyer.nome, "foto": buyer.foto} if buyer else None
    mp_data = {"available": bool(seller_mp), "status": mp_payment.status_pagamento if mp_payment else None}
    return templates.TemplateResponse(request=request, name="pagamento_pix.html", context={"usuario": user, "csrf_token": csrf_token(request), "vendedor": seller_data, "cliente": buyer_data, "pedido": payment, "mercadopago": mp_data, "visualizacao_vendedor": is_seller_view, "visualizacao_adm": is_admin_view})


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
    queue_receipt_ocr(receipt_id)
    return RedirectResponse(f"/pagamentos/pedidos/{order_id}?comprovante=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/pagamentos/comprovantes/{receipt_id}/reanalisar")
def reprocess_payment_receipt(request: Request, receipt_id: int, csrf: str = Form(...)):
    validate_csrf(request, csrf)
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
    if user.id not in {order.cliente_id, order.vendedor_id} and not is_admin(user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comprovante não encontrado.")

    queue_receipt_ocr(receipt.id, force=True)
    return RedirectResponse(
        f"/pagamentos/pedidos/{order.id}?reanalisado=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/pagamentos/pedidos/{order_id}/aprovar")
def manually_approve_payment(request: Request, order_id: int, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        order = database.get(Pedido, order_id)
        if order is None or (user.id != order.vendedor_id and not is_admin(user)):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido não encontrado.")
        receipt_exists = database.scalar(select(ComprovantePagamento.id).where(ComprovantePagamento.pedido_id == order.id)) is not None
        if is_admin(user) and user.id != order.vendedor_id and not receipt_exists:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Este pedido ainda não possui comprovante.")
        order.pago = True
        order.confirmado = True
        database.commit()
    destination = f"/adm?aprovado=1" if is_admin(user) and user.id != order.vendedor_id else f"/vendas/clientes/{order.cliente_id}?pago=1"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


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
    if user.id not in {order.cliente_id, order.vendedor_id} and not is_admin(user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comprovante não encontrado.")
    path = RECEIPT_DIR / Path(receipt.arquivo).name
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arquivo do comprovante não encontrado.")
    return FileResponse(path, media_type="image/jpeg", filename=f"comprovante-pedido-{order.id}.jpg", content_disposition_type="inline")
