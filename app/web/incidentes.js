// Incidentes: tabla paginada y detalle (#/incidentes/<id>) con métricas, explicación, tiempos y reenvío.

import { api } from "./api.js";
import { contenidoContexto } from "./contexto.js";
import { contenidoInforme } from "./informe.js";
import { contenidoDecision } from "./recomendaciones.js";
import { $, badge, duracion, el, hora, mensaje, mono, msEntre, vaciar } from "./dom.js";

const POR_PAGINA = 20;
let pagina = 0;
let origen = "real";
let idEnDetalle = null;

const ETAPAS = [
  ["t_ryu", "Detección en Ryu"],
  ["t_recibido", "Recibido por el agente"],
  ["t_explicado", "Explicado"],
  ["t_notificado", "Notificado (primer correo enviado)"],
  ["t_desbloqueado", "Desbloqueado por el operador"],
];

const SEVERIDAD = { critica: "CRÍTICA", alta: "ALTA", media: "MEDIA" };
const CLASE_NOTIFICACION = { enviada: "ok", fallida: "critica", omitida: "info" };

function etiquetaPrueba(incidente) {
  return incidente.origen === "prueba" ? [" ", badge(`PRUEBA ${incidente.caso_id ?? ""}`.trim(), "etiqueta prueba")] : null;
}

function fuente(incidente) {
  if (incidente.fuente_explicacion === "llm") return badge("LLM", "etiqueta");
  if (incidente.fuente_explicacion === "reglas") return badge("reglas", "etiqueta");
  return badge("pendiente", "info");
}

function severidad(valor) {
  return valor ? badge(SEVERIDAD[valor] ?? valor, valor) : badge("—", "info");
}

function estadoIncidente(incidente) {
  return incidente.t_desbloqueado ? badge("desbloqueado", "ok") : badge("abierto", "alta");
}

function estadoNotificacion(estado) {
  return estado ? badge(estado, CLASE_NOTIFICACION[estado] ?? "info") : badge("—", "info");
}

