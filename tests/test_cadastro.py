import re
import os
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "comedoce_testes_cadastro.db"
if TEST_DATABASE.exists():
    TEST_DATABASE.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"

from fastapi.testclient import TestClient
from sqlalchemy import select
from PIL import Image

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import ComprovantePagamento, Pedido, PerfilComprador, PerfilVendedor, Produto, Usuario, VisitaPerfilVendedor
from app.security import hash_password, verify_password
from app.routes import profile as profile_routes
from app.routes import products as product_routes
from app.routes import payments as payment_routes
from app.services.profile_photo import process_profile_photo, process_seller_image


def csrf_from(response) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


def create_test_user(email: str, account_type: str, *, active: bool = True) -> Usuario:
    with SessionLocal() as database:
        user = Usuario(
            nome="Cliente de Teste" if account_type == "comprador" else "Vendedor de Teste",
            email=email,
            senha_hash=hash_password("senha-segura"),
            tipo_conta=account_type,
            ativo=active,
        )
        if account_type == "comprador":
            user.perfil_comprador = PerfilComprador()
        else:
            user.perfil_vendedor = PerfilVendedor()
        database.add(user)
        database.commit()
        database.refresh(user)
        return user


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


def test_accepts_criar_email_and_allows_login() -> None:
    with TestClient(app) as client:
        registration_page = client.get("/cadastro")
        registration = client.post(
            "/cadastro",
            data={
                "csrf": csrf_from(registration_page),
                "nome": "Conta Local",
                "tipo_conta": "comprador",
                "email": "CONTA.LOCAL@CRIAR",
                "senha": "senha-segura",
                "confirmar_senha": "senha-segura",
            },
            follow_redirects=False,
        )
        assert registration.status_code == 303

        login_page = client.get("/login")
        login = client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": "conta.local@criar", "senha": "senha-segura"},
            follow_redirects=False,
        )

    assert login.status_code == 303
    assert login.headers["location"] == "/perfil"
    with SessionLocal() as database:
        assert database.scalar(select(Usuario).where(Usuario.email == "conta.local@criar"))


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


def test_login_session_buyer_profile_and_logout() -> None:
    create_test_user("login-comprador@teste.com", "comprador")

    with TestClient(app) as client:
        protected = client.get("/perfil", follow_redirects=False)
        assert protected.status_code == 303
        assert protected.headers["location"] == "/login"

        login_page = client.get("/login")
        wrong_password = client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": "login-comprador@teste.com", "senha": "senha-errada"},
        )
        assert wrong_password.status_code == 401
        assert "E-mail ou senha incorretos." in wrong_password.text

        successful_login = client.post(
            "/login",
            data={
                "csrf": csrf_from(wrong_password),
                "email": "LOGIN-COMPRADOR@TESTE.COM",
                "senha": "senha-segura",
            },
            follow_redirects=False,
        )
        assert successful_login.status_code == 303
        assert successful_login.headers["location"] == "/perfil"
        session_cookie = successful_login.headers.get("set-cookie", "").lower()
        assert "comedoce_session=" in session_cookie
        assert "httponly" in session_cookie
        assert "samesite=lax" in session_cookie
        assert "max-age=1209600" in session_cookie

        profile = client.get("/perfil")
        assert profile.status_code == 200
        assert "Resumo do comprador" in profile.text
        assert "login-comprador@teste.com" in profile.text
        assert "Meu perfil" in profile.text
        assert 'class="side-menu-button"' in profile.text
        assert 'id="sideMenu"' in profile.text
        assert 'action="/logout"' in profile.text
        assert '/static/js/side_menu.js' in profile.text

        logout = client.post(
            "/logout",
            data={"csrf": csrf_from(profile)},
            follow_redirects=False,
        )
        assert logout.status_code == 303
        assert client.get("/perfil", follow_redirects=False).headers["location"] == "/login"


def test_seller_receives_seller_profile() -> None:
    create_test_user("login-vendedor@teste.com", "vendedor")

    with TestClient(app) as client:
        login_page = client.get("/login")
        assert client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": "login-vendedor@teste.com", "senha": "senha-segura"},
            follow_redirects=False,
        ).status_code == 303
        profile = client.get("/perfil")

    assert profile.status_code == 200
    assert "Perfil do vendedor" in profile.text
    assert "Criar produto" in profile.text
    assert "0 produtos cadastrados" in profile.text
    assert 'href="/produtos/novo">Ver</a>' in profile.text


