import secrets
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ..admin_access import ADMIN_EMAIL, is_admin
from ..database import SessionLocal
from ..models import ConfiguracaoSorteioBau, JogadaBau, LancamentoPontos, MovimentoCustodia, Usuario
from ..security import csrf_token, validate_csrf
from ..session import current_user
from ..timezone_utils import BRASILIA_TIMEZONE


router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
DRAW_LOCK = Lock()


def _today():
    return datetime.now(BRASILIA_TIMEZONE).date()


def _game_config(database):
    config = database.get(ConfiguracaoSorteioBau, 1)
    if config is None:
        config = ConfiguracaoSorteioBau(id=1)
        database.add(config)
        database.flush()
    return config


def _play_payload(play: JogadaBau | None):
    if play is None:
        return {"played": False, "remaining_chests": 3}
    chosen = [int(value) for value in play.baus_escolhidos.split(",")]
    prizes = [{"chest": chest, "points": play.premio_pontos if chest == play.bau_premiado else 0} for chest in chosen]
    return {"played": True, "remaining_chests": 0, "prizes": prizes, "total_points": play.premio_pontos}


@router.get("/jogos", response_class=HTMLResponse)
def games_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if user.tipo_conta != "comprador":
        return RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request=request, name="jogos.html", context={"usuario": user, "csrf_token": csrf_token(request)})


@router.get("/jogos/quadro", response_class=HTMLResponse)
def game_board(request: Request):
    user = current_user(request)
    if not user or user.tipo_conta != "comprador":
        raise HTTPException(status_code=403, detail="Somente compradores podem jogar.")
    return templates.TemplateResponse(request=request, name="jogo_quadro.html", context={"csrf_token": csrf_token(request)})


@router.get("/api/jogos/baus/status")
def game_status(request: Request):
    user = current_user(request)
    if not user or user.tipo_conta != "comprador":
        raise HTTPException(status_code=403, detail="Somente compradores podem jogar.")
    with SessionLocal() as database:
        play = database.scalar(select(JogadaBau).where(JogadaBau.usuario_id == user.id, JogadaBau.data_jogo == _today()))
        return _play_payload(play)


@router.post("/api/jogos/baus/abrir")
async def open_chests(request: Request):
    user = current_user(request)
    if not user or user.tipo_conta != "comprador":
        raise HTTPException(status_code=403, detail="Somente compradores podem jogar.")
    data = await request.json()
    validate_csrf(request, str(data.get("csrf", "")))
    try:
        chosen = [int(value) for value in data.get("chests", [])]
    except (TypeError, ValueError):
        chosen = []
    if len(chosen) != 3 or len(set(chosen)) != 3 or any(value < 1 or value > 8 for value in chosen):
        raise HTTPException(status_code=422, detail="Escolha exatamente três baús diferentes.")

    with DRAW_LOCK, SessionLocal() as database:
        existing = database.scalar(select(JogadaBau).where(JogadaBau.usuario_id == user.id, JogadaBau.data_jogo == _today()))
        if existing:
            return JSONResponse(_play_payload(existing), status_code=409)
        config = _game_config(database)
        special = config.ativo and config.pontos_sorteados < config.limite_pontos
        if special:
            intended = 1000 if secrets.randbelow(10) < 7 else (secrets.randbelow(3) + 2) * 1000
            prize = min(intended, config.limite_pontos - config.pontos_sorteados)
            config.pontos_sorteados += prize
            if config.pontos_sorteados >= config.limite_pontos:
                config.ativo = False
        else:
            prize = secrets.randbelow(4) + 1 if secrets.randbelow(10) == 0 else 0
        winning_chest = secrets.choice(chosen) if prize else None
        play = JogadaBau(usuario_id=user.id, data_jogo=_today(), baus_escolhidos=",".join(map(str, chosen)), bau_premiado=winning_chest, premio_pontos=prize, modo_especial=special)
        database.add(play)
        database.flush()
        previous_total = config.total_pontos_distribuidos
        config.total_pontos_distribuidos += prize
        config.atualizado_em = datetime.now(timezone.utc)
        if prize:
            database.add(LancamentoPontos(usuario_id=user.id, jogada_bau_id=play.id, quantidade=prize, motivo="Prêmio do jogo dos baús"))
            custody_cents = config.total_pontos_distribuidos // 10 - previous_total // 10
            admin = database.scalar(select(Usuario).where(func.lower(Usuario.email) == ADMIN_EMAIL))
            if admin and custody_cents:
                database.add(MovimentoCustodia(vendedor_id=admin.id, jogada_bau_id=play.id, custodia_centavos=custody_cents, reserva_centavos=0, motivo="Liquidez de prêmio do jogo dos baús"))
        database.commit()
        return _play_payload(play)


@router.get("/jogos/controle", response_class=HTMLResponse)
def game_control(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not is_admin(user):
        raise HTTPException(status_code=404, detail="Página não encontrada.")
    with SessionLocal() as database:
        config = _game_config(database)
        total_players = int(database.scalar(select(func.count(JogadaBau.id))) or 0)
        database.commit()
        data = {"ativo": config.ativo, "limite": config.limite_pontos, "sorteados": config.pontos_sorteados, "restantes": max(0, config.limite_pontos - config.pontos_sorteados), "total": config.total_pontos_distribuidos, "jogadas": total_players}
    return templates.TemplateResponse(request=request, name="jogos_controle.html", context={"usuario": user, "csrf_token": csrf_token(request), "sorteio": data})


@router.post("/jogos/controle/iniciar")
def start_game_draw(request: Request, limite_pontos: int = Form(...), csrf: str = Form(...)):
    user = current_user(request)
    if not is_admin(user):
        raise HTTPException(status_code=404, detail="Página não encontrada.")
    validate_csrf(request, csrf)
    if limite_pontos <= 0:
        return RedirectResponse("/jogos/controle?erro=limite", status_code=status.HTTP_303_SEE_OTHER)
    with DRAW_LOCK, SessionLocal() as database:
        config = _game_config(database)
        config.ativo = True
        config.limite_pontos = limite_pontos
        config.pontos_sorteados = 0
        config.atualizado_em = datetime.now(timezone.utc)
        database.commit()
    return RedirectResponse("/jogos/controle?iniciado=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/jogos/controle/parar")
def stop_game_draw(request: Request, csrf: str = Form(...)):
    user = current_user(request)
    if not is_admin(user):
        raise HTTPException(status_code=404, detail="Página não encontrada.")
    validate_csrf(request, csrf)
    with DRAW_LOCK, SessionLocal() as database:
        config = _game_config(database)
        config.ativo = False
        config.atualizado_em = datetime.now(timezone.utc)
        database.commit()
    return RedirectResponse("/jogos/controle?parado=1", status_code=status.HTTP_303_SEE_OTHER)
