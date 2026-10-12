// Laboratorio: escenarios, corridas (con o sin LLM, variante, repeticiones, correos) y resultados por caso.

import { api } from "./api.js";
import { $, chip, duracion, el, mensaje, vaciar } from "./dom.js";

let escenarios = [];
let corridaMostrada = null;
let cupoRestante = null;

export function corridaActual() {
  return corridaMostrada;
}

function repeticiones() {
  return Number($("lab-repeticiones").value);
}

function actualizarCosto() {
  const total = escenarios.length * repeticiones();
  vaciar($("lab-costo"), `Con LLM: 1 petición por caso y repetición (todos: ${total}) · cupo restante hoy: ${cupoRestante ?? "—"}`);
}

async function actualizarCupo() {
  try { cupoRestante = (await api("/api/llm/cupo")).restantes; } catch { cupoRestante = null; }
  actualizarCosto();
}

function botones(habilitados) {
  document.querySelectorAll("#laboratorio .lanzar").forEach((b) => { b.disabled = !habilitados; });
}

function progreso(actual, total, texto = "") {
  $("lab-barra").style.width = `${total ? (100 * actual) / total : 0}%`;
  vaciar($("lab-progreso"), `${actual}/${total} ${texto}`.trim());
}

async function ejecutar(cuerpo, cantidad) {
  const usarLlm = $("lab-llm").checked;
  const correos = $("lab-correos").checked;
  const costo = cantidad * repeticiones();
  if (usarLlm && !window.confirm(`Esta corrida usará el LLM: costará ${costo} peticiones del cupo diario (quedan ${cupoRestante ?? "?"}). ¿Continuar?`)) return;
  if (correos && !window.confirm("Se enviará un correo real por Apps Script por cada caso. ¿Continuar?")) return;
  try {
    const r = await api("/api/laboratorio/corridas", {
      metodo: "POST",
      cuerpo: {
        ...cuerpo, usar_llm: usarLlm, repeticiones: repeticiones(), enviar_correos: correos, variante: $("lab-variante").value,
        modo_decision: $("lab-modo").value, umbral_confianza: Number($("lab-umbral").value),
      },
    });
    corridaMostrada = r.corrida_id;
    botones(false);
    progreso(0, r.total);
    mensaje($("lab-msg"), `Corrida #${r.corrida_id} en curso…`);
  } catch (error) {
    mensaje($("lab-msg"), `No se inició la corrida: ${error.message}`, "error");
  }
}

async function cargarEscenarios() {
  try {
    escenarios = await api("/api/laboratorio/escenarios");
  } catch (error) {
    vaciar($("lab-escenarios"), el("p", { class: "mensaje error" }, error.message));
    return;
  }
  const filas = escenarios.map((e) =>
    el(
      "tr",
      {},
      el("td", { class: "mono" }, e.id),
      el("td", {}, e.nombre),
      el("td", {}, chip(e.clase)),
      el("td", {}, e.conjunto),
      el("td", { title: e.motivo_no_decidible ?? "Cuenta en la exactitud" }, e.decidible_v2 ? "✓" : "✗ (no cuenta)"),
      el("td", {}, el("button", { class: "lanzar discreto", title: `Ejecutar ${e.id}`, onclick: () => ejecutar({ casos: [e.id] }, 1) }, "▶")),
    ),
  );
  const titulos = ["Caso", "Nombre", "Clase", "Conjunto", "Decidible", ""];
  vaciar($("lab-escenarios"), el("table", {}, el("thead", {}, el("tr", {}, titulos.map((t) => el("th", {}, t)))), el("tbody", {}, filas)));
  actualizarCosto();
}

function textoExactitud(e) {
  return e && e.total ? `${e.aciertos}/${e.total} (${(100 * e.exactitud).toFixed(0)} %)` : "—";
}

function resumenCorrida(r) {
  const lat = r.latencia_explicacion_ms;
  const fuentes = Object.entries(r.fuente_explicacion).map(([f, p]) => `${f} ${p} %`).join(" · ") || "—";
  const agrupacion = r.no_decidibles.filter((n) => n.detalle).map((n) => `${n.caso_id}: ${n.detalle.agrupacion_ok ? "✓" : "✗"}`);
  const lineas = [
    `Exactitud (solo decidibles): ${textoExactitud(r.exactitud_decidibles.global)} · desarrollo ${textoExactitud(r.exactitud_decidibles.desarrollo)} · validación ${textoExactitud(r.exactitud_decidibles.validacion)}`,
    `Fuente de la explicación: ${fuentes} · llamadas al LLM: ${r.cupo_consumido.llamadas_llm}`,
    `Latencia de explicación: media ${duracion(lat.media === null ? null : Math.round(lat.media))} · mediana ${duracion(lat.mediana)} · p95 ${duracion(lat.p95)}`,
    `No decidibles (aparte): ${r.no_decidibles.length}${agrupacion.length ? ` · agrupación ${agrupacion.join(", ")}` : ""}`,
  ];
  if (r.xai) {
    const d = r.xai.dictamenes;
    lineas.push(`Veredicto del LLM (antes del dictamen): ${textoExactitud(r.xai.exactitud_veredicto_llm)} · dictámenes: ${d.agravante} agravante, ${d.atenuante} atenuante · informes con posible alucinación: ${r.xai.posible_alucinacion}`);
  }
  if (r.estabilidad) {
    const estables = Object.entries(r.estabilidad).map(([caso, s]) => `${caso} ${s.coinciden}/${s.de}`);
    lineas.push(`Estabilidad (veredicto más común): ${estables.join(" · ")}`);
  }
  return lineas.map((linea) => el("p", { class: "mensaje" }, linea));
}

