"""Tablas del agente. Columnas según docs/contratos.md y CLAUDE.md §5–§7. Tiempos en UTC."""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


class Incidente(Base):
    __tablename__ = "incidentes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # C1
    host_origen: Mapped[str] = mapped_column(String(15), index=True)
    dpid_switch: Mapped[str | None] = mapped_column(String(32))
    alerta_nivel: Mapped[str | None] = mapped_column(String(20))
    # C4 (informativo, no cambia el bloqueo)
    veredicto: Mapped[str | None] = mapped_column(String(20))
    severidad: Mapped[str | None] = mapped_column(String(10))
    confianza: Mapped[float | None] = mapped_column(Float)
    tecnica_mitre: Mapped[str | None] = mapped_column(String(20))
    explicacion: Mapped[str | None] = mapped_column(Text)
    evidencias: Mapped[list | None] = mapped_column(JSON)
    recomendacion_operador: Mapped[str | None] = mapped_column(Text)
    fuente_explicacion: Mapped[str | None] = mapped_column(String(10))  # "llm" | "reglas"
    # Marcas de tiempo (CLAUDE.md §7)
    t_ryu: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    t_recibido: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora_utc)
    t_explicado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    t_notificado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    t_desbloqueado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Alerta(Base):
    __tablename__ = "alertas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incidente_id: Mapped[int] = mapped_column(ForeignKey("incidentes.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)  # cuerpo crudo de C1
    t_ryu: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    t_recibido: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora_utc)


class Auditoria(Base):
    """Solo inserción: nunca UPDATE ni DELETE."""

    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora_utc)
    evento: Mapped[str] = mapped_column(String(50))
    datos: Mapped[dict | None] = mapped_column(JSON)
    hash_previo: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class UsoLlm(Base):
    __tablename__ = "uso_llm"

    fecha: Mapped[str] = mapped_column(String(10), primary_key=True)  # día UTC YYYY-MM-DD
    peticiones: Mapped[int] = mapped_column(Integer, default=0)


class Notificacion(Base):
    __tablename__ = "notificaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incidente_id: Mapped[int | None] = mapped_column(ForeignKey("incidentes.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(20))  # incidente | desbloqueo | prueba
    payload: Mapped[dict] = mapped_column(JSON)  # cuerpo de C3
    estado: Mapped[str] = mapped_column(String(20))
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora_utc)


class Configuracion(Base):
    __tablename__ = "configuracion"

    clave: Mapped[str] = mapped_column(String(50), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)
