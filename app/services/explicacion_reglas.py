"""Respaldo determinista: salida v3 (SalidaLLMv3) solo con métricas y umbrales_superados, sin LLM.

Con contexto (C6), el veredicto lo fija el dictamen determinista de historial (services/dictamen_historial.py,
sin duplicar su lógica):
  - agravante (confirmado reciente o amenaza contenida en el host, o 2+ hosts confirmados) → confirmado;
  - atenuante (2+ 'falso_positivo' del operador con los MISMOS umbrales_superados) → indeterminado;
  - sin dictamen → igual que sin contexto.

Acción propuesta (Fase 6.8): sin contexto, siempre "mantener". Con contexto, "pasar_a_cuarentena" solo con
dictamen atenuante; "mantener" en otro caso.

Confianza: FIJA en 0.6 para todo resultado por reglas. Se unificó así para el motor de políticas (C7):
con el UMBRAL_CONFIANZA por defecto (0.85) las reglas NUNCA ejecutan una acción solas, solo recomiendan.
La comparación justa LLM vs. reglas se hará en la Fase 6.9 con umbral 0.6.
"""

from app.schemas.alerta import AlertaEntrada, Metricas
from app.schemas.explicacion import SalidaLLMv3
from app.schemas.informe import NO_DISPONIBLE
from app.services.dictamen_historial import dictaminar
from app.services.prompt import UMBRALES_VALIDOS

# De la técnica más específica a la más general (todas en la lista cerrada de v3).
_PRIORIDAD_MITRE = (("M3", "T1021.002"), ("M5", "T1021"), ("M1", "T1046"), ("M2", "T1046"), ("M4", "T1046"))
MITRE_SIN_UMBRALES = "T1046"
CONFIANZA_REGLAS = 0.6
JUSTIFICACION_MANTENER = "Reglas: se mantiene el bloqueo total; las reglas solo proponen cuarentena con falsos positivos verificados."
JUSTIFICACION_CUARENTENA = (
    "Reglas: 2 o más falsos positivos verificados por el operador con los mismos umbrales; la cuarentena evita "
    "un daño operativo innecesario y mantiene bloqueados los puertos de movimiento lateral."
)
_PESO = {"M3": "alto", "M5": "alto", "M1": "medio", "M2": "medio", "M4": "bajo"}
LIMITACIONES_TELEMETRIA = [f"Destinos, puertos, protocolo, duración de sesiones y línea base: {NO_DISPONIBLE}."]


def _porque(umbral: str, m: Metricas) -> str:
    return {
        "M1": f"M1={m.M1_nuevos_flujos_por_30s} flujos nuevos en 30 s",
        "M2": f"M2={m.M2_destinos_unicos_por_30s} destinos únicos en 30 s",
        "M3": "M3: tráfico SMB anómalo detectado" if m.M3_smb_anomalo else "M3: umbral SMB superado",
        "M4": f"M4={m.M4_packet_in_por_min} packet-in por minuto",
        "M5": f"M5_count={m.M5_count} flujos east-west",
    }[umbral]


def explicar_por_reglas(alerta: AlertaEntrada, contexto: dict | None = None) -> SalidaLLMv3:
    umbrales = [u for u in UMBRALES_VALIDOS if u in alerta.umbrales_superados]
    n = len(umbrales)
    dictamen = dictaminar(contexto, umbrales)
    veredicto = dictamen["veredicto"] or ("confirmado" if n >= 2 else "indeterminado")
    tecnica = next((t for u, t in _PRIORIDAD_MITRE if u in umbrales), MITRE_SIN_UMBRALES)

    factores = [{"factor": f"Umbral {u} superado", "peso": _PESO[u], "porque": _porque(u, alerta.metricas)} for u in umbrales]
    relleno = [  # v3 exige 2 a 4 factores
        {"factor": "Pocas métricas superadas", "peso": "bajo", "porque": f"Solo {n} métrica(s) superaron su umbral."},
        {"factor": "Telemetría limitada", "peso": "bajo", "porque": "Sin destinos, puertos ni protocolos para afinar el análisis."},
    ]
    factores += relleno[: max(0, 2 - len(factores))]
    if dictamen["tipo"]:
        factores.insert(0, {"factor": f"Dictamen {dictamen['tipo']} del historial", "peso": "alto", "porque": dictamen["motivo"]})

    if contexto is None:
        contexto_aplicado = "sin contexto disponible"
    elif dictamen["tipo"]:
        contexto_aplicado = f"Reglas: dictamen {dictamen['tipo']} ({dictamen['motivo']})."
    else:
        contexto_aplicado = "Reglas: el historial y la actividad de la red no cambian el veredicto."

    if veredicto == "confirmado":
        descarte = "Reglas: con 2 o más métricas superadas, el patrón es coherente con movimiento lateral; no se descarta un ataque."
        recomendacion = ("Revise el host antes de desbloquearlo: procesos activos, sesiones SMB/RDP/SSH abiertas "
                         "y archivos modificados o cifrados recientemente.")
    else:
        descarte = "Reglas: los datos no bastan para confirmar ni descartar actividad legítima."
        recomendacion = ("Revise la actividad reciente del host y confirme con su responsable si es legítima "
                         "antes de decidir el desbloqueo.")

    cuarentena = dictamen["tipo"] == "atenuante"
    return SalidaLLMv3(
        veredicto=veredicto,
        severidad="critica" if n >= 3 else "alta" if n == 2 else "media",
        confianza=CONFIANZA_REGLAS,
        tecnica_id=tecnica,
        hipotesis_fase=f"Reglas (sin LLM): {n} umbral(es) superado(s) ({', '.join(umbrales) or 'ninguno'}).",
        factores_ponderados=factores[:4],
        contexto_aplicado=contexto_aplicado,
        descarte_benigno=descarte,
        limitaciones=list(LIMITACIONES_TELEMETRIA),
        accion_propuesta="pasar_a_cuarentena" if cuarentena else "mantener",
        justificacion_accion=JUSTIFICACION_CUARENTENA if cuarentena else JUSTIFICACION_MANTENER,
        recomendacion_operador=recomendacion,
    )
