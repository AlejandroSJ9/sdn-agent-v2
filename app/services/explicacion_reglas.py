"""Respaldo determinista: salida C4 solo con métricas y umbrales_superados, sin LLM."""

from app.schemas.alerta import AlertaEntrada, Metricas
from app.schemas.explicacion import ExplicacionC4
from app.services.prompt import UMBRALES_VALIDOS

# De la técnica más específica a la más general.
_PRIORIDAD_MITRE = (("M3", "T1021.002"), ("M5", "T1021"), ("M1", "T1046"), ("M2", "T1046"), ("M4", "T1046"))
MITRE_SIN_UMBRALES = "T1046"


def _evidencia(umbral: str, m: Metricas) -> str:
    return {
        "M1": f"M1={m.M1_nuevos_flujos_por_30s} flujos nuevos en 30 s",
        "M2": f"M2={m.M2_destinos_unicos_por_30s} destinos únicos en 30 s",
        "M3": "M3=tráfico SMB anómalo detectado" if m.M3_smb_anomalo else "M3=umbral SMB superado",
        "M4": f"M4={m.M4_packet_in_por_min} packet-in por minuto",
        "M5": f"M5={m.M5_count} flujos east-west",
    }[umbral]


def explicar_por_reglas(alerta: AlertaEntrada) -> ExplicacionC4:
    umbrales = [u for u in UMBRALES_VALIDOS if u in alerta.umbrales_superados]
    n = len(umbrales)
    confirmado = n >= 2
    lista = ", ".join(umbrales) or "ninguno"

    if confirmado:
        conclusion = "El patrón es coherente con movimiento lateral; Ryu-IA mantiene el host aislado."
        recomendacion = (
            "Revise el host antes de desbloquearlo: procesos activos, sesiones SMB/RDP/SSH abiertas "
            "y archivos modificados o cifrados recientemente."
        )
    else:
        conclusion = "Los datos no bastan para confirmar movimiento lateral; Ryu-IA mantiene el bloqueo hasta su revisión."
        recomendacion = (
            "Revise la actividad reciente del host y confirme con su responsable si es legítima "
            "antes de decidir el desbloqueo."
        )

    return ExplicacionC4(
        veredicto="confirmado" if confirmado else "indeterminado",
        severidad="critica" if n >= 3 else "alta" if n == 2 else "media",
        confianza=0.6 if confirmado else 0.4,
        tecnica_mitre=next((t for u, t in _PRIORIDAD_MITRE if u in umbrales), MITRE_SIN_UMBRALES),
        explicacion=(
            f"Explicación generada por reglas (sin LLM). El host {alerta.host_origen} superó {n} "
            f"umbral(es) de detección ({lista}). {conclusion}"
        ),
        evidencias=[_evidencia(u, alerta.metricas) for u in umbrales],
        recomendacion_operador=recomendacion,
    )
