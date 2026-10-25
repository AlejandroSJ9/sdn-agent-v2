"""Explicación en lenguaje simple (Fase D3): 4 bloques para un administrador no experto y un lector no técnico.

  1. Qué pasó · 2. Por qué creemos que es esto · 4. Qué podría hacer que estemos equivocados → del LLM (v4) si los
     trae y pasan la revisión; si no, de las plantillas de abajo (fuente "reglas"), sin descartar el diagnóstico.
  3. Qué recomendamos y por qué → SIEMPRE lo arma el código desde la escalera y el dictamen (C7).
Revisión de cada bloque del LLM (verificador.verificar_bloques):
  - cifra que no está en la alerta ni en los umbrales → se reemplaza Y penaliza la confianza (como hoy);
  - jerga (M1…M5, MITRE, SMB, umbral…) → solo se reemplaza;
  - "Qué pasó" se reemplaza si el dictamen cambió el veredicto (nunca contradice el veredicto final).
Las plantillas no llevan cifras del contexto: el historial se cuenta con palabras. Funciones puras (sin BD ni red).
"""

from typing import Any

from app.schemas.alerta import AlertaEntrada
from app.services import verificador
from app.services.escalera import MOTIVO_CONTRADICCION
from app.services.informe import puntajes

BLOQUES_LLM = ("que_paso", "por_que", "podria_equivocarnos")
VEREDICTO = {"confirmado": "parece un ataque", "falso_positivo": "parece una falsa alarma",
             "indeterminado": "no está claro si es un ataque"}
ESCALON = {"limitar_tasa": "devolverle la red a velocidad reducida", "cuarentena_puertos": "pasarlo a cuarentena",
           "cuarentena_vlan": "moverlo a una red de cuarentena separada"}

# ---- Bloque 3: qué recomendamos y por qué (plantillas fijas) ----
AISLADO = "Recomendamos mantener el equipo aislado por completo."
ACCION_INDETERMINADO = (f"{AISLADO} Los datos no alcanzan para saber si es un ataque o una falsa alarma, así que "
                        "decide una persona.")
ACCION_BAJA = (AISLADO + " {veredicto}, pero nuestra confianza es baja, así que no es seguro devolverle la red; "
               "decide una persona.")
ACCION = {
    "limitar_tasa": ("Recomendamos devolverle la red al equipo, pero a velocidad reducida. Parece una falsa alarma y "
                     "nuestra confianza es {nivel}: así el equipo puede seguir trabajando y, si fuera un ataque, no "
                     "podría propagarse rápido."),
    "cuarentena_puertos": ("Recomendamos pasar el equipo a cuarentena: puede seguir usando lo básico de la red, pero se "
                           "le cierran los caminos que se usan para saltar a otros equipos. Parece un ataque y nuestra "
                           "confianza es {nivel}."),
    "cuarentena_vlan": ("Recomendamos mover el equipo a una red de cuarentena separada, donde no puede llegar a los "
                        "demás equipos. Parece un ataque y nuestra confianza es {nivel}."),
}
AGRAVANTE_CAMBIO = (AISLADO + " La confianza permitía {escalon}, pero el historial muestra señales graves: {motivos}. "
                    "Por eso no se relaja el aislamiento.")
AGRAVANTE_EXTRA = " Además, el historial muestra señales graves: {motivos}."
CONTRADICCION = ("Recomendamos mantener el equipo aislado por completo y que una persona lo revise. El análisis dice "
                 "que parece un ataque, pero un administrador ya marcó este mismo comportamiento como falsa alarma "
                 "varias veces. Como las dos cosas se contradicen, decide una persona.")
ATENUANTE_EXTRA = " El historial ayuda: un administrador ya marcó este mismo comportamiento como falsa alarma varias veces."
DEFENSA = f"{AISLADO[:-1]}: el análisis es contradictorio, así que decide una persona."
CIERRE_RELAJAR = " Nada se aplica solo: la acción se ejecuta únicamente si un administrador la aprueba en el panel."
CIERRE_MANTENER = " El equipo sigue aislado hasta que un administrador decida."
# Motivos del dictamen (textos fijos de dictamen_historial.py) → palabras, sin cifras
MOTIVOS_AGRAVANTE = (("últimos 60 min", "este mismo equipo tuvo un ataque confirmado en la última hora"),
                     ("amenaza", "un administrador ya contuvo un ataque en este equipo"),
                     ("hosts confirmados", "hay otros equipos de la red con ataques confirmados ahora mismo"))

