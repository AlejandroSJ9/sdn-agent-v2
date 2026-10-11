// Línea de tiempo en vivo (últimos 100 eventos) y franja del flujo del último incidente.

import { api } from "./api.js";
import { $, duracion, el, hora, msEntre, vaciar } from "./dom.js";

const MAXIMO_EVENTOS = 100;
const eventos = [];
let incidenteSeguido = null;
let etapasPrevias = new Set();

function resumenDatos(datos) {
  const texto = JSON.stringify(datos ?? {});
  return texto.length > 120 ? `${texto.slice(0, 117)}…` : texto;
}

function mostrarJson(evento, fila) {
  $("linea-tiempo").querySelectorAll("li.elegido").forEach((li) => li.classList.remove("elegido"));
  fila.classList.add("elegido");
  const salida = $("evento-json");
  salida.hidden = false;
  salida.textContent = JSON.stringify(evento, null, 2);
}

export function agregarEvento(evento) {
  eventos.unshift(evento);
  if (eventos.length > MAXIMO_EVENTOS) eventos.pop();
  const lista = $("linea-tiempo");
  const fila = el("li", {}, hora(evento.ts), el("span", { class: "tipo" }, evento.tipo), el("span", { class: "datos" }, resumenDatos(evento.datos)));
  fila.addEventListener("click", () => mostrarJson(evento, fila));
  lista.prepend(fila);
  while (lista.children.length > MAXIMO_EVENTOS) lista.lastElementChild.remove();
}

// ---- Franja Ryu → Recibido → Explicado → Notificado → Desbloqueado ----

function etapas(i) {
  const notif = i.ultima_notificacion;
  const problemaCorreo = !i.t_notificado && notif && notif.estado !== "enviada";
  return [
    { clave: "ryu", nombre: "Ryu detecta", t: i.t_ryu },
    { clave: "recibido", nombre: "Recibido", t: i.t_recibido },
    { clave: "explicado", nombre: `Explicado${i.fuente_explicacion ? ` (${i.fuente_explicacion})` : ""}`, t: i.t_explicado },
    {
      clave: problemaCorreo ? "notificado problema" : "notificado",
      nombre: problemaCorreo ? `Correo ${notif.estado}` : "Notificado",
      t: i.t_notificado,
      forzarActiva: problemaCorreo,
    },
    { clave: "desbloqueado", nombre: "Desbloqueado", t: i.t_desbloqueado },
  ];
}

export async function seguirIncidente(id) {
  if (!id) return;
  if (id !== incidenteSeguido) etapasPrevias = new Set();
  incidenteSeguido = id;
  let incidente;
  try {
    incidente = await api(`/api/incidentes/${id}`);
  } catch {
    return;
  }
  if (incidenteSeguido !== id) return;
  vaciar($("flujo-titulo"), `#${incidente.id} · ${incidente.host_origen}`);
  const nodos = [];
  let anterior = null;
  etapas(incidente).forEach((etapa, indice) => {
    const activa = Boolean(etapa.t) || etapa.forzarActiva;
    if (indice > 0) {
      const ms = etapa.t && anterior ? msEntre(anterior, etapa.t) : null;
      nodos.push(el("div", { class: "cable" }, ms === null ? "···" : duracion(ms)));
    }
    const nueva = activa && !etapasPrevias.has(etapa.clave);
    nodos.push(
      el(
        "div",
        { class: `etapa ${etapa.clave}${activa ? " activa" : ""}${nueva ? " nueva" : ""}` },
        el("strong", {}, etapa.nombre),
        hora(etapa.t),
      ),
    );
    if (activa) etapasPrevias.add(etapa.clave);
    if (etapa.t) anterior = etapa.t;
  });
  vaciar($("flujo"), nodos);
}

export function incidenteDelFlujo() {
  return incidenteSeguido;
}
