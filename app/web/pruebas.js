// Exportaciones del laboratorio (CSV/JSON). Fase D2: el botón de pytest se retiró del panel.

import { pedirToken, token } from "./api.js";
import { $, mensaje } from "./dom.js";
import { corridaActual } from "./laboratorio.js";

export async function descargar(ruta, nombre, salida = $("export-msg")) {
  try {
    const respuesta = await fetch(ruta, { headers: { "X-Operador-Token": token.leer() } });
    if (respuesta.status === 401) {
      pedirToken();
      return;
    }
    if (!respuesta.ok) throw new Error(`HTTP ${respuesta.status}`);
    const url = URL.createObjectURL(await respuesta.blob());
    const enlace = document.createElement("a");
    enlace.href = url;
    enlace.download = nombre;
    enlace.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    mensaje(salida, `Descargado ${nombre}`, "exito");
  } catch (error) {
    mensaje(salida, `No se pudo exportar: ${error.message}`, "error");
  }
}

function exportarCorrida(formato) {
  const id = corridaActual();
  if (!id) {
    mensaje($("export-msg"), "Primero ejecuta o abre una corrida.", "error");
    return;
  }
  descargar(`/api/export/corrida/${id}.${formato}`, `corrida_${id}.${formato}`);
}

export function iniciarPruebas() {
  $("btn-export-incidentes").addEventListener("click", () => {
    const origen = $("export-origen").value;
    descargar(`/api/export/incidentes.csv?origen=${encodeURIComponent(origen)}`, `incidentes_${origen}.csv`);
  });
  $("btn-export-json").addEventListener("click", () => exportarCorrida("json"));
  $("btn-export-csv").addEventListener("click", () => exportarCorrida("csv"));
}
