#!/usr/bin/env python3
"""
Verificación end-to-end de la Fase 6.8a+b (dictamen determinista + informe XAI).

Prueba el sistema DESDE AFUERA, como lo usaría Ryu y el operador: envía alertas
reales al agente, desbloquea por la API y revisa el informe guardado. No importa
nada de app/, así que detecta errores de integración que pytest no ve.

Requisitos:
  - Agente en http://localhost:8081 (base limpia recomendada).
  - Mock de Ryu en http://localhost:8080 (con o sin MOCK_CUARENTENA).
  - AGENT_TOKEN, OPERADOR_TOKEN y LLM_MODELO en el entorno (set -a; source .env; set +a). Si LLM_MODELO no
    está en el entorno, se toma del .env de la raíz del proyecto, igual que el agente.
  - Recomendado: APPSCRIPT_URL vacía en el agente para no recibir ~10 correos.

Uso:
  python3 scripts/verificar_fase_6_8ab.py            # con LLM (≈ 8-10 llamadas)
  python3 scripts/verificar_fase_6_8ab.py --sin-llm  # gratis: valida dictamen e informe con reglas

Salida: tabla PASA/FALLA por verificación y código de salida 0 solo si todo pasa.
Restaura la configuración en caliente que modifica (CONTEXTO, LLM, DEDUP, MODO).
"""
import argparse
import json
import os
import random
import re
import sys
import time

import httpx

AGENTE = os.environ.get("AGENTE_URL", "http://localhost:8081")
RYU = os.environ.get("RYU_URL_MOCK", "http://localhost:8080")
AT = os.environ.get("AGENT_TOKEN", "")
OT = os.environ.get("OPERADOR_TOKEN", "")
def _desde_env_del_proyecto(clave: str) -> str:
    """Mismo origen que el agente: el .env de la raíz del proyecto (sin importar app/)."""
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.isfile(ruta):
        return ""
    with open(ruta, encoding="utf-8") as archivo:
        for linea in archivo:
            nombre, igual, valor = linea.strip().partition("=")
            if igual and nombre.strip() == clave:
                return valor.strip()
    return ""


MODELO = os.environ.get("LLM_MODELO") or _desde_env_del_proyecto("LLM_MODELO")

H_AGENTE = {"X-Agent-Token": AT, "Content-Type": "application/json"}
H_OPER = {"X-Operador-Token": OT, "Content-Type": "application/json"}
NO_DISP = "no disponible en la telemetría actual"
TECNICAS = {"T1021.002", "T1021.001", "T1021.006", "T1046", "T1570", "T1021"}
UMBRALES_DEF = {"M1": 20, "M2": 8, "M4": 200, "M5": 5}  # ajustes.md (A4/A5)

resultados = []  # (grupo, nombre, ok, detalle)
cli = httpx.Client(timeout=30)


def check(grupo, nombre, ok, detalle=""):
    resultados.append((grupo, nombre, bool(ok), detalle))
    marca = "PASA " if ok else "FALLA"
    print(f"  [{marca}] {grupo} · {nombre}" + (f"  → {detalle}" if detalle and not ok else ""))
    return ok


def g(d, ruta, defecto=None):
    """Lee una ruta tipo 'a.b.c' de un dict, tolerante a ausencias."""
    cur = d
    for p in ruta.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return defecto
    return cur


# ---------------------------------------------------------------- utilidades
def config(cambios):
    r = cli.put(f"{AGENTE}/api/config", headers=H_OPER, json=cambios)
    r.raise_for_status()
    return r.json()


def leer_config():
    r = cli.get(f"{AGENTE}/api/config", headers=H_OPER)
    r.raise_for_status()
    return r.json()


def bloquear(ip):
    cli.post(f"{RYU}/mock/bloquear/{ip}", headers={"X-Agent-Token": AT})


