// Informe auditable del incidente (Fase 6.8b) en 4 tarjetas XAI. Todo el texto entra como texto (nunca HTML).
// "No disponible" se ve en gris; la verificación anti-alucinación, como badge.

import { badge, badgeVeredicto, duracion, el, hora, mono, msEntre } from "./dom.js";
import { ACCION } from "./recomendaciones.js";

const NO_DISPONIBLE = "no disponible en la telemetría actual";
const CAMPOS_TELEMETRIA = [["destinos", "Destinos"], ["puertos", "Puertos"], ["protocolo", "Protocolo"],
  ["duracion_sesiones", "Duración de sesiones"], ["varianza_intervalos", "Varianza de intervalos"], ["linea_base", "Línea base"]];
const PESO = { alto: ["critica", 100], medio: ["alta", 66], bajo: ["info", 33] };
const ETAPAS = [["t_ryu", "Detección en Ryu"], ["t_recibido", "Recibido por el agente"], ["t_explicado", "Explicado"],
  ["t_notificado", "Notificado (primer correo)"], ["t_desbloqueado", "Desbloqueado por el operador"]];

export function tarjeta(titulo, ...contenido) {
  return el("section", { class: "panel tarjeta" }, el("h2", {}, titulo), ...contenido);
}

function dl(pares) {
  return el("dl", { class: "detalle-grid" }, pares.map(([t, v]) => [el("dt", {}, t), el("dd", {}, v ?? "—")]));
}

function valor(texto) {
  return texto === NO_DISPONIBLE ? el("span", { class: "no-disponible" }, texto) : texto;
}

function tabla(titulos, filas) {
  return el("div", { class: "tabla-contenedor" },
    el("table", {}, el("thead", {}, el("tr", {}, titulos.map((t) => el("th", {}, t)))), el("tbody", {}, filas)));
}

// Barra proporcional a la desviación (valor/umbral); la marca vertical es el umbral (×1).
function barraDesviacion(m, escala) {
  const ancho = Math.min(100, (100 * m.desviacion) / escala);
  return el("span", { class: "desviacion" },
    el("span", { class: `barra-desv ${m.supera ? "supera" : ""}`, style: `--ancho: ${ancho}%; --umbral: ${100 / escala}%`, role: "presentation" }),
    mono(`×${m.desviacion.toFixed(2)}`), " ", m.supera ? badge("supera", "alta") : badge("no supera", "info"));
}

function evidencia(e) {
  const metricas = Object.entries(e.metricas);
  const escala = Math.max(2, ...metricas.map(([, m]) => m.desviacion));
  const filas = metricas.map(([nombre, m]) =>
    el("tr", {}, el("td", { class: "mono" }, nombre), el("td", { class: "mono" }, String(m.valor)),
      el("td", { class: "mono" }, `${m.comparacion} ${m.umbral}`), el("td", {}, barraDesviacion(m, escala))));
  return tarjeta(
    "1 · Evidencia objetiva",
    dl([["Fuente de los umbrales", e.fuente_umbrales],
      ["Puntaje de Ryu", e.puntaje_total === null ? valor(NO_DISPONIBLE) : mono(`${e.puntaje_total}/${e.puntaje_maximo}`)],
      ["Umbrales superados (Ryu)", mono(e.umbrales_superados_ryu.join(", ") || "ninguno")],
      ["M3 (SMB anómalo) · M5 (east-west)", mono(`${e.M3_smb_anomalo} · ${e.M5_flujos_east_west}`)]]),
    tabla(["Métrica", "Valor", "Umbral", "Desviación"], filas),
    e.discrepancias.length ? el("p", { class: "mensaje error" }, `Difiere de lo que envió Ryu: ${e.discrepancias.join(", ")}`) : null,
    el("h3", {}, "Telemetría"),
    dl(CAMPOS_TELEMETRIA.map(([clave, titulo]) => [titulo, valor(e[clave])])),
  );
}

