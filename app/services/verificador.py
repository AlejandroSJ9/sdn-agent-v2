"""Verificador anti-alucinación (Fase 6.8b). Función PURA: no bloquea nada, solo informa.

Extrae los números de los textos del LLM y comprueba que cada uno exista entre los valores enviados
(métricas, umbrales, desviaciones, puntajes, minutos y conteos del contexto, cantidad de umbrales
superados / no superados y las constantes que define el prompt), con tolerancia de redondeo.
Los dígitos de nombres (M1, M5_count, T1021.002) no cuentan; una IP que no está en los datos, sí.
Fase D3: verificar_bloques revisa además, por bloque, la explicación simple: cifras (con su propio conjunto de
permitidos, más estricto) y jerga prohibida para un lector no técnico.
"""

import re
from collections.abc import Iterable

CONSTANTES_DEL_PROMPT = (2, 30, 60)  # "2 o más", "30 s", "60 min"
_NOMBRES = re.compile(r"\bM[1-5](?:_[A-Za-z0-9_]+)?|\bT\d{4}(?:\.\d{3})?\b|\bTA\d{4}\b", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_NUMERO = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)(\s*%)?")
# Jerga que no puede aparecer en la explicación simple (sin distinguir mayúsculas).
JERGA = re.compile(
    r"\bM[1-5](?:_count)?\b|\bT1\d{3}(?:\.\d{3})?\b|\bMITRE\b|ATT&CK|east[- ]west|packet[- ]in|\bSMB\b|\bflujos?\b"
    r"|\bumbral(?:es)?\b|\bdesviaci[oó]n(?:es)?\b|\bDROP\b|\bSDN\b|\bRyu\b|\bhosts?\b",
    re.IGNORECASE,
)


def _respaldado(numero: float, permitidos: Iterable[float]) -> bool:
    for valor in permitidos:
        if abs(numero - valor) <= 0.01 or (valor and abs(numero - valor) / abs(valor) <= 0.01):
            return True
    return False


def verificar(textos: Iterable[str], permitidos: Iterable[float], ips: Iterable[str]) -> dict:
    """{"resultado": verificado | posible_alucinacion, "numeros_no_respaldados": [...]}."""
    permitidos = [float(v) for v in (*permitidos, *CONSTANTES_DEL_PROMPT)]
    ips = set(ips)
    no_respaldados: list[str] = []
    for texto in textos:
        for ip in _IPV4.findall(texto):
            if ip not in ips:
                no_respaldados.append(ip)
        limpio = _NOMBRES.sub(" ", _IPV4.sub(" ", texto))
        for numero, porcentaje in _NUMERO.findall(limpio):
            valor = float(numero.replace(",", "."))
            candidatos = (valor, valor / 100) if porcentaje else (valor,)
            if not any(_respaldado(c, permitidos) for c in candidatos):
                no_respaldados.append(numero + (porcentaje.strip() if porcentaje else ""))
    unicos = list(dict.fromkeys(no_respaldados))
    return {"resultado": "posible_alucinacion" if unicos else "verificado", "numeros_no_respaldados": unicos}


def verificar_bloques(bloques: dict[str, list[str]], permitidos: Iterable[float], ips: Iterable[str]) -> dict[str, dict]:
    """{bloque: {"cifras": [no respaldadas], "jerga": [términos]}} solo de los bloques con algún problema."""
    permitidos, ips = list(permitidos), list(ips)
    problemas = {}
    for nombre, textos in bloques.items():
        cifras = verificar(textos, permitidos, ips)["numeros_no_respaldados"]
        jerga = list(dict.fromkeys(m.group() for t in textos for m in JERGA.finditer(t)))
        if cifras or jerga:
            problemas[nombre] = {"cifras": cifras, "jerga": jerga}
    return problemas
