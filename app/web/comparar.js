// Laboratorio: "Comparar corridas" (hasta 4) sobre el conjunto común de casos, con exportación JSON/CSV.

import { api } from "./api.js";
import { $, badge, el, mensaje, vaciar } from "./dom.js";
import { descargar } from "./pruebas.js";

const MAXIMO = 4;

function elegidas() {
  return [...document.querySelectorAll("#comparar-corridas input:checked")].map((casilla) => Number(casilla.value));
}

function limitar() {
  const lleno = elegidas().length >= MAXIMO;
  document.querySelectorAll("#comparar-corridas input").forEach((casilla) => { casilla.disabled = lleno && !casilla.checked; });
}

export async function refrescarCorridasComparables() {
  const contenedor = $("comparar-corridas");
  try {
    const corridas = (await api("/api/laboratorio/corridas")).filter((c) => c.estado === "terminada");
    const previas = new Set(elegidas());
    vaciar(contenedor, corridas.length ? corridas.map((c) =>
      el("label", { class: "casilla" },
        el("input", { type: "checkbox", value: c.id, checked: previas.has(c.id), onchange: limitar }),
        ` #${c.id} · ${c.usar_llm ? "LLM" : "reglas"} · ${c.variante.replace("_", " ")}`)) : el("p", { class: "mensaje" }, "Sin corridas terminadas."));
    limitar();
  } catch (error) {
    vaciar(contenedor, el("p", { class: "mensaje error" }, error.message));
  }
}

function porcentaje(e) {
  return e && e.total ? `${e.aciertos}/${e.total} (${(100 * e.exactitud).toFixed(0)} %)` : "—";
}

function tabla(titulos, filas) {
  return el("table", {}, el("thead", {}, el("tr", {}, titulos.map((t) => el("th", {}, t)))), el("tbody", {}, filas));
}

function celda(c) {
  const veredictos = c.veredictos.map((v) => v ?? "—").join(", ") || "—";
  const ok = c.de > 0 && 2 * c.aciertos > c.de;
  return el("td", { class: ok ? "texto-ok" : "texto-mal" }, `${ok ? "✓" : "✗"} ${veredictos}`, c.de > 1 ? ` (${c.aciertos}/${c.de})` : "");
}

function pintar(r) {
  const ids = r.corridas.map((c) => String(c.id));
  const filas = r.tabla.map((f) =>
    el("tr", {}, el("td", { class: "mono" }, f.caso_id), el("td", {}, f.clase), el("td", {}, f.veredicto_esperado),
      ids.map((id) => celda(f.celdas[id]))));
  const clases = [...new Set(r.tabla.map((f) => f.clase))].sort();
  const resumen = ["global", ...clases.map((c) => `clase ${c}`), "desarrollo", "validacion"].map((fila) =>
    el("tr", {}, el("td", {}, fila), ids.map((id) => {
      const s = r.resumen[id];
      const dato = fila === "global" ? s.global : fila.startsWith("clase ") ? s.por_clase[fila.slice(6)] : s.por_conjunto[fila];
      return el("td", { class: "mono" }, porcentaje(dato));
    })));
  // Fase 6.8a+b: el acierto es sobre el veredicto final; esto muestra el del LLM, los dictámenes y las alucinaciones
  resumen.push(
    el("tr", {}, el("td", {}, "veredicto del LLM"), ids.map((id) => el("td", { class: "mono" }, porcentaje(r.resumen[id].exactitud_veredicto_llm)))),
    el("tr", {}, el("td", {}, "dictámenes (agrav./aten.)"), ids.map((id) => {
      const d = r.resumen[id].dictamenes;
      return el("td", { class: "mono" }, `${d.agravante} / ${d.atenuante}`);
    })),
    el("tr", {}, el("td", {}, "posible alucinación"), ids.map((id) => el("td", { class: "mono" }, String(r.resumen[id].posible_alucinacion)))),
  );
  const d = r.destacados;
  const destacados = d
    ? [el("p", { class: "mensaje" }, "LLM con contexto acierta y reglas con contexto fallan: ", d.llm_con_contexto_acierta_reglas_con_contexto_falla.join(", ") || "ninguno"),
      el("p", { class: "mensaje" }, "Reglas con contexto aciertan y LLM con contexto falla: ", d.reglas_con_contexto_acierta_llm_con_contexto_falla.join(", ") || "ninguno")]
    : [el("p", { class: "mensaje" }, "Destacados: hace falta una corrida LLM y una de reglas, ambas con contexto.")];
  vaciar(
    $("comparar-resultado"),
    el("p", { class: "mensaje" }, `Casos comparados: ${r.casos_comparados.length}`,
      r.casos_excluidos.length ? ` · excluidos: ${r.casos_excluidos.join(", ")}` : ""),
    tabla(["Caso", "Clase", "Esperado", ...r.corridas.map((c) => c.etiqueta)], filas),
    el("h4", {}, "Exactitud por corrida"),
    tabla(["", ...r.corridas.map((c) => c.etiqueta)], resumen),
    el("h4", {}, "Destacados ", badge("informativo", "etiqueta")),
    ...destacados,
  );
}

function consulta() {
  const ids = elegidas();
  if (ids.length < 2) {
    mensaje($("comparar-msg"), "Elige entre 2 y 4 corridas terminadas.", "error");
    return null;
  }
  return `corridas=${ids.join(",")}`;
}

async function comparar() {
  const q = consulta();
  if (!q) return;
  try {
    pintar(await api(`/api/laboratorio/comparar?${q}`));
    mensaje($("comparar-msg"), "");
  } catch (error) {
    mensaje($("comparar-msg"), `No se pudo comparar: ${error.message}`, "error");
  }
}

export function iniciarComparar() {
  $("btn-comparar").addEventListener("click", comparar);
  for (const formato of ["json", "csv"]) {
    $(`btn-comparar-${formato}`).addEventListener("click", () => {
      const q = consulta();
      if (q) descargar(`/api/export/comparar.${formato}?${q}`, `comparacion.${formato}`, $("comparar-msg"));
    });
  }
  refrescarCorridasComparables();
}
