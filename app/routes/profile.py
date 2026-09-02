from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..database import SessionLocal
from ..models import PerfilComprador, PerfilVendedor, Produto, VisitaPerfilVendedor
from ..security import csrf_token, validate_csrf
from ..services.profile_photo import FaceNotFoundError, ProfilePhotoError, process_profile_photo, process_seller_image
from ..session import current_user


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
PHOTO_DIR = Path(__file__).resolve().parent.parent / "uploads" / "profile_photos"
PHOTO_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/perfil", response_class=HTMLResponse)
def profile(request: Request):
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    product_count = 0
    visit_count = 0
    with SessionLocal() as database:
        if usuario.tipo_conta == "vendedor":
            perfil = database.scalar(select(PerfilVendedor).where(PerfilVendedor.usuario_id == usuario.id))
            product_count = database.scalar(
                select(func.count(Produto.id)).where(
                    Produto.vendedor_id == usuario.id,
                    Produto.ativo.is_(True),
                )
            ) or 0
            visit_count = database.scalar(
                select(func.count(VisitaPerfilVendedor.id)).where(
                    VisitaPerfilVendedor.vendedor_id == usuario.id,
                    VisitaPerfilVendedor.visitado_em >= datetime.now(timezone.utc) - timedelta(hours=24),
                )
            ) or 0
            template_name = "perfil_vendedor.html"
        else:
            perfil = database.scalar(select(PerfilComprador).where(PerfilComprador.usuario_id == usuario.id))
            template_name = "perfil_comprador.html"

    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={"usuario": usuario, "perfil": perfil, "quantidade_produtos": product_count, "visitas_24h": visit_count, "csrf_token": csrf_token(request)},
    )


@router.get("/conta/editar", response_class=HTMLResponse)
def edit_account(request: Request):
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    seller_profile = None
    if usuario.tipo_conta == "vendedor":
        with SessionLocal() as database:
            seller_profile = database.scalar(
                select(PerfilVendedor).where(PerfilVendedor.usuario_id == usuario.id)
            )
    return templates.TemplateResponse(
        request=request,
        name="editar_conta.html",
        context={"usuario": usuario, "perfil_vendedor": seller_profile, "csrf_token": csrf_token(request)},
    )


@router.post("/conta/apresentacao")
def update_seller_presentation(
    request: Request,
    frase_apresentacao: str = Form(""),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if usuario.tipo_conta != "vendedor":
        return RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)

    phrase = " ".join(frase_apresentacao.strip().split())
    if len(phrase) > 240:
        return RedirectResponse("/conta/editar?erro_apresentacao=tamanho", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        seller_profile = database.scalar(
            select(PerfilVendedor).where(PerfilVendedor.usuario_id == usuario.id)
        )
        if seller_profile is None:
            seller_profile = PerfilVendedor(usuario_id=usuario.id)
            database.add(seller_profile)
        seller_profile.frase_apresentacao = phrase or None
        database.commit()
    return RedirectResponse("/conta/editar?apresentacao_salva=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/conta/pix")
def update_seller_pix(request: Request, chave_pix: str = Form(""), nome_recebedor_pix: str = Form(""), csrf: str = Form(...)):
    validate_csrf(request, csrf)
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if usuario.tipo_conta != "vendedor":
        return RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)
    pix = chave_pix.strip()
    receiver_name = " ".join(nome_recebedor_pix.strip().split())
    if len(pix) > 140 or len(receiver_name) > 140:
        return RedirectResponse("/conta/editar?erro_pix=tamanho", status_code=status.HTTP_303_SEE_OTHER)
    with SessionLocal() as database:
        profile = database.scalar(select(PerfilVendedor).where(PerfilVendedor.usuario_id == usuario.id))
        profile.chave_pix = pix or None
        profile.nome_recebedor_pix = receiver_name or None
        database.commit()
    return RedirectResponse("/conta/editar?pix_salva=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/conta/foto")
async def update_profile_photo(
    request: Request,
    foto: UploadFile = File(...),
    csrf: str = Form(...),
    face_x: float | None = Form(None),
    face_y: float | None = Form(None),
):
    validate_csrf(request, csrf)
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    if foto.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        return RedirectResponse("/conta/editar?erro_foto=tipo", status_code=status.HTTP_303_SEE_OTHER)
    if (face_x is None) != (face_y is None):
        return RedirectResponse("/conta/editar?erro_foto=coordenada", status_code=status.HTTP_303_SEE_OTHER)
    if face_x is not None and not 0.0 <= face_x <= 1.0:
        return RedirectResponse("/conta/editar?erro_foto=coordenada", status_code=status.HTTP_303_SEE_OTHER)
    if face_y is not None and not 0.0 <= face_y <= 1.0:
        return RedirectResponse("/conta/editar?erro_foto=coordenada", status_code=status.HTTP_303_SEE_OTHER)

    contents = await foto.read(5 * 1024 * 1024 + 1)
    if len(contents) > 5 * 1024 * 1024:
        return RedirectResponse("/conta/editar?erro_foto=tamanho", status_code=status.HTTP_303_SEE_OTHER)
    try:
        if usuario.tipo_conta == "vendedor":
            processed = process_seller_image(contents, face_x, face_y)
        else:
            processed = process_profile_photo(contents, face_x, face_y)
    except FaceNotFoundError:
        return RedirectResponse("/conta/editar?erro_foto=rosto", status_code=status.HTTP_303_SEE_OTHER)
    except ProfilePhotoError:
        return RedirectResponse("/conta/editar?erro_foto=invalida", status_code=status.HTTP_303_SEE_OTHER)

    filename = f"{uuid4().hex}.webp"
    (PHOTO_DIR / filename).write_bytes(processed)
    old_filename = None
    with SessionLocal() as database:
        database_user = database.get(type(usuario), usuario.id)
        old_filename = database_user.foto
        database_user.foto = filename
        database.commit()
    if old_filename:
        (PHOTO_DIR / Path(old_filename).name).unlink(missing_ok=True)
    return RedirectResponse("/perfil?foto=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/conta/foto/remover")
def remove_profile_photo(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    usuario = current_user(request)
    if not usuario:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    filename = None
    with SessionLocal() as database:
        database_user = database.get(type(usuario), usuario.id)
        filename = database_user.foto
        database_user.foto = None
        database.commit()
    if filename:
        (PHOTO_DIR / Path(filename).name).unlink(missing_ok=True)
    return RedirectResponse("/conta/editar?foto_removida=1", status_code=status.HTTP_303_SEE_OTHER)