# ---- Bloques 1, 2 y 4 por plantilla (respaldo por reglas, LLM sin el bloque o bloque reemplazado) ----
QUE_PASO = {
    "confirmado": ("Este equipo ({ip}) se comportó como un programa malicioso que intenta saltar a otros equipos de la "
                   "red. El sistema lo aisló de inmediato."),
    "indeterminado": ("Este equipo ({ip}) hizo algo fuera de lo común en la red y el sistema lo aisló por precaución. "
                      "Con los datos disponibles no está claro si es un ataque."),
    "falso_positivo": ("Este equipo ({ip}) hizo algo que el sistema tomó como sospechoso y lo aisló, pero parece una "
                       "falsa alarma."),
}
QUE_PASO_ATENUANTE = " Ya pasó antes y un administrador lo marcó como falsa alarma."
SENAL_M3 = ("Envió muchos datos por el servicio de carpetas compartidas de Windows en muy poco tiempo, algo típico de "
            "cuando se copian o cifran archivos en otros equipos.")
SENALES = {  # campo de la alerta → plantilla con el valor (v) y el umbral vigente (u)
    "M5_count": "Hizo {v} conexiones directas a otros equipos internos; lo esperado es menos de {u}.",
    "M1_nuevos_flujos_por_30s": "Abrió {v} conexiones nuevas en 30 segundos; lo esperado es {u} o menos.",
    "M2_destinos_unicos_por_30s": "Intentó conectarse a {v} equipos distintos en 30 segundos; lo esperado es {u} o menos.",
    "M4_packet_in_por_min": "Obligó a la red a decidir sobre {v} conexiones desconocidas por minuto; lo esperado es {u} o menos.",
}
SENAL_AGRAVANTE = "El historial muestra señales graves: {motivos}."
SENAL_ATENUANTE = "Un administrador ya marcó este mismo comportamiento como falsa alarma varias veces."
UNA_SENAL = "Solo esta señal pasó su límite de alerta; con una sola no se puede confirmar un ataque."
SIN_SENALES = ("Ninguna señal pasó su límite de alerta: el sistema de la red aisló el equipo por su propia regla.",
               "No tenemos más datos para afinar el análisis.")
PODRIA_EQUIVOCARNOS = {
    "confirmado": ("Una copia de seguridad, una actualización de programas o una herramienta de administración también "
                   "pueden conectarse a muchos equipos a la vez. Además, no vemos a qué equipos ni servicios se conectó."),
    "indeterminado": ("Puede ser actividad normal, como una copia de seguridad o una actualización. No vemos a qué "
                      "equipos ni servicios se conectó, y por eso no podemos confirmarlo."),
    "falso_positivo": ("Si este equipo tuviera un programa malicioso que se mueve despacio, podría parecer actividad "
                       "normal. Por eso conviene revisar el equipo antes de dejarlo sin vigilancia."),
}


def nivel_confianza(total: float | None, hitl: float, vlan: float) -> str | None:
    """baja < HITL ≤ media ≤ VLAN < alta; None si no hay confianza (indeterminado)."""
    if total is None:
        return None
    return "baja" if total < hitl else "alta" if total > vlan else "media"


def _motivos_agravante(motivo: str | None) -> str:
    return "; ".join(texto for clave, texto in MOTIVOS_AGRAVANTE if clave in (motivo or "")) or "hay antecedentes graves"


def bloque_recomendacion(resultado: dict[str, Any], confianza: dict[str, Any], dictamen: dict[str, Any]) -> str:
    """Bloque 3 desde escalera.decidir() y escalera.calcular_confianza(): nunca del LLM."""
    accion, tipo = resultado["accion_recomendada"], dictamen.get("tipo")
    nivel = nivel_confianza(confianza["total"], resultado["umbrales"]["ESCALON_HITL"], resultado["umbrales"]["ESCALON_VLAN"])
    if resultado["modificada_por_dictamen"]:
        if tipo == "agravante":
            texto = AGRAVANTE_CAMBIO.format(escalon=ESCALON[resultado["escalon_por_confianza"]],
                                            motivos=_motivos_agravante(dictamen["motivo"]))
        else:
            texto = CONTRADICCION if resultado["motivo_modificacion"] == MOTIVO_CONTRADICCION else DEFENSA
    else:
        if accion in ACCION:
            texto = ACCION[accion].format(nivel=nivel)
        elif nivel is None:
            texto = ACCION_INDETERMINADO
        else:
            texto = ACCION_BAJA.format(veredicto=VEREDICTO[confianza["veredicto_usado"]].capitalize())
        if tipo == "agravante":
            texto += AGRAVANTE_EXTRA.format(motivos=_motivos_agravante(dictamen["motivo"]))
        elif tipo == "atenuante":
            texto += ATENUANTE_EXTRA
    return texto + (CIERRE_MANTENER if accion == "mantener_drop" else CIERRE_RELAJAR)


def _numero(valor: float) -> str:
    return str(int(valor)) if float(valor).is_integer() else str(valor)