def test_inactive_user_cannot_log_in() -> None:
    create_test_user("conta-inativa@teste.com", "comprador", active=False)

    with TestClient(app) as client:
        login_page = client.get("/login")
        response = client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": "conta-inativa@teste.com", "senha": "senha-segura"},
        )

    assert response.status_code == 401
    assert "E-mail ou senha incorretos." in response.text


def test_side_menu_only_appears_for_authenticated_user() -> None:
    with TestClient(app) as client:
        public_page = client.get("/")

    assert 'id="sideMenu"' not in public_page.text
    assert 'class="side-menu-button"' not in public_page.text
    assert 'action="/logout"' not in public_page.text


def test_edit_account_and_manual_face_photo(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(profile_routes, "PHOTO_DIR", tmp_path)
    create_test_user("foto-comprador@teste.com", "comprador")
    source = Image.new("RGB", (900, 1200), (225, 170, 120))
    source_bytes = BytesIO()
    source.save(source_bytes, "JPEG")

    with TestClient(app) as client:
        login_page = client.get("/login")
        client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": "foto-comprador@teste.com", "senha": "senha-segura"},
        )
        profile = client.get("/perfil")
        assert "Editar conta" in profile.text

        edit_page = client.get("/conta/editar")
        assert edit_page.status_code == 200
        assert "Detectar automaticamente" in edit_page.text
        assert "Escolher rosto" in edit_page.text
        assert 'name="face_x"' in edit_page.text
        assert 'name="face_y"' in edit_page.text

        update = client.post(
            "/conta/foto",
            data={"csrf": csrf_from(edit_page), "face_x": "0.50", "face_y": "0.42"},
            files={"foto": ("foto.jpg", source_bytes.getvalue(), "image/jpeg")},
            follow_redirects=False,
        )
        assert update.status_code == 303
        assert update.headers["location"] == "/perfil?foto=1"

        with SessionLocal() as database:
            user = database.scalar(select(Usuario).where(Usuario.email == "foto-comprador@teste.com"))
            assert user.foto
            saved_path = tmp_path / user.foto
            assert saved_path.exists()
            with Image.open(saved_path) as saved:
                assert saved.size == (600, 600)
                assert saved.format == "WEBP"

        updated_profile = client.get("/perfil")
        assert "/uploads/profile_photos/" in updated_profile.text
        remove_page = client.get("/conta/editar")
        removed = client.post(
            "/conta/foto/remover",
            data={"csrf": csrf_from(remove_page)},
            follow_redirects=False,
        )
        assert removed.status_code == 303
        assert not saved_path.exists()


def test_manual_point_works_when_no_face_is_detected(monkeypatch) -> None:
    from app.services import profile_photo as photo_service

    monkeypatch.setattr(photo_service, "_detect_faces", lambda image: [])
    source = Image.new("RGB", (1000, 1000), "orange")
    source_bytes = BytesIO()
    source.save(source_bytes, "PNG")

    output = process_profile_photo(source_bytes.getvalue(), 0.55, 0.40)
    with Image.open(BytesIO(output)) as result:
        assert result.size == (600, 600)


def test_automatic_detection_uses_detected_face(monkeypatch) -> None:
    from app.services import profile_photo as photo_service

    monkeypatch.setattr(photo_service, "_detect_faces", lambda image: [(300, 220, 260, 260)])
    source = Image.new("RGB", (1000, 1000), "orange")
    source_bytes = BytesIO()
    source.save(source_bytes, "PNG")

    output = process_profile_photo(source_bytes.getvalue(), None, None)
    with Image.open(BytesIO(output)) as result:
        assert result.size == (600, 600)


