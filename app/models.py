from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String
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
    usuario: Mapped[Usuario] = relationship(back_populates="perfil_vendedor")
