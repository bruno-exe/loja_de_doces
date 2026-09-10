from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread

from sqlalchemy import select

from ..config import settings
from ..database import SessionLocal
from ..models import DispositivoNotificacao, Mensagem, NotificacaoMensagem, NotificacaoVenda, Pedido, Usuario


SEND_LOCK = Lock()


def _firebase_messaging():
    path = Path(settings.firebase_credentials_path)
    if not settings.firebase_credentials_path or not path.is_file():
        return None
    import firebase_admin
    from firebase_admin import credentials, messaging
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(str(path)))
    return messaging


def send_sale_notification(order_id: int) -> None:
    with SEND_LOCK, SessionLocal() as database:
        order = database.get(Pedido, order_id)
        if not order or not order.confirmado:
            return
        tokens = database.scalars(select(DispositivoNotificacao).where(DispositivoNotificacao.usuario_id == order.vendedor_id, DispositivoNotificacao.ativo.is_(True))).all()
        if not tokens:
            return
        record = database.scalar(select(NotificacaoVenda).where(NotificacaoVenda.pedido_id == order.id))
        if record and record.status == "enviada":
            return
        if record is None:
            record = NotificacaoVenda(pedido_id=order.id)
            database.add(record)
        buyer = database.get(Usuario, order.cliente_id)
        try:
            messaging = _firebase_messaging()
            if messaging is None:
                raise RuntimeError("Credencial do Firebase não disponível.")
            messages = [messaging.Message(
                notification=messaging.Notification(title="Nova venda no Come Doce", body=f"{buyer.nome}: {order.quantidade}x {order.produto_nome}"),
                data={"url": "/vendas", "pedido_id": str(order.id)},
                android=messaging.AndroidConfig(priority="high", notification=messaging.AndroidNotification(channel_id="vendas_som_v2")),
                token=device.token,
            ) for device in tokens]
            response = messaging.send_each(messages)
            for device, result in zip(tokens, response.responses):
                if not result.success and result.exception and result.exception.__class__.__name__ in {"UnregisteredError", "SenderIdMismatchError"}:
                    device.ativo = False
            if response.success_count == 0:
                raise RuntimeError("Nenhum dispositivo recebeu a notificação.")
            record.status = "enviada"
            record.erro = None
            record.enviada_em = datetime.now(timezone.utc)
        except Exception as exc:
            record.status = "erro"
            record.erro = str(exc)[:500]
        database.commit()


def queue_sale_notification(order_id: int) -> None:
    Thread(target=send_sale_notification, args=(order_id,), daemon=True, name=f"push-sale-{order_id}").start()


def send_message_notification(message_id: int) -> None:
    with SEND_LOCK, SessionLocal() as database:
        message = database.get(Mensagem, message_id)
        if not message:
            return
        tokens = database.scalars(select(DispositivoNotificacao).where(DispositivoNotificacao.usuario_id == message.destinatario_id, DispositivoNotificacao.ativo.is_(True))).all()
        if not tokens:
            return
        record = database.scalar(select(NotificacaoMensagem).where(NotificacaoMensagem.mensagem_id == message.id))
        if record and record.status == "enviada":
            return
        if record is None:
            record = NotificacaoMensagem(mensagem_id=message.id)
            database.add(record)
        sender = database.get(Usuario, message.remetente_id)
        sender_name = sender.nome.split()[0] if sender and sender.nome.strip() else "Alguém"
        try:
            messaging = _firebase_messaging()
            if messaging is None:
                raise RuntimeError("Credencial do Firebase não disponível.")
            messages = [messaging.Message(
                notification=messaging.Notification(title=f"Nova mensagem de {sender_name}", body=message.texto[:140]),
                data={"url": f"/mensagens/{message.conversa_id}", "mensagem_id": str(message.id)},
                android=messaging.AndroidConfig(priority="high", notification=messaging.AndroidNotification(channel_id="mensagens_som_v1")),
                token=device.token,
            ) for device in tokens]
            response = messaging.send_each(messages)
            for device, result in zip(tokens, response.responses):
                if not result.success and result.exception and result.exception.__class__.__name__ in {"UnregisteredError", "SenderIdMismatchError"}:
                    device.ativo = False
            if response.success_count == 0:
                raise RuntimeError("Nenhum dispositivo recebeu a notificação.")
            record.status = "enviada"
            record.erro = None
            record.enviada_em = datetime.now(timezone.utc)
        except Exception as exc:
            record.status = "erro"
            record.erro = str(exc)[:500]
        database.commit()


def queue_message_notification(message_id: int) -> None:
    Thread(target=send_message_notification, args=(message_id,), daemon=True, name=f"push-message-{message_id}").start()
