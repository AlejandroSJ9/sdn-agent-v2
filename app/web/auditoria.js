// Auditoría: últimos 50 eventos y verificación de la cadena de hashes.

import { api } from "./api.js";
import { $, el, hora, vaciar } from "./dom.js";

export async function refrescarAuditoria() {
  const lista = $("auditoria");
  try {
    const filas = await api("/api/auditoria?limite=50");
    vaciar(
      lista,
      filas.map((fila) => {
        const datos = JSON.stringify(fila.datos ?? {});
        return el(
          "li",
          { title: datos },
          el("span", {}, `#${fila.id}`),
          hora(fila.ts),
          el("span", { class: "tipo" }, fila.evento),
          el("span", { class: "datos" }, datos),
        );
      }),
    );
  } catch (error) {
    vaciar(lista, el("li", { class: "mensaje error" }, `No se pudo leer la auditoría: ${error.message}`));
  }
}

export async function verificarCadena() {
  const indicador = $("ind-auditoria");
  indicador.className = "indicador";
  vaciar(indicador, "verificando…");
  try {
    const r = await api("/api/auditoria/verificar");
    if (r.integra) {
      indicador.className = "indicador ok";
      vaciar(indicador, `● Íntegra (${r.filas} filas)`);
    } else {
      indicador.className = "indicador mal";
      vaciar(indicador, `● ALTERADA desde la fila #${r.primera_rota} (${r.filas} filas)`);
    }
  } catch (error) {
    indicador.className = "indicador aviso";
    vaciar(indicador, `No se pudo verificar: ${error.message}`);
  }
}

export function iniciarAuditoria() {
  $("btn-verificar").addEventListener("click", () => {
    verificarCadena();
    refrescarAuditoria();
  });
}
