from pathlib import Path
import re

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..database import SessionLocal
from ..models import PerfilComprador, PerfilVendedor, Usuario
from ..security import csrf_token, hash_password, validate_csrf, verify_password
from ..session import current_user


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
ACCOUNT_TYPES = {"comprador", "vendedor"}
LOCAL_EMAIL_PATTERN = re.compile(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]+@criar", re.IGNORECASE)


def normalize_name(value: str) -> str:
    name = " ".join(value.strip().split())
    return name[:1].upper() + name[1:] if name else name


def valid_name(value: str) -> bool:
    return all(character.isalpha() or character in " '-" for character in value)


def normalize_email(value: str) -> str | None:
    email = value.strip().lower()
    if LOCAL_EMAIL_PATTERN.fullmatch(email):
        local_part = email.removesuffix("@criar")
        if local_part and len(local_part) <= 64 and not local_part.startswith(".") and not local_part.endswith(".") and ".." not in local_part:
            return email
        return None
    try:
        return validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


def render_registration(
    request: Request,
    *,
    form: dict | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="cadastro.html",
        context={"usuario": None, "csrf_token": csrf_token(request), "form": form or {}, "errors": errors or []},
        status_code=status_code,
    )


def render_login(
    request: Request,
    *,
    form: dict | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"usuario": None, "csrf_token": csrf_token(request), "form": form or {}, "errors": errors or []},
        status_code=status_code,
    )


@router.get("/cadastro", response_class=HTMLResponse)
def registration_page(request: Request):
    if current_user(request):
        return RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)
    return render_registration(request)


@router.post("/cadastro", response_class=HTMLResponse)
def register(
    request: Request,
    nome: str = Form(...),
    tipo_conta: str = Form(""),
    email: str = Form(...),
    senha: str = Form(...),
    confirmar_senha: str = Form(...),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    nome = normalize_name(nome)
    tipo_conta = tipo_conta.strip().lower()
    email = email.strip().lower()
    errors: list[str] = []

    if len(nome) < 2 or len(nome) > 100:
        errors.append("O nome deve ter entre 2 e 100 caracteres.")
    elif not valid_name(nome):
        errors.append("O nome pode conter apenas letras, espaços, apóstrofo e hífen.")
    if tipo_conta not in ACCOUNT_TYPES:
        errors.append("Escolha uma conta de comprador ou vendedor.")
    normalized_email = normalize_email(email)
    if normalized_email is None:
        errors.append("Informe um e-mail válido.")
    else:
        email = normalized_email
    if len(senha) < 8 or len(senha) > 128:
        errors.append("A senha deve ter entre 8 e 128 caracteres.")
    if senha != confirmar_senha:
        errors.append("As senhas não coincidem.")

    safe_form = {"nome": nome, "tipo_conta": tipo_conta, "email": email}
    if errors:
        return render_registration(request, form=safe_form, errors=errors, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    with SessionLocal() as database:
        if database.scalar(select(Usuario.id).where(Usuario.email == email)):
            return render_registration(
                request,
                form=safe_form,
                errors=["Já existe uma conta com esse e-mail."],
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        usuario = Usuario(nome=nome, email=email, senha_hash=hash_password(senha), tipo_conta=tipo_conta)
        if tipo_conta == "comprador":
            usuario.perfil_comprador = PerfilComprador()
        else:
            usuario.perfil_vendedor = PerfilVendedor()
        database.add(usuario)
        try:
            database.commit()
        except IntegrityError:
            database.rollback()
            return render_registration(
                request,
                form=safe_form,
                errors=["Já existe uma conta com esse e-mail."],
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

    request.session.pop("csrf_token", None)
    return RedirectResponse(f"/cadastro/sucesso?tipo_conta={tipo_conta}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/cadastro/sucesso", response_class=HTMLResponse)
def registration_success(request: Request, tipo_conta: str = "comprador"):
    account_label = "Vendedor" if tipo_conta == "vendedor" else "Comprador"
    return templates.TemplateResponse(
        request=request,
        name="cadastro_sucesso.html",
        context={"usuario": None, "account_label": account_label},
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)
    return render_login(request)


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    senha: str = Form(...),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    email = email.strip().lower()

    with SessionLocal() as database:
        usuario = database.scalar(
            select(Usuario).where(Usuario.email == email, Usuario.ativo.is_(True))
        )
        if not usuario or not verify_password(usuario.senha_hash, senha):
            return render_login(
                request,
                form={"email": email},
                errors=["E-mail ou senha incorretos."],
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        user_id = usuario.id

    request.session.clear()
    request.session["user_id"] = user_id
    return RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    request.session.clear()
    response = RedirectResponse("/?saiu=1", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("comedoce_session")
    return response
