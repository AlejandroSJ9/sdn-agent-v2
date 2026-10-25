// Detalle del incidente a página completa (#/incidentes/<id>): cabecera fija con veredicto, dictamen, acción y
// desbloqueo; resumen; las 4 dimensiones XAI en grilla; contexto y JSON crudo colapsados. Todo entra como texto.
// Fase D3 (informe 1.2): primero la explicación simple en 4 bloques y, plegado en "Detalle técnico", el resumen y la
// grilla XAI sin cambios. Un incidente sin explicación simple se muestra como antes.

import { api } from "./api.js";
import { abrirModal } from "./bloqueados.js";
import { contenidoContexto } from "./contexto.js";
import { $, badge, badgeDictamen, badgeVeredicto, el, hora, mensaje, mono, vaciar } from "./dom.js";
import { lineaTiempo, resumenEscalera, tarjeta, tarjetasInforme } from "./informe.js";
import { contenidoDecision } from "./recomendaciones.js";

const SEVERIDAD = { critica: "CRÍTICA", alta: "ALTA", media: "MEDIA" };
const CLASE_NOTIFICACION = { enviada: "ok", fallida: "critica", omitida: "info" };

let idEnDetalle = null;
let volverA = "#/incidentes";

function campo(titulo, ...valor) {
  return el("div", { class: "campo" }, el("span", { class: "campo-titulo" }, titulo), el("span", {}, ...valor));
}

function estadoBloqueo(i, bloqueados) {
  if (bloqueados === null) return i.t_desbloqueado ? badge("desbloqueado", "ok") : badge("Ryu sin datos", "info");
  return bloqueados.some((h) => h.ip === i.host_origen) ? badge("BLOQUEADO", "critica") : badge("sin bloqueo en Ryu", "ok");
}

// Aviso de la fuente de la explicación: el respaldo por reglas debe verse con su motivo (texto del backend).
function avisoFuente(i) {
  if (i.fuente_explicacion === "reglas") {
    return el("p", { class: "aviso-franja" }, `Explicación generada por reglas (respaldo): ${i.motivo_respaldo_legible}`);
  }
  if (i.fuente_explicacion === "llm") {
    const modelo = i.informe?.trazabilidad_y_accion?.modelo;
    return el("p", { class: "secundario" }, `Explicación generada por LLM${modelo ? ` (${modelo})` : ""}`);
  }
  return null;
}

// Fase 9b: si la reversión a DROP total también falló, el operador debe verlo de inmediato.
const ESTADO_DESCONOCIDO = "Ryu no confirmó el DROP total";

function avisoEstadoDesconocido(i) {
  const motivo = (i.motivos_politica ?? []).find((m) => m.includes(ESTADO_DESCONOCIDO));
  return motivo ? el("p", { class: "aviso-franja" }, `¡ATENCIÓN! ${ESTADO_DESCONOCIDO}: verifique el host en Ryu. (${motivo})`) : null;
}

function cabecera(i, bloqueados, abiertos) {
  const bloqueado = bloqueados !== null && bloqueados.some((h) => h.ip === i.host_origen);
  const confianza = i.confianza === null || i.confianza === undefined ? "—" : i.confianza.toFixed(2);
  const traza = i.informe?.trazabilidad_y_accion;
  return el("header", { class: "cabecera-detalle" },
    el("div", { class: "fila" },
      el("a", { href: volverA, class: "boton-enlace" }, "← Volver"),
      el("h2", {}, `Incidente #${i.id} · `, mono(i.host_origen)),
      i.origen === "prueba" ? badge(`PRUEBA ${i.caso_id ?? ""}`.trim(), "etiqueta prueba") : null,
      el("span", { class: "empuje" }, bloqueado
        ? el("button", { class: "peligro", onclick: () => abrirModal(i.host_origen) }, "Desbloquear host…") : null)),
    el("div", { class: "campos" },
      campo("Veredicto final", badgeVeredicto(i.veredicto)),
      campo("Severidad", i.severidad ? badge(SEVERIDAD[i.severidad] ?? i.severidad, i.severidad) : badge("—", "info")),
      campo("Dictamen", badgeDictamen(i.dictamen_historial)),
      ...(traza?.escalera ? [campo("Acción recomendada", resumenEscalera(traza, abiertos.has("escalera")))]  // Fase 9a
        : [campo("Acción", mono(i.accion_propuesta ?? "—"), " → ", mono(i.accion_final ?? "—")), campo("Confianza", mono(confianza))]),
      campo("Bloqueo", estadoBloqueo(i, bloqueados)),
      campo("Switch", mono(i.dpid_switch ?? "—"))),
    avisoEstadoDesconocido(i),
    avisoFuente(i));
}

