// Cabecera: salud del agente, Ryu, conexión en vivo, cupo del LLM, alertas rechazadas, configuración y tarjetas.

import { api } from "./api.js";
import { $, el, hora, mensaje, vaciar } from "./dom.js";

let configActual = null;

function indicador(id, texto, clase) {
  const nodo = $(id);
  nodo.className = `chip-estado ${clase}`;
  vaciar(nodo, texto);
}

function aviso(clave, texto) {
  const contenedor = $("avisos");
  const existente = contenedor.querySelector(`[data-aviso="${clave}"]`);
  if (!texto) {
    if (existente) existente.remove();
    return;
  }
  const nodo = existente || contenedor.appendChild(el("div", { class: "aviso-franja", "data-aviso": clave }));
  vaciar(nodo, texto);
}

export function marcarConexion({ conectado, reintentoEn }) {
  if (conectado) {
    indicador("ind-vivo", "En vivo", "ok");
    aviso("vivo", null);
  } else {
    indicador("ind-vivo", "Sin conexión en vivo", "mal");
    const detalle = reintentoEn ? `; reintentando en ${Math.round(reintentoEn / 1000)} s` : "";
    aviso("vivo", `Sin conexión en vivo con el agente${detalle}. El panel sigue funcionando; refresca a mano si lo necesitas.`);
  }
}

async function refrescarSalud() {
  try {
    const salud = await fetch("/api/salud").then((r) => r.json());
    indicador("ind-agente", `Agente ${salud.version}`, "ok");
  } catch {
    indicador("ind-agente", "Agente sin respuesta", "mal");
  }
}

async function refrescarRyu() {
  try {
    const ryu = await api("/api/ryu/estado");
    if (ryu.disponible) {
      indicador("ind-ryu", `Ryu · ${ryu.switches?.length ?? 0} switch`, "ok");
      aviso("ryu", null);
    } else {
      indicador("ind-ryu", "Ryu no disponible", "mal");
      aviso("ryu", `Ryu no responde (${ryu.error}). Las acciones sobre Ryu fallarán hasta que vuelva.`);
    }
  } catch (error) {
    indicador("ind-ryu", "Ryu: sin datos", "aviso");
  }
}

async function refrescarCupo() {
  try {
    const cupo = await api("/api/llm/cupo");
    vaciar($("cupo-texto"), `${cupo.usadas}/${cupo.cupo}`);
    $("cupo-texto").title = `Restantes ${cupo.restantes} · fallidas ${cupo.fallidas} · día UTC ${cupo.fecha}`;
    const uso = Math.min(100, (100 * cupo.usadas) / Math.max(cupo.cupo, 1));
    $("cupo-barra").style.width = `${uso}%`;
    vaciar($("res-cupo"), `${cupo.usadas}/${cupo.cupo}`);
    $("ind-cupo").className = `chip-estado ${uso >= 100 ? "mal" : uso >= 80 ? "aviso" : "neutro"}`;
  } catch { /* el aviso general ya informa */ }
}

async function refrescarRechazadas() {
  try {
    const r = await api("/api/alertas/rechazadas");
    vaciar($("rechazadas-texto"), r.total);
    $("ind-rechazadas").className = `chip-estado ${r.total > 0 ? "aviso" : "neutro"}`;
    const lineas = r.motivos.map((m) => el("li", {}, el("span", { class: "mono" }, `${m.codigo}`), ` ${m.detalle} — ${m.cantidad} ${m.cantidad === 1 ? "vez" : "veces"}, última ${hora(m.ultima)}`));
    vaciar($("detalle-rechazadas"), lineas.length ? el("ul", {}, lineas) : "Ninguna alerta rechazada desde que arrancó el agente.");
  } catch { /* el aviso general ya informa */ }
}

function alternarRechazadas() {
  const detalle = $("detalle-rechazadas");
  detalle.hidden = !detalle.hidden;
  $("ind-rechazadas").setAttribute("aria-expanded", String(!detalle.hidden));
}

