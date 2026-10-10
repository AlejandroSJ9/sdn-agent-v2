"""Motor, sesiones y creación de tablas de la base de datos."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


SesionLocal = sessionmaker(autoflush=False, expire_on_commit=False)


def iniciar_bd(url: str) -> Engine:
    """Crea el motor, enlaza las sesiones y crea las tablas si no existen."""
    from app.db import models  # noqa: F401  (registra las tablas en Base.metadata)

    argumentos = {"check_same_thread": False} if url.startswith("sqlite") else {}
    motor = create_engine(url, connect_args=argumentos)
    Base.metadata.create_all(motor)
    SesionLocal.configure(bind=motor)
    return motor