def plantilla_que_paso(veredicto: str, ip: str, dictamen: dict[str, Any]) -> str:
    atenuante = QUE_PASO_ATENUANTE if dictamen.get("tipo") == "atenuante" and veredicto == "indeterminado" else ""
    return QUE_PASO[veredicto].format(ip=ip) + atenuante


def plantilla_por_que(alerta: AlertaEntrada, evaluadas: dict, dictamen: dict[str, Any]) -> list[str]:
    """2 o 3 frases: el dictamen primero; luego las señales que pasaron su límite, de mayor a menor peso."""
    frases = []
    if dictamen.get("tipo") == "agravante":
        frases.append(SENAL_AGRAVANTE.format(motivos=_motivos_agravante(dictamen["motivo"])))
    elif dictamen.get("tipo") == "atenuante":
        frases.append(SENAL_ATENUANTE)
    senales = [SENAL_M3] if alerta.metricas.M3_smb_anomalo or "M3" in alerta.umbrales_superados else []
    senales += [plantilla.format(v=_numero(evaluadas[campo]["valor"]), u=_numero(evaluadas[campo]["umbral"]))
                for campo, plantilla in SENALES.items() if evaluadas.get(campo, {}).get("supera")]
    if len(senales) == 1:
        senales.append(UNA_SENAL)
    frases += senales or list(SIN_SENALES)
    return frases[:3]


def plantilla_podria_equivocarnos(veredicto: str) -> str:
    return PODRIA_EQUIVOCARNOS[veredicto]


def respaldados(alerta: AlertaEntrada, evaluadas: dict, alertas_agrupadas: int) -> tuple[list[float], list[str]]:
    """Cifras e IPs que puede citar un bloque simple: SOLO datos de la alerta y umbrales (sin desviaciones ni contexto)."""
    numeros = [alertas_agrupadas, *(x for x in puntajes(alerta.crudo) if x is not None)]
    for e in evaluadas.values():
        numeros += [e["valor"], e["umbral"]]
    return numeros, [alerta.host_origen]


def revisar(salida: Any, fuente: str, veredicto_final: str, permitidos: list[float], ips: list[str]) -> dict[str, Any]:
    """Bloques del LLM aceptados (None → plantilla), reemplazos con su motivo y cifras que penalizan."""
    aceptados: dict[str, Any] = dict.fromkeys(BLOQUES_LLM)
    revision = {"aceptados": aceptados, "reemplazos": [], "cifras": []}
    if fuente != "llm":
        return revision
    textos = {b: (v if isinstance(v, list) else [v]) for b in BLOQUES_LLM if (v := getattr(salida, b, None))}
    problemas = verificador.verificar_bloques(textos, permitidos, ips)
    for bloque, valor in textos.items():
        problema = problemas.get(bloque)
        if problema and problema["cifras"]:
            revision["cifras"] += problema["cifras"]
            motivo = f"cifra no respaldada: {', '.join(problema['cifras'])}"
        elif problema:
            motivo = f"jerga: {', '.join(problema['jerga'])}"
        elif bloque == "que_paso" and veredicto_final != salida.veredicto:
            motivo = "el dictamen cambió el veredicto"
        else:
            aceptados[bloque] = getattr(salida, bloque)
            continue
        revision["reemplazos"].append({"bloque": bloque, "motivo": motivo})
    return revision


def penalizar(verificacion: dict[str, Any], revision: dict[str, Any]) -> dict[str, Any]:
    """Las cifras inventadas en los bloques simples cuentan en el verificador: misma penalización que hoy."""
    if not revision["cifras"]:
        return verificacion
    numeros = list(dict.fromkeys([*verificacion["numeros_no_respaldados"], *revision["cifras"]]))
    return {"resultado": "posible_alucinacion", "numeros_no_respaldados": numeros}


def armar(revision: dict[str, Any], alerta: AlertaEntrada, evaluadas: dict, dictamen: dict[str, Any],
          veredicto_final: str, confianza: dict[str, Any], resultado: dict[str, Any]) -> dict[str, Any]:
    """Sección explicacion_simple del informe 1.2."""
    aceptados = revision["aceptados"]
    plantillas = {
        "que_paso": lambda: plantilla_que_paso(veredicto_final, alerta.host_origen, dictamen),
        "por_que": lambda: plantilla_por_que(alerta, evaluadas, dictamen),
        "podria_equivocarnos": lambda: plantilla_podria_equivocarnos(veredicto_final),
    }
    bloques = {b: aceptados[b] if aceptados[b] else plantillas[b]() for b in BLOQUES_LLM}
    return {**bloques, "que_recomendamos": bloque_recomendacion(resultado, confianza, dictamen),
            "fuentes": {b: "llm" if aceptados[b] else "reglas" for b in BLOQUES_LLM},
            "reemplazos": revision["reemplazos"]}