function mitre(m) {
  return tarjeta(
    "2 · Mapeo MITRE ATT&CK",
    dl([["Técnica", [mono(m.tecnica_id), m.tecnica_corregida ? [" ", badge("corregida a la lista cerrada", "alta")] : null]],
      ["Nombre", m.nombre], ["Táctica", m.tactica], ["Hipótesis de fase (LLM)", m.hipotesis_fase]]),
  );
}

function factor(f) {
  const [clase, ancho] = PESO[f.peso] ?? ["info", 0];
  return el("li", { class: "factor" },
    el("span", { class: "factor-titulo" }, el("strong", {}, f.factor), " ", badge(f.peso, clase)),
    el("span", { class: `barra peso ${clase}`, role: "presentation" }, el("span", { style: `width: ${ancho}%` })),
    el("span", { class: "secundario" }, f.porque));
}

function razonamiento(r, t) {
  const d = r.dictamen_historial;
  const verificado = r.verificacion.resultado === "verificado";
  const confianza = r.confianza_modelo.valor;
  return tarjeta(
    "3 · Razonamiento",
    el("p", {}, ...(t.fuente_explicacion === "reglas" ? ["Sin LLM (respaldo por reglas)"] : ["Veredicto del LLM: ", badgeVeredicto(t.veredicto_llm)]),
      " → final: ", badgeVeredicto(t.veredicto_final),
      d.tipo ? [" · dictamen ", badge(d.tipo, d.tipo === "agravante" ? "critica" : "ok"), ` ${d.motivo}`] : " · sin dictamen de historial"),
    el("h3", {}, "Factores ponderados"),
    el("ul", { class: "factores" }, r.factores_ponderados.map(factor)),
    dl([["Confianza", confianza === null ? "—" : el("span", { class: "confianza" },
        el("span", { class: "barra" }, el("span", { style: `width: ${Math.round(100 * confianza)}%` })), mono(confianza.toFixed(2)), ` (${r.confianza_modelo.rotulo})`)],
      ["Solidez de la evidencia", [mono(r.solidez_evidencia.valor.toFixed(2)), ` · ${r.solidez_evidencia.formula}`]],
      ["Contrafáctico (descarte de actividad benigna)", r.descarte_benigno],
      ["Contexto aplicado", r.contexto_aplicado],
      ["Verificador", [badge(verificado ? "verificado" : "posible alucinación", verificado ? "ok" : "critica"),
        verificado ? "" : ` números sin respaldo: ${r.verificacion.numeros_no_respaldados.join(", ")}`]]]),
    el("h3", {}, "Limitaciones"),
    r.limitaciones.length ? el("ul", {}, r.limitaciones.map((l) => el("li", {}, l))) : el("p", { class: "mensaje" }, "Ninguna declarada."),
  );
}

// Línea de tiempo t_ryu → t_recibido → t_explicado → t_notificado → t_desbloqueado con el delta de cada tramo.
export function lineaTiempo(i) {
  let anterior = null;
  const pasos = ETAPAS.map(([clave, nombre]) => {
    const t = i[clave];
    const delta = t && anterior ? `+${duracion(msEntre(anterior, t))}` : "";
    if (t) anterior = t;
    return el("li", { class: t ? "hecho" : "" }, el("span", {}, nombre), el("span", { class: "mono" }, hora(t)),
      delta ? el("span", { class: "delta" }, delta) : null);
  });
  return el("ol", { class: "tiempos" }, pasos);
}

function exportar(i) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(i.informe, null, 2)], { type: "application/json" }));
  const enlace = document.createElement("a");
  enlace.href = url;
  enlace.download = `informe_incidente_${i.id}.json`;
  enlace.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Fase 9a/9b: "Confianza 0.78: Limitar tasa (ejecutado | recomendado | falló la ejecución)" con el desglose desplegable (C7).