def alerta(ip, m1, m2, m3, m4, m5, m5c, umbrales):
    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "alerta_nivel": "CRITICO",
        "host_origen": ip,
        "dpid_switch": "0x0000000000000001",
        "contexto_topologico": "VLAN OPS (ID=10), subred 10.0.10.0/24",
        "metricas": {
            "M1_nuevos_flujos_por_30s": m1, "M2_destinos_unicos_por_30s": m2,
            "M3_smb_anomalo": m3, "M4_packet_in_por_min": m4,
            "M5_flujos_east_west": m5, "M5_count": m5c,
        },
        "umbrales_superados": umbrales,
    }
    bloquear(ip)
    r = cli.post(f"{AGENTE}/api/alertas", headers=H_AGENTE, json=payload)
    if r.status_code != 202:
        raise RuntimeError(f"alerta {ip} → HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["incidente_id"], payload


def esperar(inc_id, segundos=90):
    """Espera a que el pipeline termine (informe presente o t_explicado)."""
    fin = time.time() + segundos
    while time.time() < fin:
        r = cli.get(f"{AGENTE}/api/incidentes/{inc_id}", headers=H_OPER)
        if r.status_code == 200:
            d = r.json()
            if d.get("informe") and d.get("t_explicado"):
                return d
        time.sleep(1.5)
    raise TimeoutError(f"incidente {inc_id} sin informe tras {segundos}s")


def desbloquear_fp(ip):
    r = cli.post(f"{AGENTE}/api/hosts/{ip}/desbloquear", headers=H_OPER, json={
        "confirmacion": ip, "motivo": "tarea programada verificada", "clasificacion": "falso_positivo"})
    if r.status_code != 200:
        raise RuntimeError(f"desbloqueo {ip} → HTTP {r.status_code}: {r.text[:200]}")


def esperar_dedup(segundos):
    print(f"    … esperando {segundos}s (ventana de agrupación)")
    time.sleep(segundos)


# ------------------------------------------------- verificaciones del informe
def verificar_informe(caso, inc, payload, con_llm):
    inf = inc.get("informe") or {}
    G = f"Informe {caso}"
    check(G, "4 dimensiones presentes",
          all(k in inf for k in ("evidencia_objetiva", "mitre", "razonamiento", "trazabilidad_y_accion")),
          f"claves: {list(inf)}")

    ev = inf.get("evidencia_objetiva", {})
    check(G, "host_origen exacto", ev.get("host_origen") == payload["host_origen"], ev.get("host_origen"))

    mets = ev.get("metricas", {})
    mapa = {"M1_nuevos_flujos_por_30s": "M1", "M2_destinos_unicos_por_30s": "M2",
            "M4_packet_in_por_min": "M4", "M5_count": "M5"}
    for campo, corto in mapa.items():
        m = mets.get(campo, {})
        enviado = payload["metricas"][campo]
        ok_val = m.get("valor") is not None and abs(float(m["valor"]) - float(enviado)) < 1e-6
        check(G, f"{corto}: valor exacto", ok_val, f"{m.get('valor')} vs {enviado}")
        u = m.get("umbral")
        if u:
            esperada = round(float(enviado) / float(u), 2)
            check(G, f"{corto}: desviación = valor/umbral",
                  m.get("desviacion") is not None and abs(float(m["desviacion"]) - esperada) <= 0.01,
                  f"{m.get('desviacion')} vs {esperada}")
        else:
            check(G, f"{corto}: umbral presente", False, "umbral vacío")

    faltantes = [k for k in ("destinos", "puertos", "protocolo", "duracion_sesiones",
                             "varianza_intervalos", "linea_base") if ev.get(k) != NO_DISP]
    check(G, "telemetría ausente declarada 'no disponible'", not faltantes, f"incorrectos: {faltantes}")
    check(G, "fuente de umbrales indicada", bool(ev.get("fuente_umbrales")), "")

    mi = inf.get("mitre", {})
    check(G, "técnica en lista cerrada", mi.get("tecnica_id") in TECNICAS, mi.get("tecnica_id"))
    check(G, "nombre y táctica oficiales (código)", bool(mi.get("nombre")) and bool(mi.get("tactica")), "")
    if con_llm:
        check(G, "hipótesis de fase redactada", bool(mi.get("hipotesis_fase")), "")

    rz = inf.get("razonamiento", {})
    if con_llm:
        fp = rz.get("factores_ponderados") or []
        check(G, "2 a 4 factores ponderados", 2 <= len(fp) <= 4, f"{len(fp)}")
        check(G, "pesos válidos", all(f.get("peso") in ("alto", "medio", "bajo") for f in fp), "")
        check(G, "limitaciones declaradas", bool(rz.get("limitaciones")), "")
        ver = rz.get("verificacion")
        estado = ver.get("resultado") if isinstance(ver, dict) else ver
        check(G, "verificador ejecutado", estado in ("verificado", "posible_alucinacion"), str(ver))
        # Verificación independiente: números del texto del LLM respaldados por los datos
        respaldo = set()
        for m in mets.values():
            for k in ("valor", "umbral", "desviacion"):
                if m.get(k) is not None:
                    v = float(m[k]); respaldo |= {round(v, 2), round(v * 100, 1)}
        for k in ("puntaje_total", "puntaje_maximo"):
            if ev.get(k) is not None:
                respaldo.add(float(ev[k]))
        respaldo |= {float(len(payload["umbrales_superados"])), float(4 - len(payload["umbrales_superados"])),
                     2.0, 30.0, 60.0}
        ctx = json.dumps(inc.get("contexto_usado") or {})
        respaldo |= {float(x) for x in re.findall(r"\d+(?:\.\d+)?", ctx)}
        textos = " ".join([str(mi.get("hipotesis_fase", "")), str(rz.get("contexto_aplicado", "")),
                           str(rz.get("descarte_benigno", ""))] + [str(f.get("porque", "")) for f in fp])
        textos = re.sub(r"\b(M[1-5](_count)?|T\d{4}(\.\d{3})?|v\d\.\d|\d+\.\d+\.\d+\.\d+)\b", " ", textos)
        sospechosos = [n for n in re.findall(r"\d+(?:\.\d+)?", textos)
                       if not any(abs(float(n) - r) <= max(0.01, 0.01 * abs(r)) for r in respaldo)]
        coherente = (not sospechosos) == (estado == "verificado") or estado == "posible_alucinacion"
        check(G, "verificador coherente con revisión independiente", coherente,
              f"números sin respaldo según esta revisión: {sospechosos}, verificador: {estado}")

    tr = inf.get("trazabilidad_y_accion", {})
    check(G, "incidente_id coincide", tr.get("incidente_id") == inc.get("id"), f"{tr.get('incidente_id')} vs {inc.get('id')}")
    check(G, "hash de auditoría presente", bool(tr.get("hash_auditoria_explicacion") or tr.get("hash_auditoria")), "")
    check(G, "marcas de tiempo ISO-8601", all(re.match(r"\d{4}-\d{2}-\d{2}T", str(tr.get(k, ""))) for k in ("t_ryu", "t_explicado")), "")
    if con_llm and MODELO:
        check(G, "modelo = LLM_MODELO vigente", tr.get("modelo") == MODELO, f"{tr.get('modelo')} vs {MODELO}")
    check(G, "veredicto final del informe = columna veredicto", tr.get("veredicto_final") == inc.get("veredicto"),
          f"{tr.get('veredicto_final')} vs {inc.get('veredicto')}")
    check(G, "acción reversible declarada", tr.get("reversible") is True, str(tr.get("reversible")))
    check(G, "nunca se propone desbloquear", "desbloque" not in json.dumps(tr).lower() or tr.get("accion_propuesta") != "desbloquear", "")
    if inc.get("veredicto") == "confirmado":
        check(G, "confirmado ⇒ nunca cuarentena", tr.get("accion_propuesta") != "pasar_a_cuarentena"
              and "cuarentena" not in str(tr.get("accion_final", "")), f"{tr.get('accion_propuesta')} / {tr.get('accion_final')}")
    check(G, "resumen ≤ 600 caracteres", len(inc.get("explicacion") or "") <= 600, f"{len(inc.get('explicacion') or '')}")
    return inf


# --------------------------------------------------------------- escenarios
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-llm", action="store_true", help="valida con reglas (sin gasto)")
    args = ap.parse_args()
    con_llm = not args.sin_llm

    if not AT or not OT:
        sys.exit("Faltan AGENT_TOKEN u OPERADOR_TOKEN en el entorno (set -a; source .env; set +a).")
    try:
        cli.get(f"{AGENTE}/api/salud").raise_for_status()
        cli.get(f"{RYU}/ryu/estado", headers={"X-Agent-Token": AT}).raise_for_status()
    except Exception as e:
        sys.exit(f"Agente o mock de Ryu no disponibles: {e}")

    original = leer_config()
    base = random.randint(100, 240)  # subred aleatoria: evita historial de corridas previas
    ip = lambda n: f"10.0.{base}.{n}"
    DEDUP = 10
    print(f"\nVerificación Fase 6.8a+b · {'CON LLM (' + MODELO + ')' if con_llm else 'SIN LLM (reglas)'} · subred 10.0.{base}.0/24\n")

    try:
        config({"CONTEXTO_HABILITADO": True, "LLM_HABILITADO": con_llm, "DEDUP_SEGUNDOS": DEDUP,
                "MODO_DECISION": "recomendar"})

        # ---------- A. Sin historial: decide el LLM / reglas, sin dictamen
        print("A. Sin historial (no debe haber dictamen)")
        i, p = alerta(ip(1), 30, 12, True, 80, True, 9, ["M1", "M2", "M3", "M5"])
        inc = esperar(i)
        check("A", "variante con_contexto", inc.get("variante") == "con_contexto", inc.get("variante"))
        check("A", "versión de prompt v3.1", str(inc.get("version_prompt", "")).startswith("v3.1"), inc.get("version_prompt"))
        check("A", "sin dictamen", not inc.get("dictamen_historial"), inc.get("dictamen_historial"))
        check("A", "veredicto final = veredicto LLM", inc.get("veredicto") == inc.get("veredicto_llm"),
              f"{inc.get('veredicto')} vs {inc.get('veredicto_llm')}")
        verificar_informe("A", inc, p, con_llm)

        # ---------- B. Atenuante: 2 FP del operador con mismos umbrales
        print("\nB. Atenuante (2 falsos positivos verificados, mismos umbrales)")
        umb_b = ["M1", "M2", "M4"]
        for k in range(2):
            i, _ = alerta(ip(2), 22, 17, False, 212, False, 2, umb_b)
            esperar(i)
            desbloquear_fp(ip(2))
            esperar_dedup(DEDUP + 2)
        i, p = alerta(ip(2), 22, 17, False, 212, False, 2, umb_b)
        inc = esperar(i)
        check("B", "dictamen atenuante", inc.get("dictamen_historial") == "atenuante", inc.get("dictamen_historial"))
        check("B", "veredicto final indeterminado", inc.get("veredicto") == "indeterminado", inc.get("veredicto"))
        tr = g(inc, "informe.trazabilidad_y_accion", {})
        check("B", "acción propuesta cuarentena por dictamen",
              tr.get("accion_propuesta") == "pasar_a_cuarentena" and tr.get("origen_accion") == "dictamen",
              f"{tr.get('accion_propuesta')} / {tr.get('origen_accion')}")
        check("B", "confianza de la acción 0.6", abs(float(tr.get("confianza_accion") or 0) - 0.6) < 1e-6, str(tr.get("confianza_accion")))
        check("B", "política: recomendación (umbral 0.85)", g(tr, "decision_politica.resultado") == "recomendar",
              str(tr.get("decision_politica")))
        hist = g(inc, "contexto_usado.historial_host", [])
        check("B", "historial con 2 FP del operador", sum(1 for h in hist if h.get("clasificacion_operador") == "falso_positivo") >= 2, "")
        verificar_informe("B", inc, p, con_llm)

        # ---------- C. Atenuante NO aplica si cambia el patrón
        print("\nC. Mismo host, patrón distinto (el atenuante no debe aplicar)")
        desbloquear_fp(ip(2)); esperar_dedup(DEDUP + 2)
        i, p = alerta(ip(2), 40, 18, True, 250, True, 10, ["M1", "M2", "M3", "M4", "M5"])
        inc = esperar(i)
        check("C", "sin atenuante con umbrales distintos", inc.get("dictamen_historial") != "atenuante", inc.get("dictamen_historial"))

        # ---------- D. Agravante por confirmado reciente en el mismo host
        print("\nD. Agravante (confirmado reciente en el mismo host)")
        i, _ = alerta(ip(3), 40, 18, True, 250, True, 10, ["M1", "M2", "M3", "M4", "M5"])
        primero = esperar(i)
        if primero.get("veredicto") != "confirmado":
            check("D", "precondición: primer incidente confirmado", False, f"salió {primero.get('veredicto')}")
        else:
            esperar_dedup(DEDUP + 2)
            i, p = alerta(ip(3), 22, 17, False, 212, False, 2, ["M1", "M2", "M4"])
            inc = esperar(i)
            check("D", "dictamen agravante", inc.get("dictamen_historial") == "agravante", inc.get("dictamen_historial"))
            check("D", "veredicto final confirmado", inc.get("veredicto") == "confirmado", inc.get("veredicto"))
            check("D", "acción forzada a mantener", g(inc, "informe.trazabilidad_y_accion.accion_propuesta") == "mantener", "")
            verificar_informe("D", inc, p, con_llm)

        # ---------- E. Agravante por campaña (≥ 2 hosts confirmados en la ventana)
        print("\nE. Agravante por campaña (2 hosts confirmados recientes)")
        conf = 0
        for n in (4, 5):
            i, _ = alerta(ip(n), 40, 18, True, 250, True, 10, ["M1", "M2", "M3", "M4", "M5"])
            conf += esperar(i).get("veredicto") == "confirmado"
        if conf < 2:
            check("E", "precondición: 2 hosts confirmados", False, f"solo {conf}")
        else:
            i, p = alerta(ip(6), 22, 9, False, 212, False, 2, ["M1", "M2", "M4"])
            inc = esperar(i)
            check("E", "dictamen agravante por campaña", inc.get("dictamen_historial") == "agravante", inc.get("dictamen_historial"))
            check("E", "veredicto final confirmado", inc.get("veredicto") == "confirmado", inc.get("veredicto"))

        # ---------- F. Agravante gana sobre atenuante
        print("\nF. Agravante gana sobre atenuante (host con 2 FP + campaña activa)")
        i, p = alerta(ip(2), 22, 17, False, 212, False, 2, ["M1", "M2", "M4"])
        inc = esperar(i)
        if conf >= 2:
            check("F", "agravante prevalece", inc.get("dictamen_historial") == "agravante" and inc.get("veredicto") == "confirmado",
                  f"{inc.get('dictamen_historial')} / {inc.get('veredicto')}")

        # ---------- G. Sin contexto: nunca hay dictamen
        print("\nG. Contexto apagado")
        config({"CONTEXTO_HABILITADO": False})
        esperar_dedup(DEDUP + 2)
        i, p = alerta(ip(2), 22, 17, False, 212, False, 2, ["M1", "M2", "M4"])
        inc = esperar(i)
        check("G", "variante sin_contexto y prompt v3.0",
              inc.get("variante") == "sin_contexto" and str(inc.get("version_prompt", "")).startswith("v3.0"),
              f"{inc.get('variante')} / {inc.get('version_prompt')}")
        check("G", "sin dictamen", not inc.get("dictamen_historial"), inc.get("dictamen_historial"))
        verificar_informe("G", inc, p, con_llm)

        # ---------- H. Integridad final
        print("\nH. Integridad")
        r = cli.get(f"{AGENTE}/api/auditoria/verificar", headers=H_OPER).json()
        check("H", "cadena de auditoría íntegra", r.get("integra") is True, str(r))

    except Exception as e:
        check("EJECUCIÓN", "el script terminó sin errores", False, repr(e))
    finally:
        restaurar = {k: original[k] for k in ("CONTEXTO_HABILITADO", "LLM_HABILITADO", "DEDUP_SEGUNDOS", "MODO_DECISION") if k in original}
        try:
            config(restaurar)
            print(f"\nConfiguración restaurada: {restaurar}")
        except Exception as e:
            print(f"\n¡No se pudo restaurar la configuración! {e}  → restáurala desde el panel.")

    total = len(resultados); ok = sum(1 for r in resultados if r[2])
    print(f"\n{'=' * 60}\nResultado: {ok}/{total} verificaciones PASAN")
    for grp, nom, okk, det in resultados:
        if not okk:
            print(f"  FALLA · {grp} · {nom} · {det}")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