export async function mostrarCorrida(id) {
  let c;
  try {
    c = await api(`/api/laboratorio/corridas/${id}`);
  } catch (error) {
    mensaje($("lab-msg"), error.message, "error");
    return;
  }
  corridaMostrada = id;
  const opciones = `${c.usar_llm ? "con LLM" : "sin LLM"} · ${c.variante.replace("_", " ")} · ${c.modo_decision.replace("_", " ")} (umbral ${c.umbral_confianza}, simulado) · ${c.repeticiones} rep. · correos ${c.enviar_correos ? "sí" : "no"}`;
  vaciar($("lab-corrida"), `#${c.id} · ${c.estado} · ${opciones}`);
  vaciar($("lab-resumen"), c.resumen ? resumenCorrida(c.resumen) : el("p", { class: "mensaje" }, c.error ?? "Sin resumen todavía."));
  const filas = c.resultados.map((r) =>
    el(
      "tr",
      {},
      el("td", { class: "mono" }, r.caso_id, r.repeticion > 1 || c.repeticiones > 1 ? ` #${r.repeticion}` : ""),
      el("td", {}, r.veredicto_esperado),
      el("td", {}, r.veredicto_obtenido ?? "—"),
      el("td", { class: r.acierto ? "texto-ok" : "texto-mal", title: r.decidible ? "" : "No decidible: no cuenta en la exactitud" }, `${r.acierto ? "✓" : "✗"}${r.decidible ? "" : " *"}`),
      el("td", {}, r.fuente === "llm" ? chip("LLM", "llm") : chip(r.motivo_respaldo ?? r.fuente ?? "—")),
      el("td", { class: "mono", title: r.decision_politica ? `propuesta: ${r.accion_propuesta} · política: ${r.decision_politica}` : "" }, r.accion_final ?? "—"),
      el("td", { class: "mono", title: r.dictamen_historial ? `dictamen ${r.dictamen_historial}` : "" },
        `${r.veredicto_llm ?? "—"}${r.dictamen_historial ? ` → ${r.dictamen_historial}` : ""}${r.verificacion === "posible_alucinacion" ? " ⚠" : ""}`),
      el("td", { class: "mono" }, duracion(r.latencia_explicacion_ms)),
      el("td", { class: "mono" }, r.incidente_id ? el("a", { href: `#/incidentes/${r.incidente_id}` }, `#${r.incidente_id}`) : "—"),
    ),
  );
  const titulos = ["Caso", "Esperado", "Obtenido", "Acierto", "Fuente", "Acción final", "LLM / dictamen", "Latencia", "Incidente"];
  vaciar(
    $("lab-resultados"),
    el("table", {}, el("thead", {}, el("tr", {}, titulos.map((t) => el("th", {}, t)))), el("tbody", {}, filas)),
    el("p", { class: "mensaje" }, "* no decidible en la v2 (se reporta aparte)"),
  );
}

export function alProgreso(datos) {
  corridaMostrada = datos.corrida_id;
  botones(false);
  progreso(datos.actual, datos.total, `· ${datos.caso_id}`);
}

export function alTerminar(datos) {
  botones(true);
  const texto = datos.estado === "terminada" ? `Corrida #${datos.corrida_id} terminada.` : `Corrida #${datos.corrida_id} falló.`;
  mensaje($("lab-msg"), texto, datos.estado === "terminada" ? "exito" : "error");
  mostrarCorrida(datos.corrida_id);
  actualizarCupo();
}

export async function iniciarLaboratorio() {
  for (const conjunto of ["desarrollo", "validacion", "todos"]) {
    $(`lab-${conjunto}`).addEventListener("click", () => {
      const cantidad = escenarios.filter((e) => conjunto === "todos" || e.conjunto === conjunto).length;
      ejecutar({ conjunto }, cantidad);
    });
  }
  $("lab-repeticiones").addEventListener("change", actualizarCosto);
  await cargarEscenarios();
  actualizarCupo();
  try {
    const corridas = await api("/api/laboratorio/corridas");
    if (corridas.length) {
      mostrarCorrida(corridas[0].id);
      if (corridas[0].estado === "en_curso") botones(false);
    }
  } catch { /* sin corridas previas */ }
}