async function refrescarConfig() {
  try {
    configActual = await api("/api/config");
    const boton = $("btn-llm");
    boton.classList.toggle("encendido", configActual.LLM_HABILITADO);
    vaciar(boton, configActual.LLM_HABILITADO ? "LLM encendido" : "LLM apagado (reglas)");
    indicador("ind-llm", configActual.LLM_HABILITADO ? "LLM encendido" : "LLM apagado", configActual.LLM_HABILITADO ? "ok" : "neutro");
    if (document.activeElement !== $("campo-dedup")) $("campo-dedup").value = configActual.DEDUP_SEGUNDOS;
    const contexto = $("btn-contexto");
    contexto.classList.toggle("encendido", configActual.CONTEXTO_HABILITADO);
    vaciar(contexto, configActual.CONTEXTO_HABILITADO ? "Contexto encendido" : "Contexto apagado");
    if (document.activeElement !== $("campo-historial")) $("campo-historial").value = configActual.HISTORIAL_DIAS;
    if (document.activeElement !== $("campo-campana")) $("campo-campana").value = configActual.CAMPANA_MINUTOS;
  } catch { /* sin cambios */ }
}

export async function refrescarResumen() {
  try {
    const r = await api("/api/resumen");
    vaciar($("res-abiertos"), r.incidentes_abiertos);
    vaciar($("res-hoy"), r.incidentes_hoy);
    vaciar($("res-fallidas"), r.notificaciones_fallidas_hoy);
    const ultimo = r.ultimo_incidente_id;
    vaciar($("res-ultimo"), ultimo ? el("a", { href: `#/incidentes/${ultimo}` }, `#${ultimo} →`) : "—");
    return r;
  } catch {
    return null;
  }
}

export function refrescarEstado() {
  refrescarSalud();
  refrescarRyu();
  refrescarCupo();
  refrescarRechazadas();
  refrescarConfig();
  return refrescarResumen();
}

async function guardarConfig(cambios) {
  try {
    await api("/api/config", { metodo: "PUT", cuerpo: cambios });
    mensaje($("msg-config"), "Configuración guardada", "exito");
  } catch (error) {
    mensaje($("msg-config"), `No se guardó: ${error.message}`, "error");
  }
  refrescarConfig();
}

export function iniciarEstado() {
  $("btn-llm").addEventListener("click", () => {
    if (configActual) guardarConfig({ LLM_HABILITADO: !configActual.LLM_HABILITADO });
  });
  $("btn-dedup").addEventListener("click", () => {
    const valor = Number($("campo-dedup").value);
    if (!Number.isInteger(valor) || valor < 10 || valor > 3600) {
      mensaje($("msg-config"), "DEDUP debe ser un entero entre 10 y 3600", "error");
      return;
    }
    guardarConfig({ DEDUP_SEGUNDOS: valor });
  });
  $("btn-contexto").addEventListener("click", () => {
    if (configActual) guardarConfig({ CONTEXTO_HABILITADO: !configActual.CONTEXTO_HABILITADO });
  });
  $("btn-ventanas").addEventListener("click", () => {
    const dias = Number($("campo-historial").value);
    const minutos = Number($("campo-campana").value);
    if (!Number.isInteger(dias) || dias < 1 || dias > 90 || !Number.isInteger(minutos) || minutos < 5 || minutos > 240) {
      mensaje($("msg-config"), "Historial: entero de 1 a 90 días · Actividad de la red: entero de 5 a 240 min", "error");
      return;
    }
    guardarConfig({ HISTORIAL_DIAS: dias, CAMPANA_MINUTOS: minutos });
  });
  $("ind-rechazadas").addEventListener("click", alternarRechazadas);
  setInterval(() => { refrescarRyu(); refrescarCupo(); refrescarRechazadas(); }, 30000);
}
