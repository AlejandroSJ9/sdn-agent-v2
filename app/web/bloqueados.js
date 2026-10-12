// Hosts bloqueados en Ryu y desbloqueo con confirmación (IP exacta + motivo de 5 a 200 caracteres + clasificación).

import { api } from "./api.js";
import { $, badge, el, hora, mensaje, vaciar } from "./dom.js";

let ipEnModal = null;

function abrirModal(ip) {
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

const ACCIONES_MODO = {
  drop_total: ["cuarentena", "Pasar a cuarentena", "El host recupera lo esencial, pero los puertos de movimiento lateral siguen bloqueados."],
  cuarentena: ["bloqueo_total", "Volver a bloqueo total", "El host vuelve a quedar sin red."],
};

async function cambiarModo(ip, modo) {
  const [ruta, texto, efecto] = ACCIONES_MODO[modo];
  if (!window.confirm(`¿${texto} ${ip}?\n\n${efecto}`)) return;
  try {
    await api(`/api/hosts/${encodeURIComponent(ip)}/${ruta}`, { metodo: "POST" });
  } catch (error) {
    window.alert(`No se aplicó: ${error.message}`);
  }
  refrescarBloqueados();
}

function botonesModo(host) {
  const accion = ACCIONES_MODO[host.modo];
  return accion ? el("button", { class: "discreto", onclick: () => cambiarModo(host.ip, host.modo) }, accion[1]) : null;
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
    const lista = await api("/api/ryu/bloqueados");
    vaciar($("res-bloqueados"), lista.length);
    if (!lista.length) {
      vaciar(contenedor, el("p", { class: "mensaje" }, "Ningún host bloqueado en Ryu."));
      return;
    }
    // "modo" solo existe si Ryu tiene cuarentena (A6): la columna aparece únicamente en ese caso (solo lectura).
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
        conModo ? el("td", { class: "mono" }, host.modo ?? "—") : null,
        el("td", {}, conModo ? [botonesModo(host), " "] : null,
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
