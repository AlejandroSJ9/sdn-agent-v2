"""Motor, sesiones y creación de tablas de la base de datos."""

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


SesionLocal = sessionmaker(autoflush=False, expire_on_commit=False)

# La auditoría es de solo inserción también a nivel de base: cualquier UPDATE o DELETE se aborta.
TRIGGERS_AUDITORIA = (
    """CREATE TRIGGER IF NOT EXISTS auditoria_sin_update BEFORE UPDATE ON auditoria
       BEGIN SELECT RAISE(ABORT, 'auditoria es de solo insercion'); END""",
    """CREATE TRIGGER IF NOT EXISTS auditoria_sin_delete BEFORE DELETE ON auditoria
       BEGIN SELECT RAISE(ABORT, 'auditoria es de solo insercion'); END""",
)


def _pragmas_sqlite(conexion, _registro) -> None:
    """Claves foráneas activas y WAL (lectores y escritores no se bloquean entre sí)."""
    cursor = conexion.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def iniciar_bd(url: str) -> Engine:
    """Crea el motor, enlaza las sesiones y crea las tablas (y los triggers) si no existen."""
    from app.db import models  # noqa: F401  (registra las tablas en Base.metadata)

    es_sqlite = url.startswith("sqlite")
    motor = create_engine(url, connect_args={"check_same_thread": False} if es_sqlite else {})
    if es_sqlite:
        event.listen(motor, "connect", _pragmas_sqlite)
    Base.metadata.create_all(motor)
    if es_sqlite:
        with motor.begin() as conexion:
            for trigger in TRIGGERS_AUDITORIA:
                conexion.execute(text(trigger))
    SesionLocal.configure(bind=motor)
    return motor
