"""Acceso a datos. Todo el SQL del agente vive aquí."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.database import SesionLocal
from app.db.models import Alerta, Auditoria, Incidente, Notificacion, UsoLlm
from app.schemas.laboratorio import AmbitoPrueba

# El contador de alertas de un incidente se calcula; no se guarda en una columna.
_contador_alertas = (
    select(func.count(Alerta.id)).where(Alerta.incidente_id == Incidente.id).correlate(Incidente).scalar_subquery()
)


def _seleccion_incidentes():
    return select(*Incidente.__table__.columns, _contador_alertas.label("contador_alertas"))


def crear_alerta(sesion: Session, incidente_id: int, payload: dict, t_ryu: datetime, t_recibido: datetime) -> None:
    """Agrega una alerta a una sesión abierta (sin commit)."""
    sesion.add(Alerta(incidente_id=incidente_id, payload=payload, t_ryu=t_ryu, t_recibido=t_recibido))


def crear_incidente(
    host_origen: str,
    dpid_switch: str | None,
    alerta_nivel: str | None,
    t_ryu: datetime,
    t_recibido: datetime,
    payload: dict,
    ambito: AmbitoPrueba | None = None,
) -> int:
    """Crea el incidente y su primera alerta en una sola transacción. Devuelve el id del incidente."""
    with SesionLocal.begin() as sesion:
        incidente = Incidente(
            host_origen=host_origen,
            dpid_switch=dpid_switch,
            alerta_nivel=alerta_nivel,
            t_ryu=t_ryu,
            t_recibido=t_recibido,
            origen="real" if ambito is None else "prueba",
            **(ambito._asdict() if ambito else {}),
        )
        sesion.add(incidente)
        sesion.flush()
        crear_alerta(sesion, incidente.id, payload, t_ryu, t_recibido)
        return incidente.id


def agregar_alerta_a_incidente(incidente_id: int, payload: dict, t_ryu: datetime, t_recibido: datetime) -> int:
    """Agrupa una alerta en un incidente existente. Devuelve el contador de alertas actualizado."""
    with SesionLocal.begin() as sesion:
        crear_alerta(sesion, incidente_id, payload, t_ryu, t_recibido)
        sesion.flush()
        return sesion.scalar(select(func.count(Alerta.id)).where(Alerta.incidente_id == incidente_id))


def _mismo_ambito(ambito: AmbitoPrueba | None) -> tuple:
    """Real solo con real; prueba solo con la misma corrida, caso y repetición."""
    if ambito is None:
        return (Incidente.origen == "real", Incidente.corrida_id.is_(None))
    return (
        Incidente.origen == "prueba",
        Incidente.corrida_id == ambito.corrida_id,
        Incidente.caso_id == ambito.caso_id,
        Incidente.repeticion == ambito.repeticion,
    )


def buscar_incidente_abierto_reciente(host: str, segundos: int, ambito: AmbitoPrueba | None = None) -> int | None:
    """Incidente sin desbloquear del host cuya ÚLTIMA alerta llegó hace ≤ segundos (ventana deslizante),
    dentro del mismo ámbito (los reales nunca se mezclan con los de prueba ni las corridas entre sí)."""
    limite = datetime.now(timezone.utc) - timedelta(seconds=segundos)
    consulta = (
        select(Incidente.id)
        .join(Alerta, Alerta.incidente_id == Incidente.id)
        .where(Incidente.host_origen == host, Incidente.t_desbloqueado.is_(None), Alerta.t_recibido >= limite)
        .where(*_mismo_ambito(ambito))
        .order_by(Incidente.id.desc())
        .limit(1)
    )
    with SesionLocal() as sesion:
        return sesion.scalar(consulta)


def listar_incidentes(limite: int, offset: int, origen: str = "real") -> list[dict[str, Any]]:
    """Incidentes del más reciente al más antiguo, con su contador de alertas. origen: real | prueba | todos."""
    consulta = _seleccion_incidentes().order_by(Incidente.id.desc()).limit(limite).offset(offset)
    if origen != "todos":
        consulta = consulta.where(Incidente.origen == origen)
    with SesionLocal() as sesion:
        return [dict(fila) for fila in sesion.execute(consulta).mappings()]


def obtener_incidente(incidente_id: int) -> dict[str, Any] | None:
    consulta = _seleccion_incidentes().where(Incidente.id == incidente_id)
    with SesionLocal() as sesion:
        fila = sesion.execute(consulta).mappings().first()
    return dict(fila) if fila else None


def obtener_datos_para_explicar(incidente_id: int) -> tuple[dict, int] | None:
    """Payload de la primera alerta del incidente y su contador de alertas."""
    with SesionLocal() as sesion:
        payload = sesion.scalar(
            select(Alerta.payload).where(Alerta.incidente_id == incidente_id).order_by(Alerta.id).limit(1)
        )
        if payload is None:
            return None
        contador = sesion.scalar(select(func.count(Alerta.id)).where(Alerta.incidente_id == incidente_id))
    return payload, contador


def guardar_explicacion(
    incidente_id: int,
    explicacion: dict[str, Any],
    fuente: str,
    motivo_respaldo: str | None,
    t_explicado: datetime,
    tokens_entrada: int | None,
    tokens_salida: int | None,
    latencia_ms: int | None,
) -> None:
    """Guarda la salida C4 (claves = columnas del incidente). No toca nada del bloqueo."""
    with SesionLocal.begin() as sesion:
        sesion.execute(
            update(Incidente)
            .where(Incidente.id == incidente_id)
            .values(
                **explicacion,
                fuente_explicacion=fuente,
                motivo_respaldo=motivo_respaldo,
                t_explicado=t_explicado,
                tokens_entrada=tokens_entrada,
                tokens_salida=tokens_salida,
                latencia_llm_ms=latencia_ms,
            )
        )


def _uso_del_dia(sesion: Session, fecha: str) -> UsoLlm:
    uso = sesion.get(UsoLlm, fecha)
    if uso is None:
        uso = UsoLlm(fecha=fecha, peticiones=0, fallidas=0, tokens_entrada=0, tokens_salida=0)
        sesion.add(uso)
    return uso


def reservar_peticion_llm(fecha: str, cupo: int) -> bool:
    """Suma 1 a las peticiones del día si aún hay cupo. Devuelve si se pudo reservar."""
    with SesionLocal.begin() as sesion:
        uso = _uso_del_dia(sesion, fecha)
        if uso.peticiones >= cupo:
            return False
        uso.peticiones += 1
        return True


def registrar_uso_llm(fecha: str, peticiones_extra: int, tokens_entrada: int, tokens_salida: int, fallida: bool) -> None:
    with SesionLocal.begin() as sesion:
        uso = _uso_del_dia(sesion, fecha)
        uso.peticiones += peticiones_extra
        uso.tokens_entrada += tokens_entrada
        uso.tokens_salida += tokens_salida
        uso.fallidas += int(fallida)


def peticiones_llm_del_dia(fecha: str) -> int:
    with SesionLocal() as sesion:
        return sesion.scalar(select(UsoLlm.peticiones).where(UsoLlm.fecha == fecha)) or 0


def guardar_notificacion(
    incidente_id: int | None, tipo: str, payload: dict, resultado: dict[str, Any], ts: datetime
) -> int:
    """resultado: estado, intentos, codigo_http, error, latencia_ms (claves = columnas)."""
    with SesionLocal.begin() as sesion:
        notificacion = Notificacion(incidente_id=incidente_id, tipo=tipo, payload=payload, ts=ts, **resultado)
        sesion.add(notificacion)
        sesion.flush()
        return notificacion.id


def marcar_notificado(incidente_id: int, ts: datetime) -> None:
    """Solo el PRIMER envío exitoso marca t_notificado (un reenvío no altera la métrica)."""
    with SesionLocal.begin() as sesion:
        sesion.execute(
            update(Incidente)
            .where(Incidente.id == incidente_id, Incidente.t_notificado.is_(None))
            .values(t_notificado=ts)
        )


def ultima_notificacion(incidente_id: int) -> dict[str, Any] | None:
    consulta = (
        select(Notificacion.estado, Notificacion.intentos, Notificacion.ts, Notificacion.codigo_http, Notificacion.error)
        .where(Notificacion.incidente_id == incidente_id)
        .order_by(Notificacion.id.desc())
        .limit(1)
    )
    with SesionLocal() as sesion:
        fila = sesion.execute(consulta).mappings().first()
    return dict(fila) if fila else None


def ultimo_hash_auditoria() -> str | None:
    with SesionLocal() as sesion:
        return sesion.scalar(select(Auditoria.hash).order_by(Auditoria.id.desc()).limit(1))


def insertar_auditoria(ts: datetime, evento: str, datos: Any, hash_previo: str, hash_: str) -> None:
    """Única escritura sobre auditoria: solo INSERT, nunca UPDATE ni DELETE."""
    with SesionLocal.begin() as sesion:
        sesion.add(Auditoria(ts=ts, evento=evento, datos=datos, hash_previo=hash_previo, hash=hash_))


def listar_auditoria() -> list[Auditoria]:
    """Todas las filas de auditoría en orden de inserción."""
    with SesionLocal() as sesion:
        return list(sesion.scalars(select(Auditoria).order_by(Auditoria.id)))
