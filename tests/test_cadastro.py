import re
import os
import tempfile
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "comedoce_testes_cadastro.db"
if TEST_DATABASE.exists():
    TEST_DATABASE.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import PerfilComprador, PerfilVendedor, Usuario
from app.security import verify_password


def csrf_from(response) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


def test_account_type_starts_unselected_and_is_required() -> None:
    with TestClient(app) as client:
        page = client.get("/cadastro")
        account_fields = re.findall(r'<input[^>]+name="tipo_conta"[^>]*>', page.text)
        assert len(account_fields) == 2
        assert all("checked" not in field for field in account_fields)
        assert "Criar conta vendedor" in page.text
        assert "Criar conta comprador" in page.text
        assert '>Criar minha conta</button>' in page.text

        response = client.post(
            "/cadastro",
            data={
                "csrf": csrf_from(page),
                "nome": "Carlos Lima",
                "email": "carlos@teste.com",
                "senha": "senha-segura",
                "confirmar_senha": "senha-segura",
            },
        )

    assert response.status_code == 422
    assert "Escolha uma conta de comprador ou vendedor." in response.text
    assert "Escolha Comprador ou Vendedor para criar sua conta." in page.text


def test_registers_buyer_with_hashed_password() -> None:
    with TestClient(app) as client:
        token = csrf_from(client.get("/cadastro"))
        response = client.post(
            "/cadastro",
            data={
                "csrf": token,
                "nome": "bruno araujo",
                "tipo_conta": "comprador",
                "email": "BRUNO@TESTE.COM",
                "senha": "senha-segura",
                "confirmar_senha": "senha-segura",
            },
            follow_redirects=False,
        )
    assert response.status_code == 303
    with SessionLocal() as database:
        user = database.scalar(select(Usuario).where(Usuario.email == "bruno@teste.com"))
        assert user is not None
        assert user.nome == "Bruno araujo"
        assert user.senha_hash != "senha-segura"
        assert verify_password(user.senha_hash, "senha-segura")
        assert database.scalar(select(PerfilComprador).where(PerfilComprador.usuario_id == user.id))
        assert database.scalar(select(PerfilVendedor).where(PerfilVendedor.usuario_id == user.id)) is None


def test_registers_seller_profile() -> None:
    with TestClient(app) as client:
        token = csrf_from(client.get("/cadastro"))
        response = client.post(
            "/cadastro",
            data={
                "csrf": token,
                "nome": "Maria Silva",
                "tipo_conta": "vendedor",
                "email": "maria@teste.com",
                "senha": "senha-segura",
                "confirmar_senha": "senha-segura",
            },
            follow_redirects=False,
        )
    assert response.status_code == 303
    with SessionLocal() as database:
        user = database.scalar(select(Usuario).where(Usuario.email == "maria@teste.com"))
        assert user is not None
        assert database.scalar(select(PerfilVendedor).where(PerfilVendedor.usuario_id == user.id))


def test_rejects_duplicate_email_and_invalid_csrf() -> None:
    with TestClient(app) as client:
        token = csrf_from(client.get("/cadastro"))
        data = {
            "csrf": token,
            "nome": "Ana Souza",
            "tipo_conta": "comprador",
            "email": "ana@teste.com",
            "senha": "senha-segura",
            "confirmar_senha": "senha-segura",
        }
        assert client.post("/cadastro", data=data, follow_redirects=False).status_code == 303
        data["csrf"] = csrf_from(client.get("/cadastro"))
        duplicate = client.post("/cadastro", data=data)
        invalid_csrf = client.post("/cadastro", data={**data, "csrf": "invalido"})
    assert duplicate.status_code == 422
    assert "Já existe uma conta com esse e-mail." in duplicate.text
    assert invalid_csrf.status_code == 403
