// Motor de políticas (C7) en el panel: modo de decisión en la cabecera (con confirmación), aviso si el modo
// autónomo no tiene capacidad A6 y los parámetros editables en Configuración. Todo el texto entra como texto.

import { api } from "./api.js";
import { $, el, mensaje, vaciar } from "./dom.js";

const MODOS = {
  solo_explicar: "Solo explicar",
  recomendar: "Recomendar",
  autonomo_acotado: "Autónomo acotado",
};
const CAMPOS = [
  ["ESCALON_HITL", "Escalón HITL: debajo decide el humano", { type: "number", min: 0.5, max: 1, step: 0.01 }],
  ["ESCALON_VLAN", "Escalón VLAN: confirmado por encima → cuarentena en VLAN", { type: "number", min: 0.5, max: 1, step: 0.01 }],
  ["MAX_ACCIONES_POR_MIN", "Máx. acciones por minuto", { type: "number", min: 1, max: 20, step: 1 }],
  ["RECOMENDACION_TTL_MIN", "Vigencia de una recomendación (min)", { type: "number", min: 5, max: 1440, step: 1 }],
];
const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

let modoActual = null;

function aviso(texto) {
  const contenedor = $("avisos");
  const existente = contenedor.querySelector('[data-aviso="modo"]');
  if (!texto) {
    if (existente) existente.remove();
    return;
  }
  vaciar(existente || contenedor.appendChild(el("div", { class: "aviso-franja", "data-aviso": "modo" })), texto);
}

export async function refrescarPolitica() {
  let config;
  try {
    config = await api("/api/config");
  } catch {
    return;
  }
  modoActual = config.MODO_DECISION;
  $("sel-modo").value = modoActual;
  $("ind-modo").className = `chip-estado ${modoActual === "autonomo_acotado" ? "aviso" : modoActual === "solo_explicar" ? "neutro" : "ok"}`;
  for (const [clave] of CAMPOS) {
    const campo = $(`pol-${clave}`);
    if (campo && document.activeElement !== campo) campo.value = config[clave];
  }
  if (document.activeElement !== $("pol-HOSTS_PROTEGIDOS")) $("pol-HOSTS_PROTEGIDOS").value = config.HOSTS_PROTEGIDOS.join(", ");
  let capacidades = {};
  try {
    capacidades = (await api("/api/ryu/estado")).capacidades ?? {};
  } catch { /* sin datos de Ryu */ }
  const faltan = [["cuarentena", "A6"], ["limitar_tasa", "A7"], ["cuarentena_vlan", "A8"]].filter(([c]) => !capacidades[c]).map(([, a]) => a);
  aviso(modoActual === "autonomo_acotado" && faltan.length
    ? `Modo autónomo acotado activo, pero Ryu no tiene ${faltan.join(", ")}: esas acciones solo quedarán como recomendaciones.`
    : null);
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
  const advertencia = nuevo === "autonomo_acotado"
    ? "El agente ejecutará SIN preguntar la acción de la escalera (cuarentena de puertos, limitar tasa o VLAN) si C7 lo permite; si Ryu falla, vuelve a DROP total. Nunca desbloquea."
    : nuevo === "solo_explicar" ? "Se detiene toda acción y recomendación automática al instante." : "Las acciones quedarán como recomendaciones.";
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
  const hosts = $("pol-HOSTS_PROTEGIDOS").value.split(/[\s,]+/).filter(Boolean);
  const invalidos = hosts.filter((ip) => !IPV4.test(ip));
  if (invalidos.length) {
    mensaje(salida, `Hosts protegidos: solo IPv4 sueltas, sin subredes (${invalidos.join(", ")})`, "error");
    return;
  }
  cambios.HOSTS_PROTEGIDOS = hosts;
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
    el("p", { class: "ayuda" }, "El modo se cambia en la cabecera. El agente nunca desbloquea. En autónomo, la escalera relaja el DROP sola si C7 lo permite; el escalón HITL es el único umbral (UMBRAL_CONFIANZA se retiró)."),
    el("form", { id: "form-politica", class: "umbrales", onsubmit: guardarParametros },
      campos,
      el("label", {}, "Hosts protegidos (IPv4 separadas por coma)", el("textarea", { id: "pol-HOSTS_PROTEGIDOS", rows: 2, class: "mono" })),
      el("button", { type: "submit", class: "primario" }, "Guardar parámetros")),
    el("p", { id: "pol-msg", class: "mensaje" }),
  );
  refrescarPolitica();
}