def test_seller_can_edit_presentation_and_buyer_cannot() -> None:
    create_test_user("apresentacao-vendedor@teste.com", "vendedor")
    create_test_user("apresentacao-comprador@teste.com", "comprador")

    with TestClient(app) as seller_client:
        login_page = seller_client.get("/login")
        seller_client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": "apresentacao-vendedor@teste.com", "senha": "senha-segura"},
        )
        edit_page = seller_client.get("/conta/editar")
        assert "Frase de apresentação" in edit_page.text
        assert 'maxlength="240"' in edit_page.text
        assert "Definir foco" in edit_page.text
        assert "Centralizar imagem" in edit_page.text
        assert "Escolher rosto" not in edit_page.text
        assert "Detectar automaticamente" not in edit_page.text
        assert 'data-photo-mode="seller"' in edit_page.text
        saved = seller_client.post(
            "/conta/apresentacao",
            data={
                "csrf": csrf_from(edit_page),
                "frase_apresentacao": "  Doces artesanais   feitos com carinho.  ",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
        assert saved.headers["location"] == "/conta/editar?apresentacao_salva=1"
        profile = seller_client.get("/perfil")
        assert "Doces artesanais feitos com carinho." in profile.text

    with TestClient(app) as buyer_client:
        login_page = buyer_client.get("/login")
        buyer_client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": "apresentacao-comprador@teste.com", "senha": "senha-segura"},
        )
        edit_page = buyer_client.get("/conta/editar")
        assert 'name="frase_apresentacao"' not in edit_page.text
        denied = buyer_client.post(
            "/conta/apresentacao",
            data={"csrf": csrf_from(edit_page), "frase_apresentacao": "Não deve salvar"},
            follow_redirects=False,
        )
        assert denied.status_code == 303
        assert denied.headers["location"] == "/perfil"


def test_sweets_page_is_available_to_buyer_and_seller_and_only_lists_sellers() -> None:
    buyer = create_test_user("vitrine-comprador@teste.com", "comprador")
    other_buyer = create_test_user("outro-comprador@teste.com", "comprador")
    active_seller = create_test_user("vitrine-vendedor@teste.com", "vendedor")
    other_seller = create_test_user("outra-vendedora@teste.com", "vendedor")
    create_test_user("vendedor-inativo@teste.com", "vendedor", active=False)
    with SessionLocal() as database:
        seller_profile = database.scalar(
            select(PerfilVendedor).where(PerfilVendedor.usuario_id == active_seller.id)
        )
        seller_profile.frase_apresentacao = "Brigadeiros preparados todos os dias."
        database.get(Usuario, active_seller.id).nome = "Loja principal"
        database.get(Usuario, other_seller.id).nome = "Outra loja"
        database.add_all([
            Produto(vendedor_id=other_seller.id, nome="Trufa", descricao="Trufa artesanal", valor_centavos=500, imagem="trufa.webp"),
            Produto(vendedor_id=other_seller.id, nome="Brownie", descricao="Brownie artesanal", valor_centavos=800, imagem="brownie.webp"),
        ])
        database.commit()

    with TestClient(app) as anonymous_client:
        anonymous = anonymous_client.get("/doces", follow_redirects=False)
        assert anonymous.status_code == 303
        assert anonymous.headers["location"] == "/login"

    for viewer_email in ("vitrine-comprador@teste.com", "vitrine-vendedor@teste.com"):
        with TestClient(app) as client:
            login_page = client.get("/login")
            client.post(
                "/login",
                data={"csrf": csrf_from(login_page), "email": viewer_email, "senha": "senha-segura"},
            )
            page = client.get("/doces")
            assert page.status_code == 200
            assert "outro-comprador@teste.com" not in page.text
            assert "vendedor-inativo@teste.com" not in page.text
            assert "vitrine-vendedor@teste.com" not in page.text
            assert 'href="/doces"' in page.text
            assert 'aria-current="page"' in page.text
            if viewer_email == "vitrine-vendedor@teste.com":
                assert f'href="/vendedores/{active_seller.id}"' not in page.text
                assert "Brigadeiros preparados todos os dias." not in page.text
                assert "Outra loja" in page.text
                assert f'href="/vendedores/{other_seller.id}"' in page.text
                assert "2 doces cadastrados" in page.text
            else:
                assert "Loja principal" in page.text
                assert "Brigadeiros preparados todos os dias." in page.text
                assert f'href="/vendedores/{active_seller.id}"' in page.text
                assert "Outra loja" in page.text
                assert "0 doces cadastrados" in page.text
                assert "2 doces cadastrados" in page.text

            public_seller = client.get(f"/vendedores/{active_seller.id}")
            assert public_seller.status_code == 200
            assert "Doces de Loja principal" in public_seller.text
            assert "Nenhum doce cadastrado" in public_seller.text
            assert "Brigadeiros preparados todos os dias." in public_seller.text
            assert "Reputação" in public_seller.text
            assert "1/10" in public_seller.text
            assert "Doces cadastrados" in public_seller.text
            assert "vitrine-vendedor@teste.com" not in public_seller.text
            if viewer_email == "vitrine-vendedor@teste.com":
                assert "Editar minha apresentação" in public_seller.text
            else:
                assert "Editar minha apresentação" not in public_seller.text

            not_a_seller = client.get(f"/vendedores/{other_buyer.id}")
            assert not_a_seller.status_code == 404

    with SessionLocal() as database:
        visits = database.scalars(
            select(VisitaPerfilVendedor).where(VisitaPerfilVendedor.vendedor_id == active_seller.id)
        ).all()
        assert len(visits) == 1
        assert visits[0].visitante_id == buyer.id

    with TestClient(app) as owner_client:
        login_page = owner_client.get("/login")
        owner_client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": active_seller.email, "senha": "senha-segura"},
        )
        profile = owner_client.get("/perfil")
        assert "Visitas nas últimas 24 horas" in profile.text
        assert '<strong>1</strong>' in profile.text

        with SessionLocal() as database:
            visit = database.get(VisitaPerfilVendedor, visits[0].id)
            visit.visitado_em = datetime.now(timezone.utc) - timedelta(hours=25)
            database.commit()

        expired_profile = owner_client.get("/perfil")
        assert "Visitas nas últimas 24 horas" in expired_profile.text
        assert '<strong>0</strong>' in expired_profile.text


