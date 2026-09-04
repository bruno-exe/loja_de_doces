from datetime import datetime, timezone
from zoneinfo import ZoneInfo


BRASILIA_TIMEZONE = ZoneInfo("America/Sao_Paulo")


def brasilia_datetime(value: datetime) -> datetime:
    """Converte datas armazenadas em UTC para o horário de Brasília."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BRASILIA_TIMEZONE)


def format_brasilia_datetime(value: datetime) -> str:
    return brasilia_datetime(value).strftime("%d/%m/%Y às %H:%M")
