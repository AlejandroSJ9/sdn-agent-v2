// Umbrales de Ryu: se leen con GET y se guardan con PUT (solo las claves cambiadas), con confirmación.

import { api } from "./api.js";
import { $, el, mensaje, vaciar } from "./dom.js";

let actuales = {};

export async function refrescarUmbrales() {
  const formulario = $("form-umbrales");
  try {
    actuales = await api("/api/ryu/umbrales");
    const campos = Object.entries(actuales).map(([clave, valor]) =>
      el("label", {}, clave, el("input", { type: "number", min: 1, step: 1, name: clave, value: valor })),
    );
    vaciar(formulario, campos, el("button", { type: "submit", class: "primario" }, "Guardar cambios"));
  } catch (error) {
    vaciar(formulario, el("p", { class: "mensaje error" }, `No se pudieron leer los umbrales de Ryu: ${error.message}`));
  }
}

async function guardar(evento) {
  evento.preventDefault();
  const salida = $("msg-umbrales");
  const cambios = {};
  for (const campo of $("form-umbrales").querySelectorAll("input")) {
    const valor = Number(campo.value);
    if (!Number.isInteger(valor) || valor <= 0) {
      mensaje(salida, `${campo.name} debe ser un entero mayor que 0`, "error");
      return;
    }
    if (valor !== actuales[campo.name]) cambios[campo.name] = valor;
  }
  const claves = Object.keys(cambios);
  if (!claves.length) {
    mensaje(salida, "No hay cambios.");
    return;
  }
  const resumen = claves.map((c) => `${c}: ${actuales[c]} → ${cambios[c]}`).join("\n");
  if (!window.confirm(`¿Cambiar los umbrales de detección de Ryu?\n\n${resumen}`)) return;
  try {
    await api("/api/ryu/umbrales", { metodo: "PUT", cuerpo: cambios });
    mensaje(salida, `Umbrales actualizados (${claves.join(", ")}).`, "exito");
  } catch (error) {
    mensaje(salida, `No se guardaron: ${error.message}`, "error");
  }
  refrescarUmbrales();
}

export function iniciarUmbrales() {
  $("form-umbrales").addEventListener("submit", guardar);
}
