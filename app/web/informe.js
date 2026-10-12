// Informe auditable del incidente (Fase 6.8b) en 4 secciones. Todo el texto entra como texto (nunca HTML).
// "No disponible" se ve en gris; la verificación anti-alucinación, como badge.

import { badge, el, mono } from "./dom.js";

const NO_DISPONIBLE = "no disponible en la telemetría actual";
const CAMPOS_TELEMETRIA = [["destinos", "Destinos"], ["puertos", "Puertos"], ["protocolo", "Protocolo"],
  ["duracion_sesiones", "Duración de sesiones"], ["varianza_intervalos", "Varianza de intervalos"], ["linea_base", "Línea base"]];
const PESO = { alto: "critica", medio: "alta", bajo: "info" };

function bloque(titulo, ...contenido) {
  return el("div", { class: "bloque informe" }, el("h3", {}, titulo), ...contenido);
}

function dl(pares) {
  return el("dl", { class: "detalle-grid" }, pares.map(([t, v]) => [el("dt", {}, t), el("dd", {}, v ?? "—")]));
}

function valor(texto) {
  return texto === NO_DISPONIBLE ? el("span", { class: "no-disponible" }, texto) : texto;
}

function tabla(titulos, filas) {
  return el("table", {}, el("thead", {}, el("tr", {}, titulos.map((t) => el("th", {}, t)))), el("tbody", {}, filas));
}

function evidencia(e) {
  const filas = Object.entries(e.metricas).map(([nombre, m]) =>
    el("tr", {}, el("td", { class: "mono" }, nombre), el("td", { class: "mono" }, String(m.valor)),
      el("td", { class: "mono" }, `${m.comparacion} ${m.umbral}`), el("td", { class: "mono" }, `×${m.desviacion.toFixed(2)}`),
      el("td", {}, m.supera ? badge("supera", "alta") : badge("no supera", "info"))));
  return bloque(
    "1. Evidencia objetiva",
    dl([["Host", mono(e.host_origen)], ["Switch", mono(e.dpid_switch ?? "—")], ["Fuente de los umbrales", e.fuente_umbrales],
      ["M3 (SMB anómalo)", String(e.M3_smb_anomalo)], ["M5 (flujos east-west)", String(e.M5_flujos_east_west)],
      ["Puntaje de Ryu", e.puntaje_total === null ? valor(NO_DISPONIBLE) : mono(`${e.puntaje_total}/${e.puntaje_maximo}`)],
      ["Umbrales superados (Ryu)", mono(e.umbrales_superados_ryu.join(", ") || "ninguno")]]),
    tabla(["Métrica", "Valor", "Umbral", "Desviación", ""], filas),
    e.discrepancias.length ? el("p", { class: "mensaje error" }, `Difiere de lo que envió Ryu: ${e.discrepancias.join(", ")}`) : null,
    dl(CAMPOS_TELEMETRIA.map(([clave, titulo]) => [titulo, valor(e[clave])])),
  );
}

function mitre(m) {
  return bloque(
    "2. Mapeo MITRE ATT&CK",
    dl([["Técnica", [mono(m.tecnica_id), " ", m.nombre, m.tecnica_corregida ? [" ", badge("corregida a la lista cerrada", "alta")] : null]],
      ["Táctica", m.tactica], ["Hipótesis de fase (LLM)", m.hipotesis_fase]]),
  );
}

function razonamiento(r, t) {
  const d = r.dictamen_historial;
  const verificado = r.verificacion.resultado === "verificado";
  return bloque(
    "3. Razonamiento y certeza",
    el("p", {}, "Veredicto final: ", badge(t.veredicto_final, "etiqueta"), " · veredicto del LLM: ", badge(t.veredicto_llm, "etiqueta"),
      d.tipo ? [" · dictamen ", badge(d.tipo, d.tipo === "agravante" ? "critica" : "ok"), ` ${d.motivo}`] : " · sin dictamen de historial"),
    tabla(["Factor", "Peso", "Por qué"], r.factores_ponderados.map((f) =>
      el("tr", {}, el("td", {}, f.factor), el("td", {}, badge(f.peso, PESO[f.peso] ?? "info")), el("td", {}, f.porque)))),
    dl([["Contexto aplicado", r.contexto_aplicado], ["Descarte de actividad benigna", r.descarte_benigno],
      ["Confianza", [mono(r.confianza_modelo.valor === null ? "—" : r.confianza_modelo.valor.toFixed(2)), ` (${r.confianza_modelo.rotulo})`]],
      ["Solidez de la evidencia", [mono(r.solidez_evidencia.valor.toFixed(2)), ` · ${r.solidez_evidencia.formula}`]],
      ["Verificación", [badge(verificado ? "verificado" : "posible alucinación", verificado ? "ok" : "critica"),
        verificado ? "" : ` números sin respaldo: ${r.verificacion.numeros_no_respaldados.join(", ")}`]]]),
    el("h4", {}, "Limitaciones"),
    r.limitaciones.length ? el("ul", {}, r.limitaciones.map((l) => el("li", {}, l))) : el("p", { class: "mensaje" }, "Ninguna declarada."),
  );
}

function exportar(i) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(i.informe, null, 2)], { type: "application/json" }));
  const enlace = document.createElement("a");
  enlace.href = url;
  enlace.download = `informe_incidente_${i.id}.json`;
  enlace.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function trazabilidad(t, i) {
  return bloque(
    "4. Trazabilidad y acción",
    dl([["Incidente", mono(`#${t.incidente_id}`)], ["Hash de auditoría de la explicación", mono(t.hash_auditoria_explicacion ?? "—")],
      ["Detección en Ryu (UTC)", mono(t.t_ryu ?? "—")], ["Explicado (UTC)", mono(t.t_explicado)],
      ["Modelo", mono(t.modelo)], ["Fuente", t.fuente_explicacion], ["Versión del prompt", mono(t.version_prompt)],
      ["Variante", t.variante], ["Acción propuesta", [mono(t.accion_propuesta), ` (origen: ${t.origen_accion}, confianza ${t.confianza_accion.toFixed(2)})`]],
      ["Reversible", t.reversible ? "sí" : "no"], ["Justificación", t.justificacion_accion || "—"],
      ["Decisión de la política", [mono(t.decision_politica.resultado ?? "—"), t.decision_politica.motivos.length ? ` · ${t.decision_politica.motivos.join(" · ")}` : ""]],
      ["Acción final", mono(t.accion_final ?? "—")]]),
    el("button", { onclick: () => exportar(i) }, "Exportar informe (JSON)"),
  );
}

export function contenidoInforme(i) {
  const r = i.informe;
  return [evidencia(r.evidencia_objetiva), mitre(r.mitre), razonamiento(r.razonamiento, r.trazabilidad_y_accion),
    trazabilidad(r.trazabilidad_y_accion, i)];
}
