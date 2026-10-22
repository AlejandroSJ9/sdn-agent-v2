// Umbrales de Ryu en SOLO LECTURA (GET). Fase D2: el agente ya no los edita; se cambian en Ryu.

import { api } from "./api.js";
import { $, el, mono, vaciar } from "./dom.js";

export async function refrescarUmbrales() {
  const contenedor = $("lista-umbrales");
  try {
    const actuales = await api("/api/ryu/umbrales");
    vaciar(contenedor, Object.entries(actuales).map(([clave, valor]) => el("label", {}, clave, mono(String(valor)))));
  } catch (error) {
    vaciar(contenedor, el("p", { class: "mensaje error" }, `No se pudieron leer los umbrales de Ryu: ${error.message}`));
  }
}
