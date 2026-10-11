// Pruebas automáticas desde el panel (consola de solo lectura) y exportaciones (CSV/JSON).

import { api, pedirToken, token } from "./api.js";
import { $, mensaje, vaciar } from "./dom.js";
import { corridaActual } from "./laboratorio.js";

const MAX_LINEAS = 3000;
let ejecucionActual = null;

function botones(habilitados) {
  $("btn-pytest-unit").disabled = !habilitados;
  $("btn-pytest-integracion").disabled = !habilitados;
}

function textoResultado(r) {
  const cifras = r.aprobadas === null ? "sin resumen" : `${r.aprobadas} aprobadas, ${r.fallidas} fallidas`;
  const segundos = r.duracion_ms === null ? "" : ` en ${(r.duracion_ms / 1000).toFixed(1)} s`;
  return `${r.carpeta}: ${r.estado} · ${cifras}${segundos}`;
}

async function lanzar(carpeta) {
  const consola = $("pytest-consola");
  try {
    const r = await api("/api/laboratorio/pytest", { metodo: "POST", cuerpo: { carpeta } });
    ejecucionActual = r.ejecucion_id;
    vaciar(consola);
    botones(false);
    mensaje($("pytest-msg"), `Ejecutando pruebas de ${carpeta}…`);
  } catch (error) {
    mensaje($("pytest-msg"), `No se pudo ejecutar: ${error.message}`, "error");
  }
}

export function alLinea(datos) {
  if (ejecucionActual !== datos.ejecucion_id) {
    ejecucionActual = datos.ejecucion_id;
    vaciar($("pytest-consola"));
    botones(false);
  }
  const consola = $("pytest-consola");
  consola.append(document.createTextNode(`${datos.linea}\n`)); // texto, nunca HTML
  while (consola.childNodes.length > MAX_LINEAS) consola.firstChild.remove();
  consola.scrollTop = consola.scrollHeight;
}

export function alTerminar(datos) {
  botones(true);
  mensaje($("pytest-msg"), textoResultado(datos), datos.estado === "terminada" && datos.fallidas === 0 ? "exito" : "error");
}

async function mostrarUltimo() {
  try {
    const r = await api("/api/laboratorio/pytest/ultimo");
    if (!r) return;
    $("pytest-consola").textContent = r.salida;
    mensaje($("pytest-msg"), `Última ejecución · ${textoResultado(r)}`);
    if (r.estado === "en_curso") botones(false);
  } catch { /* sin ejecuciones previas */ }
}

async function descargar(ruta, nombre) {
  const salida = $("export-msg");
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
  $("btn-pytest-unit").addEventListener("click", () => lanzar("unit"));
  $("btn-pytest-integracion").addEventListener("click", () => lanzar("integracion"));
  $("btn-export-incidentes").addEventListener("click", () => {
    const origen = $("export-origen").value;
    descargar(`/api/export/incidentes.csv?origen=${encodeURIComponent(origen)}`, `incidentes_${origen}.csv`);
  });
  $("btn-export-json").addEventListener("click", () => exportarCorrida("json"));
  $("btn-export-csv").addEventListener("click", () => exportarCorrida("csv"));
  mostrarUltimo();
}