export async function refrescarIncidentes() {
  const contenedor = $("incidentes");
  try {
    const lista = await api(`/api/incidentes?limite=${POR_PAGINA}&offset=${pagina * POR_PAGINA}&origen=${origen}`);
    vaciar($("pag-numero"), pagina + 1);
    $("pag-anterior").disabled = pagina === 0;
    $("pag-siguiente").disabled = lista.length < POR_PAGINA;
    if (!lista.length) {
      vaciar(contenedor, el("p", { class: "mensaje" }, pagina === 0 ? "Sin incidentes." : "Sin incidentes en esta página."));
      return;
    }
    const filas = lista.map((i) =>
      el(
        "tr",
        { class: "clic", onclick: () => { location.hash = `#/incidentes/${i.id}`; } },
        el("td", { class: "mono" }, `#${i.id}`),
        el("td", {}, mono(i.host_origen), etiquetaPrueba(i)),
        el("td", {}, severidad(i.severidad)),
        el("td", {}, badge(i.veredicto ?? "—", "etiqueta")),
        el("td", {}, fuente(i)),
        el("td", { class: "mono" }, i.contador_alertas),
        el("td", {}, estadoNotificacion(i.ultima_notificacion?.estado)),
        el("td", {}, estadoIncidente(i)),
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
  const pasos = ETAPAS.map(([clave, nombre]) => {
    const valor = incidente[clave];
    const delta = valor && anterior ? `+${duracion(msEntre(anterior, valor))}` : "";
    if (valor) anterior = valor;
    return el("li", { class: valor ? "hecho" : "" }, el("span", {}, nombre), el("span", { class: "mono" }, hora(valor)),
      delta ? el("span", { class: "delta" }, delta) : null);
  });
  const total = duracion(msEntre(incidente.t_ryu, incidente.t_notificado));
  return el("div", {}, el("ol", { class: "tiempos" }, pasos), el("p", { class: "mensaje" }, "Ryu → correo: ", mono(total)));
}

function barraConfianza(confianza) {
  if (confianza === null || confianza === undefined) return "—";
  const barra = el("span", { class: "barra" }, el("span", { style: `width: ${Math.round(100 * confianza)}%` }));
  return el("span", { class: "confianza" }, barra, mono(confianza.toFixed(2)));
}

function tablaMetricas(metricas) {
  if (!metricas) return el("p", { class: "mensaje" }, "Sin métricas.");
  const filas = Object.entries(metricas).map(([nombre, valor]) =>
    el("tr", {}, el("td", { class: "mono" }, nombre), el("td", { class: "mono" }, String(valor))));
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
    const boton = el("button", {}, "Reenviar correo");
    boton.addEventListener("click", () => reenviar(boton, salida));
    const notif = i.ultima_notificacion;
    vaciar(
      seccion,
      el("h2", {}, `Incidente #${i.id} · `, mono(i.host_origen), etiquetaPrueba(i),
        el("a", { href: "#/incidentes", class: "empuje", "aria-label": "Cerrar detalle" }, "✕ Cerrar")),
      bloque(
        "Resumen",
        el(
          "dl",
          { class: "detalle-grid" },
          dato("Veredicto (informativo)", badge(i.veredicto ?? "—", "etiqueta")),
          dato("Severidad", severidad(i.severidad)),
          dato("Técnica MITRE", mono(i.tecnica_mitre)),
          dato("Confianza", barraConfianza(i.confianza)),
          dato("Fuente de la explicación", fuente(i)),
          dato("Motivo del respaldo", i.motivo_respaldo ? mono(i.motivo_respaldo) : "—"),
          dato("Estado", estadoIncidente(i)),
          dato("Switch", mono(i.dpid_switch)),
          dato("Variante", i.variante ? badge(i.variante.replace("_", " "), "etiqueta") : "—"),
          dato("Versión del prompt", mono(i.version_prompt)),
        ),
      ),
      bloque("Contexto usado", ...contenidoContexto(i)),
      bloque("Decisión de la política", ...contenidoDecision(i)),
      // Fase 6.8b: con informe, las 4 secciones auditables; sin informe (incidentes previos), la vista anterior
      i.informe ? [bloque("Resumen del informe", el("p", { class: "texto-largo" }, i.explicacion ?? "—"),
        el("h3", {}, "Recomendación al operador"), el("p", { class: "texto-largo" }, i.recomendacion_operador ?? "—")),
      ...contenidoInforme(i)] : [
        bloque(
          `Métricas de la alerta que originó el incidente · alertas agrupadas: ${i.contador_alertas}`,
          tablaMetricas(i.metricas),
          el("p", { class: "mensaje" }, "Umbrales superados: ", mono((i.umbrales_superados ?? []).join(", ") || "ninguno")),
        ),
        bloque(
          "Explicación y evidencias",
          el("p", { class: "texto-largo" }, i.explicacion ?? "Aún sin explicación."),
          el("ul", {}, (i.evidencias ?? []).map((e) => el("li", {}, e))),
          el("h3", {}, "Recomendación al operador"),
          el("p", { class: "texto-largo" }, i.recomendacion_operador ?? "—"),
        )],
      bloque("Tiempos por etapa", tablaTiempos(i)),
      bloque(
        "Notificación y acciones",
        el("p", {}, notif ? [estadoNotificacion(notif.estado), " ", mono(`${notif.intentos} intento(s)`), " · ", hora(notif.ts)] : "Sin notificaciones."),
        notif && (notif.error || notif.codigo_http)
          ? el("p", { class: "mensaje mono" }, `Código HTTP: ${notif.codigo_http ?? "—"} · Error: ${notif.error ?? "—"}`)
          : null,
        boton,
        salida,
      ),
    );
  } catch (error) {
    vaciar(seccion, el("h2", {}, `Incidente #${id}`, el("a", { href: "#/incidentes", class: "empuje" }, "✕ Cerrar")),
      el("p", { class: "mensaje error" }, error.message));
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
  $("filtro-origen").addEventListener("change", (evento) => {
    origen = evento.target.value;
    pagina = 0;
    refrescarIncidentes();
  });
}
