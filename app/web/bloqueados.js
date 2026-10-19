// Hosts bloqueados en Ryu y desbloqueo con confirmación (IP exacta + motivo de 5 a 200 caracteres + clasificación).

import { api } from "./api.js";
import { $, badge, el, hora, mensaje, vaciar } from "./dom.js";
import { pintarAtencion } from "./tablero.js";

let ipEnModal = null;

// También lo usa la cabecera del detalle del incidente: mismo flujo (IP exacta + motivo + clasificación).
export function abrirModal(ip) {
  ipEnModal = ip;
  vaciar($("modal-ip"), ip);
  $("modal-confirmacion").value = "";
  $("modal-motivo").value = "";
  $("modal-clasificacion").value = "";
  mensaje($("modal-resultado"), "");
  $("modal-confirmar").disabled = true;
  $("modal").hidden = false;
  $("modal-confirmacion").focus();
}

function cerrarModal() {
  $("modal").hidden = true;
  ipEnModal = null;
}

function validarModal() {
  const motivo = $("modal-motivo").value.trim();
  const clasificada = $("modal-clasificacion").value !== "";
  const valido = $("modal-confirmacion").value === ipEnModal && motivo.length >= 5 && motivo.length <= 200 && clasificada;
  $("modal-confirmar").disabled = !valido;
}

// Fase 9b: modos de C2 (leídos de Ryu en vivo). Desde DROP total, el operador puede pasar a cuarentena de puertos;
// desde cualquier otro modo, volver a DROP total. limitar_tasa y cuarentena_vlan no tienen botón manual (solo escalera).
const MODOS = { drop_total: "DROP total", cuarentena: "Cuarentena de puertos", limitado: "Tasa limitada", vlan_cuarentena: "VLAN de cuarentena" };
const A_CUARENTENA = ["cuarentena", "Cuarentena de puertos", "El host recupera lo esencial, pero los puertos de movimiento lateral siguen bloqueados."];
const A_DROP = ["bloqueo_total", "Volver a DROP total", "El host vuelve a quedar sin red."];

function textoModo(host) {
  const extra = host.modo === "limitado" && host.kbps ? ` (${host.kbps} kbps)` : host.modo === "vlan_cuarentena" && host.vlan ? ` (VLAN ${host.vlan})` : "";
  return `${MODOS[host.modo] ?? host.modo ?? "—"}${extra}`;
}

async function cambiarModo(ip, modo) {
  const [ruta, texto, efecto] = modo === "drop_total" ? A_CUARENTENA : A_DROP;
  if (!window.confirm(`¿${texto} ${ip}?\n\n${efecto}`)) return;
  try {
    await api(`/api/hosts/${encodeURIComponent(ip)}/${ruta}`, { metodo: "POST" });
  } catch (error) {
    window.alert(`No se aplicó: ${error.message}`);
  }
  refrescarBloqueados();
}

function botonesModo(host, conA6) {
  if (!host.modo || (host.modo === "drop_total" && !conA6)) return null;  // la cuarentena manual exige A6 declarada
  const [, texto] = host.modo === "drop_total" ? A_CUARENTENA : A_DROP;
  return el("button", { class: "discreto", onclick: () => cambiarModo(host.ip, host.modo) }, texto);
}

async function confirmar(evento) {
  evento.preventDefault();
  const ip = ipEnModal;
  const boton = $("modal-confirmar");
  boton.disabled = true;
  mensaje($("modal-resultado"), "Pidiendo a Ryu que desbloquee…");
  try {
    const r = await api(`/api/hosts/${encodeURIComponent(ip)}/desbloquear`, {
      metodo: "POST",
      cuerpo: {
        confirmacion: $("modal-confirmacion").value,
        motivo: $("modal-motivo").value.trim(),
        clasificacion: $("modal-clasificacion").value,
      },
    });
    const incidentes = r.incidentes.length ? `incidentes ${r.incidentes.map((i) => `#${i}`).join(", ")}` : "sin incidente abierto";
    mensaje($("modal-resultado"), `Ryu desbloqueó ${r.ip} (reglas borradas: ${r.reglas_borradas ?? "—"}; ${incidentes}).`, "exito");
    refrescarBloqueados();
    setTimeout(cerrarModal, 1800);
  } catch (error) {
    mensaje($("modal-resultado"), `No se desbloqueó: ${error.message}`, "error");
    validarModal();
  }
}

export async function refrescarBloqueados() {
  const contenedor = $("bloqueados");
  try {
    const [lista, estado] = await Promise.all([api("/api/ryu/bloqueados"), api("/api/ryu/estado").catch(() => null)]);
    const conA6 = Boolean(estado?.capacidades?.cuarentena);
    vaciar($("res-bloqueados"), lista.length);
    pintarAtencion(lista);
    if (!lista.length) {
      vaciar(contenedor, el("p", { class: "mensaje" }, "Ningún host bloqueado en Ryu."));
      return;
    }
    // "modo" solo existe si Ryu tiene A6, A7 o A8: la columna aparece únicamente en ese caso.
    const conModo = lista.some((host) => "modo" in host);
    const filas = lista.map((host) =>
      el(
        "tr",
        {},
        el("td", { class: "mono" }, host.ip),
        el("td", { class: "mono" }, host.dpid ?? "—"),
        el("td", { class: "mono" }, hora(host.desde)),
        el("td", { class: "mono" }, host.incidente_id ? el("a", { href: `#/incidentes/${host.incidente_id}` }, `#${host.incidente_id}`) : "—"),
        el("td", {}, badge("BLOQUEADO", "critica")),
        conModo ? el("td", {}, textoModo(host)) : null,
        el("td", {}, conModo ? [botonesModo(host, conA6), " "] : null,
          el("button", { class: "peligro", onclick: () => abrirModal(host.ip) }, "Desbloquear")),
      ),
    );
    vaciar(
      contenedor,
      el(
        "table",
        {},
        el("thead", {}, el("tr", {}, ["IP", "Switch", "Desde", "Incidente", "Estado", ...(conModo ? ["Modo"] : []), ""].map((t) => el("th", {}, t)))),
        el("tbody", {}, filas),
      ),
    );
  } catch (error) {
    vaciar($("res-bloqueados"), "—");
    pintarAtencion(null);
    vaciar(contenedor, el("p", { class: "mensaje error" }, `Ryu no disponible: ${error.message}`));
  }
}

export function iniciarBloqueados() {
  $("btn-bloqueados").addEventListener("click", refrescarBloqueados);
  $("modal-confirmacion").addEventListener("input", validarModal);
  $("modal-motivo").addEventListener("input", validarModal);
  $("modal-clasificacion").addEventListener("change", validarModal);
  $("modal-cancelar").addEventListener("click", cerrarModal);
  $("form-desbloqueo").addEventListener("submit", confirmar);
}