def test_seller_image_uses_focus_without_face_detection(monkeypatch) -> None:
    from app.services import profile_photo as photo_service

    def fail_if_face_detection_runs(image):
        raise AssertionError("A detecção facial não deve ser usada para imagens de vendedor.")

    monkeypatch.setattr(photo_service, "_detect_faces", fail_if_face_detection_runs)
    source = Image.new("RGB", (1400, 800), "orange")
    source_bytes = BytesIO()
    source.save(source_bytes, "PNG")

    output = process_seller_image(source_bytes.getvalue(), 0.80, 0.50)
    with Image.open(BytesIO(output)) as result:
        assert result.size == (600, 600)


def test_seller_creates_product_with_clean_form_and_default_options(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(product_routes, "PRODUCT_PHOTO_DIR", tmp_path)
    seller = create_test_user("produto-vendedor@teste.com", "vendedor")
    source = Image.new("RGB", (1400, 900), "orange")
    source_bytes = BytesIO()
    source.save(source_bytes, "PNG")

    with TestClient(app) as client:
        login_page = client.get("/login")
        client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": "produto-vendedor@teste.com", "senha": "senha-segura"},
        )
        seller_profile = client.get("/perfil")
        assert 'href="/produtos/novo"' in seller_profile.text

        form_page = client.get("/produtos/novo")
        assert form_page.status_code == 200
        assert "Aceita venda fiada" in form_page.text
        assert "Com entrega" in form_page.text
        checkbox_fields = re.findall(r'<input type="checkbox"[^>]*>', form_page.text)
        assert len(checkbox_fields) == 2
        assert all("checked" not in field for field in checkbox_fields)
        assert "Definir foco" in form_page.text
        assert "Capturar com a câmera" in form_page.text

        created = client.post(
            "/produtos/novo",
            data={
                "csrf": csrf_from(form_page),
                "nome": "Brigadeiro especial",
                "descricao": "Brigadeiro artesanal com chocolate.",
                "valor": "7,50",
                "focus_x": "0.70",
                "focus_y": "0.45",
            },
            files={"imagem": ("brigadeiro.png", source_bytes.getvalue(), "image/png")},
            follow_redirects=False,
        )
        assert created.status_code == 303
        assert created.headers["location"] == "/produtos/novo?criado=1"

        with SessionLocal() as database:
            product = database.scalar(select(Produto).where(Produto.vendedor_id == seller.id))
            assert product is not None
            assert product.valor_centavos == 750
            assert product.aceita_fiado is False
            assert product.com_entrega is False
            image_path = tmp_path / product.imagem
            with Image.open(image_path) as saved_image:
                assert saved_image.size == (600, 600)

        clean_page = client.get(created.headers["location"])
        assert "Produto criado com sucesso!" in clean_page.text
        assert "Brigadeiro especial" in clean_page.text
        assert "R$ 7,50" in clean_page.text
        assert 'value="Brigadeiro especial"' not in clean_page.text
        assert "Excluir produto" in clean_page.text

        updated_profile = client.get("/perfil")
        assert "1 produto cadastrado" in updated_profile.text
        assert 'href="/produtos/novo">Ver</a>' in updated_profile.text

        public_page = client.get(f"/vendedores/{seller.id}")
        assert "Brigadeiro especial" in public_page.text
        assert "Brigadeiro artesanal com chocolate." in public_page.text
        assert "R$ 7,50" in public_page.text
        assert "Doces cadastrados</dt><dd>1" in public_page.text
        assert "Excluir produto" not in public_page.text

        deleted = client.post(
            f"/produtos/{product.id}/excluir",
            data={"csrf": csrf_from(clean_page)},
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        assert deleted.headers["location"] == "/produtos/novo?excluido=1"
        assert not image_path.exists()

        with SessionLocal() as database:
            assert database.get(Produto, product.id) is None

        deletion_page = client.get(deleted.headers["location"])
        assert "Produto excluído com sucesso!" in deletion_page.text
        assert "Brigadeiro especial" not in deletion_page.text


def test_buyer_cannot_open_or_create_product() -> None:
    create_test_user("produto-comprador@teste.com", "comprador")
    source = Image.new("RGB", (200, 200), "orange")
    source_bytes = BytesIO()
    source.save(source_bytes, "PNG")

    with TestClient(app) as client:
        login_page = client.get("/login")
        client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": "produto-comprador@teste.com", "senha": "senha-segura"},
        )
        profile = client.get("/perfil")
        assert 'href="/produtos/novo"' not in profile.text
        page = client.get("/produtos/novo", follow_redirects=False)
        assert page.status_code == 303
        assert page.headers["location"] == "/perfil"


