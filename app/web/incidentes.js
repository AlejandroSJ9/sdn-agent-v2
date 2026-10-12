// Incidentes: tabla paginada. El detalle (#/incidentes/<id>) es una página completa: detalle.js.

import { api } from "./api.js";
import { $, badge, badgeVeredicto, el, mono, vaciar } from "./dom.js";

const POR_PAGINA = 20;
let pagina = 0;
let origen = "real";

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
        el("td", { class: "mono" }, el("a", { href: `#/incidentes/${i.id}` }, `#${i.id}`)),
        el("td", {}, mono(i.host_origen), etiquetaPrueba(i)),
        el("td", {}, severidad(i.severidad)),
        el("td", {}, badgeVeredicto(i.veredicto)),
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

export function iniciarIncidentes() {
  $("pag-anterior").addEventListener("click", () => { pagina = Math.max(0, pagina - 1); refrescarIncidentes(); });
  $("pag-siguiente").addEventListener("click", () => { pagina += 1; refrescarIncidentes(); });
  $("filtro-origen").addEventListener("change", (evento) => {
    origen = evento.target.value;
    pagina = 0;
    refrescarIncidentes();
  });
}
