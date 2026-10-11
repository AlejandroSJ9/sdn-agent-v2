// Incidentes: tabla paginada y detalle (#/incidentes/<id>) con métricas, explicación, tiempos y reenvío.

import { api } from "./api.js";
import { $, chip, duracion, el, hora, mensaje, msEntre, vaciar } from "./dom.js";

const POR_PAGINA = 20;
let pagina = 0;
let idEnDetalle = null;

const ETAPAS = [
  ["t_ryu", "Detección en Ryu"],
  ["t_recibido", "Recibido por el agente"],
  ["t_explicado", "Explicado"],
  ["t_notificado", "Notificado (primer correo enviado)"],
  ["t_desbloqueado", "Desbloqueado por el operador"],
];

function fuente(incidente) {
  if (incidente.fuente_explicacion === "llm") return chip("LLM", "llm");
  if (incidente.fuente_explicacion === "reglas") return chip("reglas");
  return chip("pendiente");
}

export async function refrescarIncidentes() {
  const contenedor = $("incidentes");
  try {
    const lista = await api(`/api/incidentes?limite=${POR_PAGINA}&offset=${pagina * POR_PAGINA}`);
    vaciar($("pag-numero"), pagina + 1);
    $("pag-anterior").disabled = pagina === 0;
    $("pag-siguiente").disabled = lista.length < POR_PAGINA;
    if (!lista.length) {
      vaciar(contenedor, el("p", { class: "mensaje" }, "Sin incidentes en esta página."));
      return;
    }
    const filas = lista.map((i) =>
      el(
        "tr",
        { class: "clic", onclick: () => { location.hash = `#/incidentes/${i.id}`; } },
        el("td", {}, `#${i.id}`),
        el("td", {}, i.host_origen),
        el("td", {}, chip(i.severidad)),
        el("td", {}, i.veredicto ?? "—"),
        el("td", {}, fuente(i)),
        el("td", {}, i.contador_alertas),
        el("td", {}, chip(i.ultima_notificacion?.estado ?? "—")),
        el("td", {}, i.t_desbloqueado ? "desbloqueado" : "abierto"),
      ),
    );
    const titulos = ["#", "Host", "Severidad", "Veredicto", "Fuente", "Alertas", "Correo", "Estado"];
    vaciar(contenedor, el("table", {}, el("thead", {}, el("tr", {}, titulos.map((t) => el("th", {}, t)))), el("tbody", {}, filas)));
  } catch (error) {
    vaciar(contenedor, el("p", { class: "mensaje error" }, `No se pudieron leer los incidentes: ${error.message}`));
  }
}

function dato(titulo, valor) {
  return [el("dt", {}, titulo), el("dd", {}, valor ?? "—")];
}

function bloque(titulo, ...contenido) {
  return el("div", { class: "bloque" }, el("h3", {}, titulo), ...contenido);
}

function tablaTiempos(incidente) {
  let anterior = null;
  const filas = ETAPAS.map(([clave, nombre]) => {
    const valor = incidente[clave];
    const delta = valor && anterior ? `+${duracion(msEntre(anterior, valor))}` : "";
    if (valor) anterior = valor;
    return el("tr", {}, el("td", {}, nombre), el("td", {}, hora(valor)), el("td", {}, delta));
  });
  const total = duracion(msEntre(incidente.t_ryu, incidente.t_notificado));
  return el("div", {}, el("table", {}, el("tbody", {}, filas)), el("p", { class: "mensaje" }, `Ryu → correo: ${total}`));
}

function tablaMetricas(metricas) {
  if (!metricas) return el("p", { class: "mensaje" }, "Sin métricas.");
  const filas = Object.entries(metricas).map(([nombre, valor]) => el("tr", {}, el("td", {}, nombre), el("td", {}, String(valor))));
  return el("table", {}, el("tbody", {}, filas));
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

export async function mostrarDetalle(id) {
  const seccion = $("detalle");
  idEnDetalle = id;
  seccion.hidden = false;
  try {
    const i = await api(`/api/incidentes/${id}`);
    if (idEnDetalle !== id) return;
    const salida = el("p", { class: "mensaje" });
    const boton = el("button", { class: "primario" }, "Reenviar correo");
    boton.addEventListener("click", () => reenviar(boton, salida));
    const notif = i.ultima_notificacion;
    vaciar(
      seccion,
      el("h2", {}, `Detalle del incidente #${i.id} · ${i.host_origen}`, el("a", { href: "#/", class: "discreto" }, "cerrar")),
      el(
        "dl",
        { class: "detalle-grid" },
        dato("Veredicto (informativo)", i.veredicto),
        dato("Severidad", chip(i.severidad)),
        dato("Técnica MITRE", i.tecnica_mitre),
        dato("Confianza", i.confianza === null ? "—" : i.confianza.toFixed(2)),
        dato("Fuente de la explicación", fuente(i)),
        dato("Motivo del respaldo", i.motivo_respaldo),
        dato("Estado", i.t_desbloqueado ? "desbloqueado" : "abierto (bloqueado por Ryu)"),
        dato("Switch", i.dpid_switch),
      ),
      bloque(
        `Métricas de la alerta que originó el incidente · alertas agrupadas: ${i.contador_alertas}`,
        tablaMetricas(i.metricas),
        el("p", {}, "Umbrales superados: ", (i.umbrales_superados ?? []).join(", ") || "ninguno"),
      ),
      bloque("Explicación", el("p", { class: "texto-largo" }, i.explicacion ?? "Aún sin explicación.")),
      bloque("Evidencias", el("ul", {}, (i.evidencias ?? []).map((e) => el("li", {}, e)))),
      bloque("Recomendación al operador", el("p", { class: "texto-largo" }, i.recomendacion_operador ?? "—")),
      bloque("Tiempos por etapa", tablaTiempos(i)),
      bloque(
        "Última notificación",
        el("p", {}, notif ? [chip(notif.estado), ` ${notif.intentos} intento(s) · `, hora(notif.ts)] : "Sin notificaciones."),
        boton,
        salida,
      ),
    );
    seccion.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    vaciar(seccion, el("h2", {}, `Incidente #${id}`), el("p", { class: "mensaje error" }, error.message));
  }
}

export function ocultarDetalle() {
  idEnDetalle = null;
  $("detalle").hidden = true;
}

export function detalleAbierto() {
  return idEnDetalle;
}

export function iniciarIncidentes() {
  $("pag-anterior").addEventListener("click", () => { pagina = Math.max(0, pagina - 1); refrescarIncidentes(); });
  $("pag-siguiente").addEventListener("click", () => { pagina += 1; refrescarIncidentes(); });
}
