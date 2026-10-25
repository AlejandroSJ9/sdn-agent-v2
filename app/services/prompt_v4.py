"""Prompts v4.0 (sin contexto) y v4.1 (con contexto), Fase D3: v3.0 / v3.1 SIN CAMBIOS + explicación simple.

v4 = v3 + (a) cuatro reglas de lenguaje simple justo antes de "- Basa tu análisis…" y (b) tres campos nuevos al
final de la respuesta (que_paso, por_que, podria_equivocarnos). Toda línea de v3 sigue igual en v4; solo la de
recomendacion_operador gana una coma (tests/unit/test_prompt_v4.py). Activo por defecto desde la D3.1 (PROMPT_V4).
"""

from app.services.prompt import _ULTIMA_REGLA, SYSTEM_PROMPT_V30, SYSTEM_PROMPT_V31

REGLAS_SIMPLES = (
    "- Explicación simple: además del análisis técnico, escribe que_paso, por_que y podria_equivocarnos para un "
    "administrador de red que no es experto en seguridad y para una persona no técnica (un jefe). Usa frases cortas en "
    "español simple y llama al host \"este equipo\". En esos tres campos no escribas nombres de métricas (M1, M2, M3, "
    "M4, M5, M5_count), identificadores ni nombres de MITRE ATT&CK, siglas sin explicar ni las palabras \"east-west\", "
    "\"packet-in\", \"SMB\", \"flujo\", \"umbral\" o \"desviación\"; si un término es indispensable, explícalo en la "
    "misma frase. No digas qué hacer: la recomendación la arma el sistema.\n"
    "- Glosario para los textos simples: M1 = \"conexiones nuevas que abrió el equipo en 30 segundos\"; M2 = \"equipos "
    "distintos a los que intentó conectarse en 30 segundos\"; M3 = \"envió muchos datos por el servicio de carpetas "
    "compartidas de Windows en muy poco tiempo\"; M4 = \"veces por minuto que el equipo obligó a la red a decidir qué "
    "hacer con una conexión desconocida\"; M5 = \"conexiones directas entre equipos internos\" (M5_count = cuántas "
    "hizo); movimiento lateral = \"intentar saltar a otros equipos de la red\"; ransomware = \"programa malicioso que "
    "cifra archivos para pedir un rescate\"; drop_total = \"aislado por completo de la red\"; falso_positivo = \"falsa "
    "alarma\".{glosario_contexto}\n"
    "- Cifras en los textos simples: usa SOLO los valores de \"metricas\" y \"umbrales\" de <datos_red>. Al citar una "
    "métrica, da su valor y lo esperado según su umbral: para M1, M2 y M4, \"lo esperado es N o menos\"; para "
    "M5_count, \"lo esperado es menos de N\". Ejemplo: \"Intentó conectarse a <valor de M2> equipos distintos en 30 "
    "segundos; lo esperado es <umbral de M2> o menos.\" Las carpetas compartidas y las conexiones directas no tienen "
    "cifra de referencia: di solo si ocurrieron. No calcules porcentajes, promedios ni otras cifras{cifras_contexto}, y "
    "escribe \"por minuto\" en lugar de \"en 1 minuto\".\n"
    "- que_paso debe ser coherente con tu veredicto: si es indeterminado, di que no está claro si es un ataque; si es "
    "falso_positivo, di que parece una falsa alarma.\n"
)
_CONTEXTO = {
    "glosario_contexto": " Del historial: una clasificación 'falso_positivo' del operador = \"un administrador ya "
                         "revisó un caso igual y lo marcó como falsa alarma\"; 'amenaza_contenida' = \"un administrador "
                         "ya contuvo un ataque en este equipo\".",
    "cifras_contexto": "; del historial di cuántas veces pasó algo solo con palabras (\"varias veces\", \"hace poco\")",
}
CAMPOS_FIN_V3 = ' "recomendacion_operador": texto en español, máximo 300 caracteres}'
CAMPOS_V4 = (
    ' "recomendacion_operador": texto en español, máximo 300 caracteres,\n'
    ' "que_paso": texto simple, 1 o 2 frases, máximo 250 caracteres: qué hizo este equipo y que el sistema lo aisló,\n'
    ' "por_que": lista de 2 o 3 frases simples, cada una de máximo 180 caracteres: las señales principales, con su '
    'valor y lo esperado,\n'
    ' "podria_equivocarnos": texto simple, 1 o 2 frases, máximo 300 caracteres: qué actividad legítima o qué dato que '
    'falta podría explicar lo ocurrido}'
)


def _v4(base: str, con_contexto: bool) -> str:
    reglas = REGLAS_SIMPLES.format(**(_CONTEXTO if con_contexto else dict.fromkeys(_CONTEXTO, "")))
    for antes, despues in ((_ULTIMA_REGLA + "\n", reglas + _ULTIMA_REGLA + "\n"), (CAMPOS_FIN_V3, CAMPOS_V4)):
        assert base.count(antes) == 1, antes  # falla al importar si el texto base cambió
        base = base.replace(antes, despues)
    return base


SYSTEM_PROMPT_V40 = _v4(SYSTEM_PROMPT_V30, False)
SYSTEM_PROMPT_V41 = _v4(SYSTEM_PROMPT_V31, True)
