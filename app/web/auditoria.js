// Auditoría: últimos 50 eventos y verificación de la cadena de hashes.

import { api } from "./api.js";
import { $, el, hora, vaciar } from "./dom.js";

export async function refrescarAuditoria() {
  const lista = $("auditoria");
  try {
    const filas = await api("/api/auditoria?limite=50");
    if (!filas.length) {
      vaciar(lista, el("p", { class: "mensaje" }, "Sin eventos de auditoría todavía."));
      return;
    }
    const cuerpo = filas.map((fila) => {
      const datos = JSON.stringify(fila.datos ?? {});
      const corto = datos.length > 140 ? `${datos.slice(0, 137)}…` : datos;
      return el(
        "tr",
        {},
        el("td", { class: "mono" }, `#${fila.id}`),
        el("td", { class: "mono" }, hora(fila.ts)),
        el("td", { class: "mono" }, fila.evento),
        el("td", { class: "mono secundario", title: datos }, corto),
      );
    });
    const titulos = ["ID", "Hora", "Evento", "Datos"];
    vaciar(lista, el("table", {}, el("thead", {}, el("tr", {}, titulos.map((t) => el("th", {}, t)))), el("tbody", {}, cuerpo)));
  } catch (error) {
    vaciar(lista, el("p", { class: "mensaje error" }, `No se pudo leer la auditoría: ${error.message}`));
  }
}

export async function verificarCadena() {
  const indicador = $("ind-auditoria");
  indicador.className = "estado-cadena";
  vaciar(indicador, "Verificando la cadena…");
  try {
    const r = await api("/api/auditoria/verificar");
    if (r.integra) {
      indicador.className = "estado-cadena ok";
      vaciar(indicador, `Cadena íntegra · ${r.filas} filas verificadas`);
    } else {
      indicador.className = "estado-cadena mal";
      vaciar(indicador, `Cadena alterada desde la fila #${r.primera_rota} · ${r.filas} filas`);
    }
  } catch (error) {
    indicador.className = "estado-cadena aviso";
    vaciar(indicador, `No se pudo verificar: ${error.message}`);
  }
}

export function iniciarAuditoria() {
  $("btn-verificar").addEventListener("click", () => {
    verificarCadena();
    refrescarAuditoria();
  });
}