function resumen(i) {
  const fuente = i.fuente_explicacion ? `Explicación por ${i.fuente_explicacion === "llm" ? "LLM" : "reglas"}` : "Aún sin explicación";
  return el("section", { class: "panel tarjeta destacada" },
    el("h2", {}, "Resumen ", el("small", {}, `· ${fuente}`)),
    el("p", { class: "texto-largo" }, i.explicacion ?? "Aún sin explicación."),
    el("h3", {}, "Recomendación al operador"),
    el("p", { class: "texto-largo" }, i.recomendacion_operador ?? "—"));
}

const BLOQUES_SIMPLES = [["que_paso", "Qué pasó"], ["por_que", "Por qué creemos que es esto"],
  ["que_recomendamos", "Qué recomendamos y por qué"], ["podria_equivocarnos", "Qué podría hacer que estemos equivocados"]];

function notaFuentes(fuentes) {
  const valores = Object.values(fuentes ?? {});
  if (valores.length && valores.every((f) => f === "llm")) return "redactado por el modelo de lenguaje";
  if (valores.every((f) => f === "reglas")) return "redactado por reglas automáticas";
  return "redactado en parte por el modelo de lenguaje y en parte por reglas automáticas";
}

function explicacionSimple(s) {
  return el("section", { class: "panel tarjeta destacada" },
    el("h2", {}, "Explicación ", el("small", {}, `· ${notaFuentes(s.fuentes)}`)),
    BLOQUES_SIMPLES.map(([clave, titulo]) => [el("h3", {}, titulo), Array.isArray(s[clave])
      ? el("ul", {}, s[clave].map((frase) => el("li", {}, frase)))
      : el("p", { class: "texto-largo" }, s[clave])]));
}

async function reenviar(boton, salida) {
  boton.disabled = true;
  mensaje(salida, "Enviando correo… (puede tardar si Apps Script reintenta)");
  try {
    const r = await api(`/api/incidentes/${idEnDetalle}/reenviar`, { metodo: "POST" });
    const detalle = r.error ? ` · ${r.error}` : "";
    mensaje(salida, `Resultado: ${r.estado} en ${r.intentos} intento(s)${detalle}`, r.estado === "enviada" ? "exito" : "error");
    mostrarDetalle(idEnDetalle);
  } catch (error) {
    mensaje(salida, `No se pudo reenviar: ${error.message}`, "error");
  } finally {
    boton.disabled = false;
  }
}

// Decisión de la política, acciones sobre Ryu y notificación: van al final de la tarjeta 4.
function extrasTrazabilidad(i) {
  const notif = i.ultima_notificacion;
  const salida = el("p", { class: "mensaje" });
  const boton = el("button", {}, "Reenviar correo");
  boton.addEventListener("click", () => reenviar(boton, salida));
  return [
    el("h3", {}, "Decisión de la política"), ...contenidoDecision(i),
    el("h3", {}, "Notificación"),
    el("p", {}, notif ? [badge(notif.estado, CLASE_NOTIFICACION[notif.estado] ?? "info"), " ", mono(`${notif.intentos} intento(s)`), " · ", hora(notif.ts)] : "Sin notificaciones."),
    notif && (notif.error || notif.codigo_http) ? el("p", { class: "mensaje mono" }, `Código HTTP: ${notif.codigo_http ?? "—"} · Error: ${notif.error ?? "—"}`) : null,
    el("div", { class: "fila" }, boton), salida,
  ];
}

