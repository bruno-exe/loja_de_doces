from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select

from ..database import SessionLocal
from ..models import Conversa, Mensagem, Usuario
from ..security import csrf_token, validate_csrf
from ..session import current_user
from ..timezone_utils import format_brasilia_datetime


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
MAX_MESSAGE_LENGTH = 2000


def _logged_user(request: Request):
    usuario = current_user(request)
    if not usuario:
        return None, RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return usuario, None


def _conversation_for_user(database, conversation_id: int, user_id: int) -> Conversa:
    conversation = database.scalar(
        select(Conversa).where(
            Conversa.id == conversation_id,
            or_(Conversa.cliente_id == user_id, Conversa.vendedor_id == user_id),
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversa não encontrada.")
    return conversation


@router.get("/mensagens", response_class=HTMLResponse)
def conversations_page(request: Request):
    usuario, redirect = _logged_user(request)
    if redirect:
        return redirect

    conversations = []
    with SessionLocal() as database:
        rows = database.scalars(
            select(Conversa)
            .where(or_(Conversa.cliente_id == usuario.id, Conversa.vendedor_id == usuario.id))
            .order_by(Conversa.atualizada_em.desc(), Conversa.id.desc())
        ).all()
        for conversation in rows:
            other_id = conversation.vendedor_id if conversation.cliente_id == usuario.id else conversation.cliente_id
            other = database.get(Usuario, other_id)
            last_message = database.scalar(
                select(Mensagem).where(Mensagem.conversa_id == conversation.id).order_by(Mensagem.id.desc()).limit(1)
            )
            unread = len(database.scalars(select(Mensagem).where(
                Mensagem.conversa_id == conversation.id,
                Mensagem.destinatario_id == usuario.id,
                Mensagem.lida.is_(False),
            )).all())
            conversations.append({"id": conversation.id, "pessoa": other, "ultima": last_message, "nao_lidas": unread})

    return templates.TemplateResponse(request=request, name="mensagens.html", context={
        "usuario": usuario, "csrf_token": csrf_token(request), "conversas": conversations,
    })


@router.post("/mensagens/iniciar/{seller_id}")
def start_conversation(request: Request, seller_id: int, csrf: str = Form(...)):
    usuario, redirect = _logged_user(request)
    if redirect:
        return redirect
    validate_csrf(request, csrf)
    if usuario.id == seller_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Você não pode conversar consigo mesmo.")

    with SessionLocal() as database:
        seller = database.scalar(select(Usuario).where(
            Usuario.id == seller_id, Usuario.tipo_conta == "vendedor", Usuario.ativo.is_(True)
        ))
        if seller is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendedor não encontrado.")
        conversation = database.scalar(select(Conversa).where(
            Conversa.cliente_id == usuario.id, Conversa.vendedor_id == seller_id
        ))
        if conversation is None:
            conversation = Conversa(cliente_id=usuario.id, vendedor_id=seller_id)
            database.add(conversation)
            database.commit()
            database.refresh(conversation)
        conversation_id = conversation.id
    return RedirectResponse(f"/mensagens/{conversation_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/mensagens/{conversation_id}", response_class=HTMLResponse)
def conversation_page(request: Request, conversation_id: int):
    usuario, redirect = _logged_user(request)
    if redirect:
        return redirect

    with SessionLocal() as database:
        conversation = _conversation_for_user(database, conversation_id, usuario.id)
        other_id = conversation.vendedor_id if conversation.cliente_id == usuario.id else conversation.cliente_id
        other = database.get(Usuario, other_id)
        messages = database.scalars(
            select(Mensagem).where(Mensagem.conversa_id == conversation.id).order_by(Mensagem.id)
        ).all()
        changed = False
        for message in messages:
            if message.destinatario_id == usuario.id and not message.lida:
                message.lida = True
                changed = True
        if changed:
            database.commit()
        message_data = [{
            "texto": message.texto,
            "minha": message.remetente_id == usuario.id,
            "horario": format_brasilia_datetime(message.enviada_em),
        } for message in messages]
        other_data = {"id": other.id, "nome": other.nome, "foto": other.foto} if other else None

    return templates.TemplateResponse(request=request, name="conversa.html", context={
        "usuario": usuario, "csrf_token": csrf_token(request), "conversa_id": conversation_id,
        "pessoa": other_data, "mensagens": message_data, "erro": request.query_params.get("erro"),
        "max_message_length": MAX_MESSAGE_LENGTH,
    })


@router.post("/mensagens/{conversation_id}")
def send_message(request: Request, conversation_id: int, csrf: str = Form(...), texto: str = Form("")):
    usuario, redirect = _logged_user(request)
    if redirect:
        return redirect
    validate_csrf(request, csrf)
    texto = texto.strip()
    if not texto or len(texto) > MAX_MESSAGE_LENGTH:
        return RedirectResponse(f"/mensagens/{conversation_id}?erro=mensagem", status_code=status.HTTP_303_SEE_OTHER)

    with SessionLocal() as database:
        conversation = _conversation_for_user(database, conversation_id, usuario.id)
        recipient_id = conversation.vendedor_id if conversation.cliente_id == usuario.id else conversation.cliente_id
        database.add(Mensagem(
            conversa_id=conversation.id, remetente_id=usuario.id,
            destinatario_id=recipient_id, texto=texto,
        ))
        conversation.atualizada_em = datetime.now(timezone.utc)
        database.commit()
    return RedirectResponse(f"/mensagens/{conversation_id}", status_code=status.HTTP_303_SEE_OTHER)