def test_customer_buys_product_from_storefront() -> None:
    seller = create_test_user("pedido-vendedor@teste.com", "vendedor")
    buyer = create_test_user("pedido-comprador@teste.com", "comprador")
    with SessionLocal() as database:
        product = Produto(
            vendedor_id=seller.id,
            nome="Bolo no pote",
            descricao="Chocolate com brigadeiro.",
            valor_centavos=1200,
            aceita_fiado=True,
            com_entrega=True,
            imagem="produto-teste.webp",
        )
        database.add(product)
        database.commit()
        database.refresh(product)

    with TestClient(app) as client:
        login_page = client.get("/login")
        client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": buyer.email, "senha": "senha-segura"},
        )
        storefront = client.get(f"/vendedores/{seller.id}")
        assert "Comprar" in storefront.text
        assert "Comprar e pagar depois" in storefront.text
        assert "Entregar aqui" in storefront.text
        assert 'name="quantidade"' in storefront.text

        purchase = client.post(
            f"/produtos/{product.id}/comprar",
            data={
                "csrf": csrf_from(storefront),
                "quantidade": "3",
                "pagar_depois": "true",
                "entregar_aqui": "true",
            },
            follow_redirects=False,
        )
        assert purchase.status_code == 303
        assert purchase.headers["location"] == f"/vendedores/{seller.id}?pedido=1"
        confirmation = client.get(purchase.headers["location"])
        assert "Compra registrada com sucesso!" in confirmation.text

        purchases = client.get("/minhas-compras")
        assert purchases.status_code == 200
        assert "Minhas compras" in purchases.text
        assert "Vendedor de Teste" in purchases.text
        assert "3 itens comprados" in purchases.text
        assert f'href="/minhas-compras/vendedores/{seller.id}"' in purchases.text

        seller_orders = client.get(f"/minhas-compras/vendedores/{seller.id}")
        assert seller_orders.status_code == 200
        assert "Bolo no pote" in seller_orders.text
        assert "3 unidades" in seller_orders.text
        assert "Pagamento pendente" in seller_orders.text
        assert ">Pagar<" in seller_orders.text
        denied_sales = client.get("/vendas", follow_redirects=False)
        assert denied_sales.status_code == 303
        assert denied_sales.headers["location"] == "/perfil"

    with SessionLocal() as database:
        order = database.scalar(select(Pedido).where(Pedido.cliente_id == buyer.id))
        assert order is not None
        assert order.vendedor_id == seller.id
        assert order.produto_id == product.id
        assert order.quantidade == 3
        assert order.valor_total_centavos == 3600
        assert order.pagar_depois is True
        assert order.entregar_aqui is True
        assert order.pago is False
        assert order.status == "recebido"

    with TestClient(app) as seller_client:
        login_page = seller_client.get("/login")
        seller_client.post(
            "/login",
            data={"csrf": csrf_from(login_page), "email": seller.email, "senha": "senha-segura"},
        )
        own_storefront = seller_client.get(f"/vendedores/{seller.id}")
        assert 'data-buy-product' not in own_storefront.text
        assert 'href="/vendas"' in own_storefront.text

        sales = seller_client.get("/vendas")
        assert sales.status_code == 200
        assert "Vendas" in sales.text
        assert "Cliente de Teste" in sales.text
        assert "3 doces comprados" in sales.text
        assert "0 doces pagos" in sales.text
        assert "Doce mais comprado" in sales.text
        assert "Bolo no pote" in sales.text
        assert f'href="/vendas/clientes/{buyer.id}"' in sales.text

        customer_history = seller_client.get(f"/vendas/clientes/{buyer.id}")
        assert customer_history.status_code == 200
        assert "Histórico de compras" in customer_history.text
        assert "Dia em que mais pede" in customer_history.text
        assert "Preferência de recebimento" in customer_history.text
        assert "Entrega" in customer_history.text
        assert "Pagar depois" in customer_history.text
        assert "3 unidades" in customer_history.text
        assert "Pagamento pendente" in customer_history.text
        forbidden = seller_client.post(
            f"/produtos/{product.id}/comprar",
            data={"csrf": csrf_from(own_storefront), "quantidade": "1"},
        )
        assert forbidden.status_code == 403


