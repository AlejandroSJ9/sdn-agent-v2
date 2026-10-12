"""Tablas del agente. Columnas según docs/contratos.md y CLAUDE.md §5–§7. Tiempos en UTC."""

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class FechaHoraUTC(TypeDecorator):
    """Guarda en UTC y SIEMPRE devuelve datetimes con zona UTC (SQLite no guarda la zona).

    Al guardar: una fecha sin zona se toma como UTC; una con zona se convierte a UTC.
    El formato almacenado es el mismo que el de DateTime, así que es compatible con bases previas.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, valor, dialecto):
        if valor is None or valor.tzinfo is None:
            return valor
        return valor.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, valor, dialecto):
        if valor is None:
            return None
        return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor.astimezone(timezone.utc)


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
    motivo_respaldo: Mapped[str | None] = mapped_column(String(30))  # null si la fuente es "llm"
    tokens_entrada: Mapped[int | None] = mapped_column(Integer)
    tokens_salida: Mapped[int | None] = mapped_column(Integer)
    latencia_llm_ms: Mapped[int | None] = mapped_column(Integer)
    # Marcas de tiempo (CLAUDE.md §7)
    t_ryu: Mapped[datetime | None] = mapped_column(FechaHoraUTC)
    t_recibido: Mapped[datetime] = mapped_column(FechaHoraUTC, default=ahora_utc)
    t_explicado: Mapped[datetime | None] = mapped_column(FechaHoraUTC)
    t_notificado: Mapped[datetime | None] = mapped_column(FechaHoraUTC)
    t_desbloqueado: Mapped[datetime | None] = mapped_column(FechaHoraUTC)
    # Laboratorio (Fase 6): los incidentes reales quedan con origen="real" y el resto en NULL
    origen: Mapped[str] = mapped_column(String(10), default="real", server_default="real", index=True)
    corrida_id: Mapped[int | None] = mapped_column(ForeignKey("corridas.id"), index=True)
    caso_id: Mapped[str | None] = mapped_column(String(10))
    repeticion: Mapped[int | None] = mapped_column(Integer)
    # Fase 6.7: contexto dinámico (docs/contratos.md C6)
    clasificacion_operador: Mapped[str | None] = mapped_column(String(20))  # falso_positivo | amenaza_contenida | otro
    motivo_operador: Mapped[str | None] = mapped_column(String(200))
    variante: Mapped[str | None] = mapped_column(String(15))  # sin_contexto | con_contexto (null = sin explicar)
    version_prompt: Mapped[str | None] = mapped_column(String(30))
    contexto_usado: Mapped[dict | None] = mapped_column(JSON)  # exactamente lo enviado; null sin contexto
    sembrado: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")  # historial del laboratorio
    # Fase 6.8: decisión acotada (C4 extensión y C7)
    accion_propuesta: Mapped[str | None] = mapped_column(String(20))  # mantener | pasar_a_cuarentena | escalar_a_drop
    justificacion_accion: Mapped[str | None] = mapped_column(String(300))
    decision_politica: Mapped[str | None] = mapped_column(String(12))  # ejecutar | recomendar | descartar
    motivos_politica: Mapped[list | None] = mapped_column(JSON)
    accion_final: Mapped[str | None] = mapped_column(String(30))  # ninguna | <acción> | recomendada | fallida (+ _simulada)
    # Fase 6.8a+b: dictamen determinista e informe auditable. "veredicto" es el veredicto FINAL.
    informe: Mapped[dict | None] = mapped_column(JSON)
    veredicto_llm: Mapped[str | None] = mapped_column(String(20))  # del LLM (o de las reglas si hubo respaldo)
    dictamen_historial: Mapped[str | None] = mapped_column(String(20))  # agravante | atenuante | null


class Alerta(Base):
    __tablename__ = "alertas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incidente_id: Mapped[int] = mapped_column(ForeignKey("incidentes.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)  # cuerpo crudo de C1
    t_ryu: Mapped[datetime | None] = mapped_column(FechaHoraUTC)
    t_recibido: Mapped[datetime] = mapped_column(FechaHoraUTC, default=ahora_utc)


class Auditoria(Base):
    """Solo inserción: nunca UPDATE ni DELETE."""

    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(FechaHoraUTC, default=ahora_utc)
    evento: Mapped[str] = mapped_column(String(50))
    datos: Mapped[dict | None] = mapped_column(JSON)
    hash_previo: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class UsoLlm(Base):
    __tablename__ = "uso_llm"

    fecha: Mapped[str] = mapped_column(String(10), primary_key=True)  # día UTC YYYY-MM-DD
    peticiones: Mapped[int] = mapped_column(Integer, default=0)
    fallidas: Mapped[int] = mapped_column(Integer, default=0)
    tokens_entrada: Mapped[int] = mapped_column(Integer, default=0)
    tokens_salida: Mapped[int] = mapped_column(Integer, default=0)


class Notificacion(Base):
    __tablename__ = "notificaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incidente_id: Mapped[int | None] = mapped_column(ForeignKey("incidentes.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(20))  # incidente | desbloqueo | prueba
    payload: Mapped[dict] = mapped_column(JSON)  # cuerpo de C3
    estado: Mapped[str] = mapped_column(String(20))
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    codigo_http: Mapped[int | None] = mapped_column(Integer)  # código HTTP final (null si no hubo respuesta)
    error: Mapped[str | None] = mapped_column(String(200))  # texto corto; nunca incluye la URL
    latencia_ms: Mapped[int | None] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(FechaHoraUTC, default=ahora_utc)


class Configuracion(Base):
    __tablename__ = "configuracion"

    clave: Mapped[str] = mapped_column(String(50), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)


class Corrida(Base):
    """Una ejecución del laboratorio sobre los escenarios (tests/escenarios/escenarios.json)."""

    __tablename__ = "corridas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    t_inicio: Mapped[datetime] = mapped_column(FechaHoraUTC, default=ahora_utc)
    t_fin: Mapped[datetime | None] = mapped_column(FechaHoraUTC)
    estado: Mapped[str] = mapped_column(String(15))  # en_curso | terminada | fallida
    casos: Mapped[list] = mapped_column(JSON)
    conjunto: Mapped[str | None] = mapped_column(String(12))  # desarrollo | validacion | todos | None
    usar_llm: Mapped[bool] = mapped_column(Boolean)
    enviar_correos: Mapped[bool] = mapped_column(Boolean)
    repeticiones: Mapped[int] = mapped_column(Integer)
    costo_estimado: Mapped[int] = mapped_column(Integer)
    variante: Mapped[str] = mapped_column(String(15), default="sin_contexto", server_default="sin_contexto")
    modo_decision: Mapped[str] = mapped_column(String(20), default="autonomo_acotado", server_default="autonomo_acotado")
    umbral_confianza: Mapped[float] = mapped_column(Float, default=0.85, server_default="0.85")
    version_prompt: Mapped[str] = mapped_column(String(30), default="v2.0-sin-contexto", server_default="v2.0-sin-contexto")
    resumen: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(200))


class ResultadoCaso(Base):
    __tablename__ = "resultados_caso"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    corrida_id: Mapped[int] = mapped_column(ForeignKey("corridas.id"), index=True)
    caso_id: Mapped[str] = mapped_column(String(10))
    repeticion: Mapped[int] = mapped_column(Integer)
    incidente_id: Mapped[int | None] = mapped_column(ForeignKey("incidentes.id"))
    decidible: Mapped[bool] = mapped_column(Boolean)
    veredicto_esperado: Mapped[str] = mapped_column(String(20))
    veredicto_obtenido: Mapped[str | None] = mapped_column(String(20))
    acierto: Mapped[bool | None] = mapped_column(Boolean)  # el obtenido está en "aceptables"
    fuente: Mapped[str | None] = mapped_column(String(10))
    motivo_respaldo: Mapped[str | None] = mapped_column(String(30))
    latencia_explicacion_ms: Mapped[int | None] = mapped_column(Integer)  # t_explicado - t_recibido
    detalle: Mapped[dict | None] = mapped_column(JSON)  # DU-01: comprobación de agrupación
    accion_propuesta: Mapped[str | None] = mapped_column(String(20))
    decision_politica: Mapped[str | None] = mapped_column(String(12))
    accion_final: Mapped[str | None] = mapped_column(String(30))  # siempre simulada en el laboratorio
    veredicto_llm: Mapped[str | None] = mapped_column(String(20))
    dictamen_historial: Mapped[str | None] = mapped_column(String(20))
    verificacion: Mapped[str | None] = mapped_column(String(20))  # verificado | posible_alucinacion


class Recomendacion(Base):
    """Acción propuesta que la política no ejecutó sola: la resuelve el operador (o expira)."""

    __tablename__ = "recomendaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incidente_id: Mapped[int | None] = mapped_column(ForeignKey("incidentes.id"), index=True)
    host: Mapped[str] = mapped_column(String(15), index=True)  # una IPv4, nunca una subred
    accion: Mapped[str] = mapped_column(String(20))  # pasar_a_cuarentena | escalar_a_drop
    justificacion: Mapped[str | None] = mapped_column(String(300))
    confianza: Mapped[float | None] = mapped_column(Float)
    motivos_politica: Mapped[list] = mapped_column(JSON, default=list)
    estado: Mapped[str] = mapped_column(String(12), default="pendiente", index=True)
    # pendiente | aprobada | rechazada | ejecutada | expirada | fallida
    t_creada: Mapped[datetime] = mapped_column(FechaHoraUTC, default=ahora_utc)
    t_expira: Mapped[datetime] = mapped_column(FechaHoraUTC)
    t_resuelta: Mapped[datetime | None] = mapped_column(FechaHoraUTC)
    resuelta_por: Mapped[str | None] = mapped_column(String(12))  # operador | sistema
    motivo_resolucion: Mapped[str | None] = mapped_column(String(200))


class EjecucionPytest(Base):
    __tablename__ = "ejecuciones_pytest"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    carpeta: Mapped[str] = mapped_column(String(12))
    t_inicio: Mapped[datetime] = mapped_column(FechaHoraUTC, default=ahora_utc)
    t_fin: Mapped[datetime | None] = mapped_column(FechaHoraUTC)
    estado: Mapped[str] = mapped_column(String(15))  # en_curso | terminada | timeout | error
    aprobadas: Mapped[int | None] = mapped_column(Integer)
    fallidas: Mapped[int | None] = mapped_column(Integer)
    duracion_ms: Mapped[int | None] = mapped_column(Integer)
    salida: Mapped[str] = mapped_column(Text, default="")  # recortada a los últimos 20 000 caracteres
