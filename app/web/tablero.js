// Tablero principal: KPIs de hoy (/api/resumen), "Requiere atención" (hosts bloqueados), actividad reciente
// y mini-gráfico por hora. Lo refresca main.js con cada evento del WebSocket. Todo el texto entra como texto.

import { api } from "./api.js";
import { $, badge, badgeDictamen, badgeVeredicto, duracion, el, hora, mono, vaciar } from "./dom.js";
import { dibujarPorHora } from "./grafico.js";
import { etiquetaAccion } from "./recomendaciones.js";

const RANGO_SEVERIDAD = { critica: 0, alta: 1, media: 2 };
const SEVERIDAD = { critica: "CRÍTICA", alta: "ALTA", media: "MEDIA" };

function tabla(titulos, filas) {
  return el("table", {}, el("thead", {}, el("tr", {}, titulos.map((t) => el("th", {}, t)))), el("tbody", {}, filas));
}

function irA(id) {
  return () => { location.hash = `#/incidentes/${id}`; };
}

function enlace(id) {
  return el("a", { href: `#/incidentes/${id}` }, `#${id}`);
}

function hace(iso) {
  const minutos = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (Number.isNaN(minutos)) return "—";
  if (minutos < 60) return `hace ${minutos} min`;
  if (minutos < 2880) return `hace ${Math.round(minutos / 60)} h`;
  return `hace ${Math.round(minutos / 1440)} días`;
}

function pintarLlm(llm) {
  const nodo = $("res-llm");
  vaciar(nodo, llm.habilitado ? "LLM habilitado" : "LLM apagado (reglas)");
  nodo.className = `kpi-texto ${llm.habilitado ? "txt-ok" : "secundario"}`;
  vaciar($("res-modelo"), llm.modelo || "—");
  $("res-modelo").title = llm.modelo || "";
  vaciar($("res-cupo"), `${llm.usadas}/${llm.cupo}`);
  $("cupo-barra").style.width = `${Math.min(100, (100 * llm.usadas) / Math.max(llm.cupo, 1))}%`;
}

export async function refrescarResumen() {
  try {
    const r = await api("/api/resumen");
    vaciar($("res-abiertos"), r.incidentes_abiertos);
    vaciar($("res-hoy"), r.incidentes_hoy);
    vaciar($("res-fallidas"), r.notificaciones_fallidas_hoy);
    vaciar($("res-confirmados"), r.veredictos_hoy.confirmado);
    vaciar($("res-indeterminados"), r.veredictos_hoy.indeterminado);
    vaciar($("res-falsos"), r.veredictos_hoy.falso_positivo);
    vaciar($("res-recomendaciones"), r.recomendaciones_pendientes);
    const ms = r.tiempo_medio_explicacion_ms;
    vaciar($("res-tiempo"), ms === null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
    $("res-tiempo").title = duracion(ms);
    pintarLlm(r.llm);
    dibujarPorHora($("grafico-horas"), r.incidentes_por_hora);
    const ultimo = r.ultimo_incidente_id;
    vaciar($("res-ultimo"), ultimo ? el("a", { href: `#/incidentes/${ultimo}` }, `#${ultimo} →`) : "—");
    return r;
  } catch {
    return null;
  }
}

function fuente(i) {
  if (i.fuente_explicacion === "llm") return badge("LLM", "etiqueta");
  if (i.fuente_explicacion !== "reglas") return badge("—", "info");
  const marca = badge("Reglas", "etiqueta");
  marca.title = `Respaldo por reglas: ${i.motivo_respaldo_legible}`;
  return marca;
}

function puntaje(i) {
  const e = i.informe?.evidencia_objetiva;
  if (!e) return "—";
  const umbrales = e.umbrales_superados_ryu.join(", ") || "ninguno";
  return e.puntaje_total === null ? umbrales : `${e.puntaje_total}/${e.puntaje_maximo} · ${umbrales}`;
}

export async function refrescarActividad() {
  const contenedor = $("actividad");
  try {
    const lista = await api("/api/incidentes?limite=10&offset=0&origen=real");
    if (!lista.length) {
      vaciar(contenedor, el("p", { class: "mensaje" }, "Sin incidentes todavía."));
      return;
    }
    const filas = lista.map((i) =>
      el("tr", { class: "clic", onclick: irA(i.id) },
        el("td", { class: "mono" }, hora(i.t_recibido)),
        el("td", {}, mono(i.host_origen), " ", el("small", {}, enlace(i.id))),
        el("td", { class: "mono" }, puntaje(i)),
        el("td", {}, badgeVeredicto(i.veredicto)),
        el("td", {}, fuente(i)),
        el("td", {}, badgeDictamen(i.dictamen_historial)),
        el("td", {}, etiquetaAccion(i.accion_final ?? i.accion_propuesta))));
    vaciar(contenedor, tabla(["Hora", "Host", "Puntaje · umbrales", "Veredicto", "Fuente", "Dictamen", "Acción"], filas));
  } catch (error) {
    vaciar(contenedor, el("p", { class: "mensaje error" }, `No se pudo leer la actividad: ${error.message}`));
  }
}

// Recibe la lista de /api/ryu/bloqueados (la pide bloqueados.js) y la ordena por severidad y antigüedad.
export async function pintarAtencion(bloqueados) {
  const contenedor = $("atencion");
  if (bloqueados === null) {
    vaciar(contenedor, el("p", { class: "mensaje error" }, "Ryu no disponible: no se puede saber qué hosts están bloqueados."));
    return;
  }
  if (!bloqueados.length) {
    vaciar(contenedor, el("p", { class: "mensaje" }, "Nada pendiente: ningún host bloqueado."));
    return;
  }
  const detalles = await Promise.all(bloqueados.map((h) =>
    (h.incidente_id ? api(`/api/incidentes/${h.incidente_id}`).catch(() => null) : Promise.resolve(null))));
  const filas = bloqueados.map((host, k) => ({ host, i: detalles[k] }))
    .sort((a, b) => (RANGO_SEVERIDAD[a.i?.severidad] ?? 3) - (RANGO_SEVERIDAD[b.i?.severidad] ?? 3)
      || new Date(a.host.desde ?? 0) - new Date(b.host.desde ?? 0))
    .map(({ host, i }) =>
      el("tr", i ? { class: "clic", onclick: irA(i.id) } : {},
        el("td", {}, mono(host.ip)),
        el("td", {}, i?.severidad ? badge(SEVERIDAD[i.severidad] ?? i.severidad, i.severidad) : badge("—", "info")),
        el("td", {}, i ? badgeVeredicto(i.veredicto) : "—"),
        el("td", { class: "mono", title: host.desde ?? "" }, host.desde ? hace(host.desde) : "—"),
        el("td", { class: "mono" }, i ? el("a", { href: `#/incidentes/${i.id}` }, `Ver #${i.id} →`) : el("span", { class: "secundario" }, "sin incidente"))));
  vaciar(contenedor, tabla(["Host", "Severidad", "Veredicto", "Bloqueado", ""], filas));
}