def test_immediate_purchase_redirects_to_pix_payment(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(payment_routes, "RECEIPT_DIR", tmp_path)
    monkeypatch.setattr(payment_routes, "extract_pix_receipt", lambda path: {
        "valor": Decimal("19.00"), "data": "2026-09-02", "hora": "10:30",
        "destinatario": "VENDEDOR REAL DA SILVA", "cpf_cnpj_destinatario": None,
        "pagador": "CLIENTE DE TESTE", "instituicao": "BANCO TESTE",
        "e2e_id": "E1234567820260902ABCDEF1234567890",
        "texto_ocr": "Comprovante Pix\nValor: R$ 19,00\nDestinatário: VENDEDOR REAL DA SILVA",
    })
    seller = create_test_user("pix-vendedor@teste.com", "vendedor")
    buyer = create_test_user("pix-comprador@teste.com", "comprador")
    with SessionLocal() as database:
        product = Produto(vendedor_id=seller.id, nome="Cupcake", descricao="Cupcake de baunilha com cobertura.", valor_centavos=950, aceita_fiado=True, com_entrega=False, imagem="cupcake.webp")
        database.add(product)
        database.commit()
        database.refresh(product)

    with TestClient(app) as seller_client:
        login_page = seller_client.get("/login")
        seller_client.post("/login", data={"csrf": csrf_from(login_page), "email": seller.email, "senha": "senha-segura"})
        edit_page = seller_client.get("/conta/editar")
        assert "Chave Pix para receber pagamentos" in edit_page.text
        saved = seller_client.post("/conta/pix", data={"csrf": csrf_from(edit_page), "chave_pix": "pix-vendedor@criar", "nome_recebedor_pix": "Vendedor Real da Silva"}, follow_redirects=False)
        assert saved.status_code == 303
        owner_profile = seller_client.get("/perfil")
        assert "pix-vendedor@criar" in owner_profile.text
        assert ">Editar</a>" in owner_profile.text
        with SessionLocal() as database:
            seller_profile = database.scalar(select(PerfilVendedor).where(PerfilVendedor.usuario_id == seller.id))
            assert seller_profile.nome_recebedor_pix == "Vendedor Real da Silva"

    with TestClient(app) as buyer_client:
        login_page = buyer_client.get("/login")
        buyer_client.post("/login", data={"csrf": csrf_from(login_page), "email": buyer.email, "senha": "senha-segura"})
        storefront = buyer_client.get(f"/vendedores/{seller.id}")
        purchase = buyer_client.post(
            f"/produtos/{product.id}/comprar",
            data={"csrf": csrf_from(storefront), "quantidade": "2"},
            follow_redirects=False,
        )
        assert purchase.status_code == 303
        assert re.fullmatch(r"/pagamentos/pedidos/\d+", purchase.headers["location"])

        payment = buyer_client.get(purchase.headers["location"])
        assert payment.status_code == 200
        assert "pix-vendedor@criar" in payment.text
        assert "Cupcake de baunilha com cobertura." in payment.text
        assert "2 unidades" in payment.text
        assert "R$ 19,00" in payment.text
        assert "confirmação automática será adicionada futuramente" in payment.text
        assert "Anexar comprovante" in payment.text

        receipt_source = Image.new("RGB", (900, 1400), "white")
        receipt_bytes = BytesIO()
        receipt_source.save(receipt_bytes, "PNG")
        uploaded = buyer_client.post(
            f"{purchase.headers['location']}/comprovante",
            data={"csrf": csrf_from(payment)},
            files={"comprovante": ("comprovante.png", receipt_bytes.getvalue(), "image/png")},
            follow_redirects=False,
        )
        assert uploaded.status_code == 303
        assert uploaded.headers["location"] == f"{purchase.headers['location']}?comprovante=1"
        receipt_page = buyer_client.get(uploaded.headers["location"])
        assert "Comprovante anexado" in receipt_page.text
        assert "não pode ser apagado ou substituído" in receipt_page.text
        assert "Anexar comprovante" not in receipt_page.text
        assert "Texto extraído do comprovante" in receipt_page.text
        assert "Valor: R$ 19,00" in receipt_page.text
        assert "Destinatário: VENDEDOR REAL DA SILVA" in receipt_page.text

        with SessionLocal() as database:
            receipt = database.scalar(select(ComprovantePagamento).where(ComprovantePagamento.cliente_id == buyer.id))
            assert receipt is not None
            assert receipt.texto_ocr.startswith("Comprovante Pix")
            assert receipt.ocr_valor == "19.00"
            assert receipt.ocr_processado_em is not None
            saved_receipt = tmp_path / receipt.arquivo
            assert saved_receipt.exists()
        assert buyer_client.get(f"/pagamentos/comprovantes/{receipt.id}/imagem").status_code == 200

        repeated = buyer_client.post(
            f"{purchase.headers['location']}/comprovante",
            data={"csrf": csrf_from(receipt_page)},
            files={"comprovante": ("outro.png", receipt_bytes.getvalue(), "image/png")},
            follow_redirects=False,
        )
        assert repeated.status_code == 303
        assert repeated.headers["location"].endswith("?erro_comprovante=ja_enviado")
        assert saved_receipt.exists()
        with SessionLocal() as database:
            assert len(database.scalars(select(ComprovantePagamento).where(ComprovantePagamento.pedido_id == receipt.pedido_id)).all()) == 1

        purchases = buyer_client.get(f"/minhas-compras/vendedores/{seller.id}")
        assert "Pagamento pendente" in purchases.text
        assert f'href="{purchase.headers["location"]}">Pagar</a>' in purchases.text

    with TestClient(app) as other_client:
        other = create_test_user("pix-outro@teste.com", "comprador")
        login_page = other_client.get("/login")
        other_client.post("/login", data={"csrf": csrf_from(login_page), "email": other.email, "senha": "senha-segura"})
        assert other_client.get(purchase.headers["location"]).status_code == 404
        assert other_client.get(f"/pagamentos/comprovantes/{receipt.id}/imagem").status_code == 404
