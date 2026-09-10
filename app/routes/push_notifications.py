from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from ..database import SessionLocal
from ..models import DispositivoNotificacao
from ..security import validate_csrf
from ..session import current_user


router = APIRouter()


@router.post("/api/notificacoes/dispositivos")
async def register_notification_device(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Entre na conta para ativar notificações.")
    data = await request.json()
    validate_csrf(request, str(data.get("csrf", "")))
    token = str(data.get("token", "")).strip()
    if not token or len(token) > 512:
        raise HTTPException(status_code=422, detail="Token de notificação inválido.")
    with SessionLocal() as database:
        device = database.scalar(select(DispositivoNotificacao).where(DispositivoNotificacao.token == token))
        if device is None:
            device = DispositivoNotificacao(usuario_id=user.id, token=token)
            database.add(device)
        else:
            device.usuario_id = user.id
            device.ativo = True
            device.atualizado_em = datetime.now(timezone.utc)
        database.commit()
    return {"success": True}
