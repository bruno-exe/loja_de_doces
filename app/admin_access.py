from .models import Usuario


ADMIN_EMAIL = "bruno@criar"


def is_admin(user: Usuario | None) -> bool:
    return bool(user and user.email.strip().casefold() == ADMIN_EMAIL)
