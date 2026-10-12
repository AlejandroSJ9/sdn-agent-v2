// Detalle del incidente: "Contexto usado" (C6) legible. Historial en tabla con las clasificaciones del
// operador destacadas (son lo único verificado) y actividad reciente de otros hosts. Todo entra como texto.

import { badge, el, mono } from "./dom.js";

const CLASIFICACION = {
  falso_positivo: ["Falso positivo", "ok"],
  amenaza_contenida: ["Amenaza contenida", "critica"],
  otro: ["Otro", "alta"],
};

function hace(minutos) {
  if (minutos < 60) return `hace ${minutos} min`;
  if (minutos < 2880) return `hace ${Math.round(minutos / 60)} h`;
  return `hace ${Math.round(minutos / 1440)} días`;
}

function clasificacion(previo) {
  const [texto, clase] = CLASIFICACION[previo.clasificacion_operador] ?? [];
  return texto ? badge(`Operador: ${texto}`, clase) : "—";
}

function tabla(titulos, filas) {
  return el("table", {}, el("thead", {}, el("tr", {}, titulos.map((t) => el("th", {}, t)))), el("tbody", {}, filas));
}

function historial(previos) {
  if (!previos.length) return el("p", { class: "mensaje" }, "Sin incidentes previos del host en la ventana.");
  const filas = previos.map((p) =>
    el(
      "tr",
      { class: p.clasificacion_operador ? "verificado" : "" },
      el("td", { class: "mono" }, hace(p.minutos_antes)),
      el("td", {}, badge(p.veredicto_agente ?? "—", "etiqueta"), " ", el("small", { class: "secundario" }, "no verificado")),
      el("td", { class: "mono" }, (p.umbrales_superados ?? []).join(", ") || "—"),
      el("td", {}, clasificacion(p)),
      el("td", {}, p.motivo_operador ?? "—"),
    ),
  );
  return tabla(["Cuándo", "Veredicto del agente", "Umbrales", "Clasificación del operador", "Motivo"], filas);
}

function actividad(otros) {
  if (!otros.length) return el("p", { class: "mensaje" }, "Sin actividad de otros hosts en la ventana.");
  const filas = otros.map((a) =>
    el("tr", {}, el("td", {}, mono(a.host)), el("td", { class: "mono" }, hace(a.minutos_antes)),
      el("td", {}, badge(a.veredicto ?? "—", "etiqueta")), el("td", { class: "mono" }, (a.umbrales_superados ?? []).join(", ") || "—")),
  );
  return tabla(["Host", "Cuándo", "Veredicto", "Umbrales"], filas);
}

export function contenidoContexto(incidente) {
  const c = incidente.contexto_usado;
  if (incidente.variante !== "con_contexto" || !c) {
    return [el("p", { class: "mensaje" }, "No utilizado: la explicación se generó sin contexto.")];
  }
  const r = c.resumen ?? {};
  return [
    el("p", { class: "mensaje" },
      `${r.total_previos ?? 0} incidentes previos del host · ${r.falsos_positivos_operador ?? 0} falsos positivos y `,
      `${r.amenazas_contenidas_operador ?? 0} amenazas contenidas verificados por el operador · `,
      r.confirmado_ultimos_60_min ? "hubo un confirmado en los últimos 60 min" : "sin confirmados en los últimos 60 min"),
    el("h4", {}, "Historial del host"),
    historial(c.historial_host ?? []),
    el("h4", {}, "Actividad reciente en otros hosts"),
    actividad(c.actividad_red ?? []),
  ];
}