// Incidentes anteriores al informe auditable: misma grilla con lo que haya.
function tarjetasPrevias(i) {
  const metricas = Object.entries(i.metricas ?? {}).map(([nombre, v]) =>
    el("tr", {}, el("td", { class: "mono" }, nombre), el("td", { class: "mono" }, String(v))));
  return [
    tarjeta("1 · Evidencia objetiva",
      metricas.length ? el("table", {}, el("tbody", {}, metricas)) : el("p", { class: "mensaje" }, "Sin métricas."),
      el("p", { class: "mensaje" }, "Umbrales superados: ", mono((i.umbrales_superados ?? []).join(", ") || "ninguno"))),
    tarjeta("2 · Mapeo MITRE ATT&CK", el("p", {}, "Técnica: ", mono(i.tecnica_mitre))),
    tarjeta("3 · Razonamiento", el("ul", {}, (i.evidencias ?? []).map((e) => el("li", {}, e))),
      el("p", { class: "mensaje" }, "Este incidente es anterior al informe auditable.")),
    tarjeta("4 · Trazabilidad y acción", el("p", {}, "Versión del prompt: ", mono(i.version_prompt)),
      el("h3", {}, "Línea de tiempo"), lineaTiempo(i), ...extrasTrazabilidad(i)),
  ];
}

function plegable(clave, titulo, abiertos, ...contenido) {
  return el("details", { class: "panel plegable", "data-clave": clave, open: abiertos.has(clave) }, el("summary", {}, titulo), ...contenido);
}

export async function mostrarDetalle(id, desde = null) {
  const pagina = $("detalle");
  if (desde !== null) volverA = desde;
  if (idEnDetalle !== id) vaciar(pagina, el("p", { class: "mensaje" }, `Cargando incidente #${id}…`));
  idEnDetalle = id;
  try {
    const [i, bloqueados] = await Promise.all([api(`/api/incidentes/${id}`), api("/api/ryu/bloqueados").catch(() => null)]);
    if (idEnDetalle !== id) return;
    // Un refresco en vivo no debe cerrar lo que el operador desplegó.
    const abiertos = new Set([...pagina.querySelectorAll("details[open]")].map((d) => d.dataset.clave));
    const grilla = el("div", { class: "grilla-xai" }, i.informe ? tarjetasInforme(i, extrasTrazabilidad(i)) : tarjetasPrevias(i));
    const simple = i.informe?.explicacion_simple;
    vaciar(pagina,
      cabecera(i, bloqueados, abiertos),
      simple ? [explicacionSimple(simple), plegable("tecnico", "Detalle técnico", abiertos, resumen(i), grilla)] : [resumen(i), grilla],
      plegable("contexto", "Contexto usado (historial del host y actividad de la red)", abiertos, ...contenidoContexto(i)),
      plegable("json", "JSON crudo del informe", abiertos,
        el("pre", { class: "json crudo" }, i.informe ? JSON.stringify(i.informe, null, 2) : "Sin informe auditable.")));
  } catch (error) {
    if (idEnDetalle !== id) return;
    vaciar(pagina, el("header", { class: "cabecera-detalle" }, el("div", { class: "fila" },
      el("a", { href: volverA, class: "boton-enlace" }, "← Volver"), el("h2", {}, `Incidente #${id}`))),
    el("p", { class: "mensaje error" }, error.message));
  }
}

export function ocultarDetalle() {
  idEnDetalle = null;
}

export function detalleAbierto() {
  return idEnDetalle;
}
