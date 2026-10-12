// Recomendaciones (C7): bandeja con contador de pendientes, aprobar (confirmación) o rechazar (motivo 5–200),
// y el bloque "Decisión de la política" del detalle del incidente. Todo el texto entra como texto.

import { api } from "./api.js";
import { $, badge, el, hora, mensaje, mono, vaciar } from "./dom.js";

const ACCION = { pasar_a_cuarentena: "Pasar a cuarentena", escalar_a_drop: "Volver a bloqueo total", mantener: "Mantener" };
const CLASE_ESTADO = { pendiente: "alta", ejecutada: "ok", aprobada: "ok", rechazada: "info", expirada: "info", fallida: "critica" };
const DECISION = { ejecutar: ["ejecutada por la política", "ok"], recomendar: ["recomendación al operador", "alta"], descartar: ["sin acción", "info"] };

function tabla(titulos, filas) {
  return el("table", {}, el("thead", {}, el("tr", {}, titulos.map((t) => el("th", {}, t)))), el("tbody", {}, filas));
}

async function aprobar(r) {
  if (!window.confirm(`¿Aprobar "${ACCION[r.accion]}" sobre ${r.host}?\n\nSe ejecutará en Ryu ahora. El host nunca queda liberado por completo.`)) return;
  try {
    const resultado = await api(`/api/recomendaciones/${r.id}/aprobar`, { metodo: "POST" });
    mensaje($("rec-msg"), `Recomendación #${r.id}: ${resultado.estado}${resultado.motivo_resolucion ? ` · ${resultado.motivo_resolucion}` : ""}`,
      resultado.estado === "ejecutada" ? "exito" : "error");
  } catch (error) {
    mensaje($("rec-msg"), `No se aprobó: ${error.message}`, "error");
  }
  refrescarRecomendaciones();
}

async function rechazar(r) {
  const motivo = (window.prompt(`Motivo para rechazar "${ACCION[r.accion]}" sobre ${r.host} (5 a 200 caracteres):`) ?? "").trim();
  if (!motivo) return;
  if (motivo.length < 5 || motivo.length > 200) {
    mensaje($("rec-msg"), "El motivo debe tener entre 5 y 200 caracteres.", "error");
    return;
  }
  try {
    await api(`/api/recomendaciones/${r.id}/rechazar`, { metodo: "POST", cuerpo: { motivo } });
    mensaje($("rec-msg"), `Recomendación #${r.id} rechazada.`, "exito");
  } catch (error) {
    mensaje($("rec-msg"), `No se rechazó: ${error.message}`, "error");
  }
  refrescarRecomendaciones();
}

function fila(r) {
  const pendiente = r.estado === "pendiente";
  return el(
    "tr",
    {},
    el("td", { class: "mono" }, `#${r.id}`),
    el("td", {}, mono(r.host), r.incidente_id ? [" ", el("a", { href: `#/incidentes/${r.incidente_id}` }, `#${r.incidente_id}`)] : null),
    el("td", {}, ACCION[r.accion] ?? r.accion),
    el("td", { class: "mono" }, r.confianza === null ? "—" : r.confianza.toFixed(2)),
    el("td", {}, r.justificacion ?? "—"),
    el("td", {}, (r.motivos_politica ?? []).join(" · ") || "—"),
    el("td", {}, badge(r.estado, CLASE_ESTADO[r.estado] ?? "info"), r.motivo_resolucion ? el("small", { class: "secundario" }, ` ${r.motivo_resolucion}`) : null),
    el("td", { class: "mono" }, pendiente ? `vence ${hora(r.t_expira)}` : hora(r.t_resuelta)),
    el("td", {}, pendiente ? [el("button", { class: "primario", onclick: () => aprobar(r) }, "Aprobar"), " ",
      el("button", { class: "discreto", onclick: () => rechazar(r) }, "Rechazar")] : null),
  );
}

export async function refrescarRecomendaciones() {
  let lista;
  try {
    lista = await api("/api/recomendaciones?limite=100");
  } catch (error) {
    vaciar($("rec-lista"), el("p", { class: "mensaje error" }, error.message));
    return;
  }
  const pendientes = lista.filter((r) => r.estado === "pendiente").length;
  vaciar($("contador-recomendaciones"), pendientes ? `(${pendientes})` : "");
  vaciar($("rec-lista"), lista.length
    ? tabla(["#", "Host / incidente", "Acción", "Confianza", "Justificación", "Motivos de la política", "Estado", "Vence / resuelta", ""], lista.map(fila))
    : el("p", { class: "mensaje" }, "Sin recomendaciones."));
}

export function iniciarRecomendaciones() {
  vaciar(
    $("recomendaciones"),
    el("header", { class: "panel-cabecera" }, el("h2", {}, "Recomendaciones"),
      el("button", { class: "discreto empuje", onclick: refrescarRecomendaciones }, "↻ Actualizar")),
    el("p", { class: "ayuda" }, "Acciones que la política no ejecutó sola. Aprobar las ejecuta en Ryu; nunca desbloquean un host."),
    el("p", { id: "rec-msg", class: "mensaje" }),
    el("div", { id: "rec-lista", class: "tabla-contenedor" }),
  );
  refrescarRecomendaciones();
}

// Detalle del incidente: propuesta, decisión de la política con sus motivos y acciones ejecutadas.
export function contenidoDecision(i) {
  if (!i.accion_propuesta) return [el("p", { class: "mensaje" }, "Sin decisión todavía.")];
  const [texto, clase] = DECISION[i.decision_politica] ?? ["—", "info"];
  const acciones = (i.acciones ?? []).map((a) =>
    el("li", {}, mono(hora(a.ts)), ` ${ACCION[a.accion] ?? a.accion} · ${a.origen} · `,
      badge(a.resultado, a.resultado === "fallida" ? "critica" : a.resultado === "simulada" ? "info" : "ok"),
      a.error ? ` · ${a.error}` : ""));
  return [
    el("p", {}, "Acción propuesta: ", badge(ACCION[i.accion_propuesta] ?? i.accion_propuesta, "etiqueta"), " → ", badge(texto, clase),
      " · acción final: ", mono(i.accion_final ?? "—")),
    el("p", { class: "texto-largo" }, i.justificacion_accion ?? "Sin justificación."),
    (i.motivos_politica ?? []).length ? el("ul", {}, i.motivos_politica.map((m) => el("li", {}, m))) : null,
    el("h4", {}, "Acciones sobre Ryu"),
    acciones.length ? el("ul", {}, acciones) : el("p", { class: "mensaje" }, "Ninguna."),
  ];
}
