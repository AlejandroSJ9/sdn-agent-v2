// Hosts bloqueados en Ryu y desbloqueo con confirmación (IP exacta + motivo de 5 a 200 caracteres).

import { api } from "./api.js";
import { $, el, hora, mensaje, vaciar } from "./dom.js";

let ipEnModal = null;

function abrirModal(ip) {
  ipEnModal = ip;
  vaciar($("modal-ip"), ip);
  $("modal-confirmacion").value = "";
  $("modal-motivo").value = "";
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
  const valido = $("modal-confirmacion").value === ipEnModal && motivo.length >= 5 && motivo.length <= 200;
  $("modal-confirmar").disabled = !valido;
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
      cuerpo: { confirmacion: $("modal-confirmacion").value, motivo: $("modal-motivo").value.trim() },
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
    if (!lista.length) {
      vaciar(contenedor, el("p", { class: "mensaje" }, "Ningún host bloqueado en Ryu."));
      return;
    }
    const filas = lista.map((host) =>
      el(
        "tr",
        {},
        el("td", {}, host.ip),
        el("td", {}, host.dpid ?? "—"),
        el("td", {}, hora(host.desde)),
        el("td", {}, host.incidente_id ? el("a", { href: `#/incidentes/${host.incidente_id}` }, `#${host.incidente_id}`) : "—"),
        el("td", {}, el("button", { class: "peligro", onclick: () => abrirModal(host.ip) }, "Desbloquear")),
      ),
    );
    vaciar(
      contenedor,
      el(
        "table",
        {},
        el("thead", {}, el("tr", {}, ["IP", "Switch", "Desde", "Incidente", ""].map((t) => el("th", {}, t)))),
        el("tbody", {}, filas),
      ),
    );
  } catch (error) {
    vaciar(contenedor, el("p", { class: "mensaje error" }, `No se pudo leer la lista de Ryu: ${error.message}`));
  }
}

export function iniciarBloqueados() {
  $("btn-bloqueados").addEventListener("click", refrescarBloqueados);
  $("modal-confirmacion").addEventListener("input", validarModal);
  $("modal-motivo").addEventListener("input", validarModal);
  $("modal-cancelar").addEventListener("click", cerrarModal);
  $("form-desbloqueo").addEventListener("submit", confirmar);
}
