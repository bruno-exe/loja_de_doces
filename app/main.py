from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .database import Base, engine
from .routes.auth import router as auth_router
from .routes.profile import router as profile_router
from .routes.sweets import router as sweets_router
from .routes.products import router as products_router
from .routes.purchases import router as purchases_router
from .routes.sales import router as sales_router
from .routes.payments import router as payments_router
from .routes.cart import router as cart_router
from .routes.availability import router as availability_router
from .security import csrf_token
from .session import current_user


APP_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = APP_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    user_columns = {column["name"] for column in inspect(engine).get_columns("usuarios")}
    if "foto" not in user_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE usuarios ADD COLUMN foto VARCHAR(255)"))
    seller_profile_columns = {column["name"] for column in inspect(engine).get_columns("perfis_vendedores")}
    if "chave_pix" not in seller_profile_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE perfis_vendedores ADD COLUMN chave_pix VARCHAR(140)"))
    if "nome_recebedor_pix" not in seller_profile_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE perfis_vendedores ADD COLUMN nome_recebedor_pix VARCHAR(140)"))
    order_columns = {column["name"] for column in inspect(engine).get_columns("pedidos")}
    with engine.begin() as connection:
        if "produto_descricao" not in order_columns:
            connection.execute(text("ALTER TABLE pedidos ADD COLUMN produto_descricao TEXT"))
        if "produto_imagem" not in order_columns:
            connection.execute(text("ALTER TABLE pedidos ADD COLUMN produto_imagem VARCHAR(255)"))
        if "desconto_centavos" not in order_columns:
            connection.execute(text("ALTER TABLE pedidos ADD COLUMN desconto_centavos INTEGER NOT NULL DEFAULT 0"))
        if "confirmado" not in order_columns:
            connection.execute(text("ALTER TABLE pedidos ADD COLUMN confirmado BOOLEAN NOT NULL DEFAULT 1"))
    product_columns = {column["name"] for column in inspect(engine).get_columns("produtos")}
    with engine.begin() as connection:
        if "quantidade_desconto" not in product_columns:
            connection.execute(text("ALTER TABLE produtos ADD COLUMN quantidade_desconto INTEGER"))
        if "valor_desconto_centavos" not in product_columns:
            connection.execute(text("ALTER TABLE produtos ADD COLUMN valor_desconto_centavos INTEGER"))
    cart_columns = {column["name"] for column in inspect(engine).get_columns("itens_carrinho")}
    if "pedido_pendente_id" not in cart_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE itens_carrinho ADD COLUMN pedido_pendente_id INTEGER"))
    receipt_columns = {column["name"] for column in inspect(engine).get_columns("comprovantes_pagamentos")}
    receipt_migrations = {
        "ocr_valor": "VARCHAR(40)", "ocr_data": "VARCHAR(10)", "ocr_hora": "VARCHAR(5)",
        "ocr_destinatario": "VARCHAR(255)", "ocr_cpf_cnpj_destinatario": "VARCHAR(20)",
        "ocr_pagador": "VARCHAR(255)", "ocr_instituicao": "VARCHAR(255)", "ocr_e2e_id": "VARCHAR(80)",
        "texto_ocr": "TEXT", "ocr_erro": "TEXT", "ocr_processado_em": "DATETIME",
    }
    with engine.begin() as connection:
        for column_name, column_type in receipt_migrations.items():
            if column_name not in receipt_columns:
                connection.execute(text(f"ALTER TABLE comprovantes_pagamentos ADD COLUMN {column_name} {column_type}"))
    yield


app = FastAPI(
    title="Come Doce",
    description="Aplicacao local do Come Doce.",
    version="0.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="comedoce_session",
    same_site="lax",
    https_only=settings.cookie_secure,
    max_age=60 * 60 * 24 * 14,
)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
templates = Jinja2Templates(directory=APP_DIR / "templates")
app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(sweets_router)
app.include_router(products_router)
app.include_router(purchases_router)
app.include_router(sales_router)
app.include_router(payments_router)
app.include_router(cart_router)
app.include_router(availability_router)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    usuario = current_user(request)
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"usuario": usuario, "csrf_token": csrf_token(request) if usuario else None},
    )


@app.get("/status")
def status():
    return {"site": "Come Doce", "status": "funcionando"}