export function resumenEscalera(t, abierto = false) {
  const c = t.confianza_calculada;
  const e = t.escalera;
  const accion = ACCION[e.accion_recomendada] ?? e.accion_recomendada;
  const final = t.accion_final ?? "";
  const estado = final === e.accion_recomendada ? "ejecutado" : final === `${e.accion_recomendada}_simulada` ? "ejecutado, simulado"
    : final === "fallida" ? "falló la ejecución" : "recomendado";
  const titulo = c.total === null ? `Sin escalón (${c.veredicto_usado}): ${accion}, decide el operador`
    : `Confianza ${c.total.toFixed(2)}: ${accion} (${estado})`;
  return el("details", { class: "plegable", "data-clave": "escalera", open: abierto }, el("summary", {}, titulo), desgloseEscalera(t));
}

function desgloseEscalera(t) {
  const c = t.confianza_calculada;
  const e = t.escalera;
  const componentes = c.total === null ? "no se calcula (diagnóstico indeterminado)"
    : `Ryu ${c.termino_ryu.toFixed(4)} (s = ${c.s.toFixed(4)}, ${c.fuente_ryu}) + veredicto ${c.termino_veredicto.toFixed(2)} (${c.veredicto_usado}) − penalización ${c.penalizacion.toFixed(2)}`;
  return dl([["Fórmula", mono(c.formula)], ["Componentes", componentes],
    ["Penalización", c.motivos_penalizacion.length ? c.motivos_penalizacion.join(" · ") : "ninguna"],
    ["Escalón por confianza", ACCION[e.escalon_por_confianza] ?? e.escalon_por_confianza],
    ["Acción recomendada", ACCION[e.accion_recomendada] ?? e.accion_recomendada],
    ["Modificada por el dictamen", e.modificada_por_dictamen ? `sí: ${e.motivo_modificacion}` : "no"],
    ["Umbrales", `HITL ${e.umbrales.ESCALON_HITL} · VLAN ${e.umbrales.ESCALON_VLAN}`],
    ["Sugerida por el LLM (informativa)", t.accion_sugerida_llm ?? "—"]]);
}

function trazabilidad(t, i, extras) {
  return tarjeta(
    "4 · Trazabilidad y acción",
    dl([["Incidente", mono(`#${t.incidente_id}`)], ["Hash de auditoría", el("span", { class: "mono hash" }, t.hash_auditoria_explicacion ?? "—")],
      ["Modelo", t.fuente_explicacion === "reglas" ? `no usado (respaldo por reglas: ${i.motivo_respaldo_legible})` : mono(t.modelo)], ["Fuente", t.fuente_explicacion], ["Versión del prompt", mono(t.version_prompt)], ["Variante", t.variante],
      ["Acción propuesta", [mono(t.accion_propuesta), ` (origen: ${t.origen_accion}, confianza ${t.confianza_accion === null ? "—" : t.confianza_accion.toFixed(2)})`]],
      ["Decisión de la política", [mono(t.decision_politica.resultado ?? "—"), t.decision_politica.motivos.length ? ` · ${t.decision_politica.motivos.join(" · ")}` : ""]],
      ["Acción final", mono(t.accion_final ?? "—")], ["Reversible", t.reversible ? "sí" : "no"]]),
    t.escalera ? [el("h3", {}, "Escalera de respuesta (C7)"), desgloseEscalera(t)] : null,
    el("h3", {}, "Línea de tiempo"), lineaTiempo(i),
    ...extras,
    el("button", { onclick: () => exportar(i) }, "Exportar informe (JSON)"),
  );
}

// extras: nodos que la página del detalle agrega al final de la tarjeta 4 (decisión, acciones y notificación).
export function tarjetasInforme(i, extras = []) {
  const r = i.informe;
  return [evidencia(r.evidencia_objetiva), mitre(r.mitre), razonamiento(r.razonamiento, r.trazabilidad_y_accion),
    trazabilidad(r.trazabilidad_y_accion, i, extras)];
}
