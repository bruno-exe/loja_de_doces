from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = (
        CheckConstraint("tipo_conta IN ('comprador', 'vendedor')", name="ck_usuarios_tipo_conta"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(255))
    tipo_conta: Mapped[str] = mapped_column(String(20), index=True)
    foto: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    perfil_comprador: Mapped["PerfilComprador | None"] = relationship(
        back_populates="usuario", uselist=False, cascade="all, delete-orphan"
    )
    perfil_vendedor: Mapped["PerfilVendedor | None"] = relationship(
        back_populates="usuario", uselist=False, cascade="all, delete-orphan"
    )


class PerfilComprador(Base):
    __tablename__ = "perfis_compradores"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), unique=True, index=True)
    usuario: Mapped[Usuario] = relationship(back_populates="perfil_comprador")


class PerfilVendedor(Base):
    __tablename__ = "perfis_vendedores"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), unique=True, index=True)
    frase_apresentacao: Mapped[str | None] = mapped_column(String(240), nullable=True)
    foto: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chave_pix: Mapped[str | None] = mapped_column(String(140), nullable=True)
    nome_recebedor_pix: Mapped[str | None] = mapped_column(String(140), nullable=True)
    usuario: Mapped[Usuario] = relationship(back_populates="perfil_vendedor")


class Produto(Base):
    __tablename__ = "produtos"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendedor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    nome: Mapped[str] = mapped_column(String(120))
    descricao: Mapped[str] = mapped_column(Text)
    valor_centavos: Mapped[int] = mapped_column(Integer)
    quantidade_desconto: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valor_desconto_centavos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aceita_fiado: Mapped[bool] = mapped_column(Boolean, default=False)
    com_entrega: Mapped[bool] = mapped_column(Boolean, default=False)
    imagem: Mapped[str] = mapped_column(String(255))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    variacoes: Mapped[list["VariacaoProduto"]] = relationship(
        back_populates="produto", cascade="all, delete-orphan", order_by="VariacaoProduto.id"
    )


class VariacaoProduto(Base):
    __tablename__ = "variacoes_produtos"

    id: Mapped[int] = mapped_column(primary_key=True)
    produto_id: Mapped[int] = mapped_column(ForeignKey("produtos.id"), index=True)
    nome: Mapped[str] = mapped_column(String(120))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    produto: Mapped[Produto] = relationship(back_populates="variacoes")


class Pedido(Base):
    __tablename__ = "pedidos"
    __table_args__ = (
        CheckConstraint("quantidade BETWEEN 1 AND 99", name="ck_pedidos_quantidade"),
        CheckConstraint("status IN ('recebido', 'aceito', 'cancelado', 'concluido')", name="ck_pedidos_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    vendedor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    produto_id: Mapped[int] = mapped_column(ForeignKey("produtos.id"), index=True)
    produto_nome: Mapped[str] = mapped_column(String(120))
    produto_descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    produto_imagem: Mapped[str | None] = mapped_column(String(255), nullable=True)
    valor_unitario_centavos: Mapped[int] = mapped_column(Integer)
    quantidade: Mapped[int] = mapped_column(Integer)
    valor_total_centavos: Mapped[int] = mapped_column(Integer)
    desconto_centavos: Mapped[int] = mapped_column(Integer, default=0)
    pagar_depois: Mapped[bool] = mapped_column(Boolean, default=False)
    entregar_aqui: Mapped[bool] = mapped_column(Boolean, default=False)
    pago: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="recebido")
    confirmado: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    itens: Mapped[list["ItemPedido"]] = relationship(
        back_populates="pedido", cascade="all, delete-orphan", order_by="ItemPedido.id"
    )


class ItemPedido(Base):
    __tablename__ = "itens_pedidos"
    __table_args__ = (CheckConstraint("quantidade BETWEEN 1 AND 99", name="ck_itens_pedidos_quantidade"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedidos.id"), index=True)
    variacao_id: Mapped[int | None] = mapped_column(ForeignKey("variacoes_produtos.id"), nullable=True, index=True)
    variacao_nome: Mapped[str] = mapped_column(String(120))
    quantidade: Mapped[int] = mapped_column(Integer)
    pedido: Mapped[Pedido] = relationship(back_populates="itens")


class ItemCarrinho(Base):
    __tablename__ = "itens_carrinho"
    __table_args__ = (CheckConstraint("quantidade BETWEEN 1 AND 99", name="ck_itens_carrinho_quantidade"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    vendedor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    produto_id: Mapped[int] = mapped_column(ForeignKey("produtos.id"), index=True)
    variacao_id: Mapped[int | None] = mapped_column(ForeignKey("variacoes_produtos.id"), nullable=True, index=True)
    pedido_pendente_id: Mapped[int | None] = mapped_column(ForeignKey("pedidos.id"), nullable=True, index=True)
    quantidade: Mapped[int] = mapped_column(Integer)
    pagar_depois: Mapped[bool] = mapped_column(Boolean, default=False)
    entregar_aqui: Mapped[bool] = mapped_column(Boolean, default=False)
    adicionado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class VisitaPerfilVendedor(Base):
    __tablename__ = "visitas_perfis_vendedores"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendedor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    visitante_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    visitado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class ComprovantePagamento(Base):
    __tablename__ = "comprovantes_pagamentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedidos.id"), unique=True, index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    arquivo: Mapped[str] = mapped_column(String(255))
    enviado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    ocr_valor: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ocr_data: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ocr_hora: Mapped[str | None] = mapped_column(String(5), nullable=True)
    ocr_destinatario: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ocr_cpf_cnpj_destinatario: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ocr_pagador: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ocr_instituicao: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ocr_e2e_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    texto_ocr: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_processado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
