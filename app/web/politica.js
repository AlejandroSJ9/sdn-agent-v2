// Motor de políticas (C7) en el panel: modo de decisión en la cabecera (con confirmación) y los parámetros
// editables en Configuración. Fase D1: el agente solo recomienda; el operador aprueba. Todo el texto entra como texto.

import { api } from "./api.js";
import { $, el, mensaje, vaciar } from "./dom.js";

const MODOS = {
  solo_explicar: "Solo explicar",
  recomendar: "Recomendar",
};
const CAMPOS = [
  ["ESCALON_HITL", "Escalón HITL: debajo decide el humano", { type: "number", min: 0.5, max: 1, step: 0.01 }],
  ["ESCALON_VLAN", "Escalón VLAN: confirmado por encima → cuarentena en VLAN", { type: "number", min: 0.5, max: 1, step: 0.01 }],
  ["RECOMENDACION_TTL_MIN", "Vigencia de una recomendación (min)", { type: "number", min: 5, max: 1440, step: 1 }],
];

let modoActual = null;

export async function refrescarPolitica() {
  let config;
  try {
    config = await api("/api/config");
  } catch {
    return;
  }
  modoActual = config.MODO_DECISION;
  $("sel-modo").value = modoActual;
  $("ind-modo").className = `chip-estado ${modoActual === "solo_explicar" ? "neutro" : "ok"}`;
  for (const [clave] of CAMPOS) {
    const campo = $(`pol-${clave}`);
    if (campo && document.activeElement !== campo) campo.value = config[clave];
  }
}

async function guardar(cambios, salida) {
  try {
    await api("/api/config", { metodo: "PUT", cuerpo: cambios });
    mensaje(salida, "Guardado.", "exito");
  } catch (error) {
    mensaje(salida, `No se guardó: ${error.message}`, "error");
  }
  refrescarPolitica();
}

async function cambiarModo() {
  const nuevo = $("sel-modo").value;
  const advertencia = nuevo === "solo_explicar"
    ? "Se detiene toda recomendación automática al instante."
    : "Las acciones quedarán como recomendaciones; solo se ejecutan si las apruebas.";
  if (!window.confirm(`¿Cambiar el modo de decisión a "${MODOS[nuevo]}"?\n\n${advertencia}`)) {
    $("sel-modo").value = modoActual;
    return;
  }
  guardar({ MODO_DECISION: nuevo }, $("pol-msg"));
}

function guardarParametros(evento) {
  evento.preventDefault();
  const salida = $("pol-msg");
  const cambios = {};
  for (const [clave, , atributos] of CAMPOS) {
    const valor = Number($(`pol-${clave}`).value);
    const entero = atributos.step === 1;
    if (Number.isNaN(valor) || valor < atributos.min || valor > atributos.max || (entero && !Number.isInteger(valor))) {
      mensaje(salida, `${clave}: entre ${atributos.min} y ${atributos.max}${entero ? " (entero)" : ""}`, "error");
      return;
    }
    cambios[clave] = valor;
  }
  if (cambios.ESCALON_HITL >= cambios.ESCALON_VLAN) {
    mensaje(salida, "El escalón HITL debe ser menor que el escalón VLAN.", "error");
    return;
  }
  guardar(cambios, salida);
}

export function iniciarPolitica() {
  vaciar($("sel-modo"), Object.entries(MODOS).map(([valor, texto]) => el("option", { value: valor }, texto)));
  $("sel-modo").addEventListener("change", cambiarModo);
  const campos = CAMPOS.map(([clave, texto, atributos]) =>
    el("label", {}, texto, el("input", { id: `pol-${clave}`, class: "mono", ...atributos })));
  vaciar(
    $("config-politica"),
    el("header", { class: "panel-cabecera" }, el("h2", {}, "Decisión acotada (motor de políticas)")),
    el("p", { class: "ayuda" }, "El modo se cambia en la cabecera. El agente nunca ejecuta por su cuenta ni desbloquea: toda acción de la escalera queda como recomendación y solo se ejecuta si el operador la aprueba. El escalón HITL es el único umbral."),
    el("form", { id: "form-politica", class: "umbrales", onsubmit: guardarParametros },
      campos,
      el("button", { type: "submit", class: "primario" }, "Guardar parámetros")),
    el("p", { id: "pol-msg", class: "mensaje" }),
  );
  refrescarPolitica();
}
