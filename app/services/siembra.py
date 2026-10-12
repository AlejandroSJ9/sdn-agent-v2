"""Siembra del historial de un caso del laboratorio (variante con contexto).

Antes de enviar la alerta del caso se crean, dentro de su propio ámbito (corrida + caso + repetición),
los "incidentes_previos" de la v1 tomados LITERALMENTE: fecha (minutos_antes), veredicto, confianza y
umbrales. Un veredicto 'falso_positivo' previo de la v1 se siembra también como clasificación del
operador. No se siembran hosts_riesgo ni nivel_alerta (C6 no los contempla).

Los sembrados quedan con sembrado=true: no se explican, no notifican, no se agrupan con alertas nuevas,
no cuentan en métricas y no aparecen en la tabla de incidentes ni en las exportaciones.
"""

from datetime import datetime, timedelta, timezone

from app.db import repository_contexto
from app.schemas.laboratorio import AmbitoPrueba


def sembrar(ambito: AmbitoPrueba, caso: dict) -> list[int]:
    """Crea el historial del caso y devuelve los ids sembrados (vacío si la v1 no trae incidentes previos)."""
    ahora = datetime.now(timezone.utc)
    sembrados = []
    for previo in caso.get("contexto_v1", {}).get("incidentes_previos", []):
        veredicto = previo["veredicto"]
        confianza = previo.get("confianza")
        sembrados.append(repository_contexto.crear_sembrado(
            ambito,
            host=previo["host"],
            t_recibido=ahora - timedelta(minutes=previo["minutos_antes"]),
            veredicto=veredicto,
            confianza=None if confianza is None else confianza / 100,
            clasificacion="falso_positivo" if veredicto == "falso_positivo" else None,
            umbrales=list(previo["metricas"]),
            fuente=f"escenarios_v1:{caso['id']}",
        ))
    return sembrados
