// Arranque del panel: acceso con token, rutas con hash y reparto de los eventos en vivo a cada sección.

import { api, ErrorApi, token } from "./api.js";
import { iniciarAuditoria, refrescarAuditoria, verificarCadena } from "./auditoria.js";
import { iniciarBloqueados, refrescarBloqueados } from "./bloqueados.js";
import { iniciarComparar, refrescarCorridasComparables } from "./comparar.js";
import { iniciarPolitica, refrescarPolitica } from "./politica.js";
import { iniciarRecomendaciones, refrescarRecomendaciones } from "./recomendaciones.js";
import { $, agrupar, mensaje } from "./dom.js";
import { detalleAbierto, mostrarDetalle, ocultarDetalle } from "./detalle.js";
import { iniciarEstado, marcarConexion, refrescarEstado } from "./estado.js";
import { iniciarIncidentes, refrescarIncidentes } from "./incidentes.js";
import { refrescarActividad, refrescarResumen } from "./tablero.js";
import { agregarEvento, incidenteDelFlujo, seguirIncidente } from "./timeline.js";
import * as laboratorio from "./laboratorio.js";
import * as pruebas from "./pruebas.js";
import { refrescarUmbrales } from "./umbrales.js";
import { conectarEventos } from "./ws.js";

let conexion = null;
let iniciado = false;

const refrescar = {
  bloqueados: agrupar(refrescarBloqueados),
  incidentes: agrupar(refrescarIncidentes),
  resumen: agrupar(refrescarResumen),
  actividad: agrupar(refrescarActividad),
  auditoria: agrupar(refrescarAuditoria),
  estado: agrupar(refrescarEstado),
  umbrales: agrupar(refrescarUmbrales),
  politica: agrupar(refrescarPolitica),
  recomendaciones: agrupar(refrescarRecomendaciones),
};

const VISTAS = ["resumen", "incidentes", "bloqueados", "recomendaciones", "laboratorio", "auditoria", "configuracion"];

function cerrarMenu() {
  document.body.classList.remove("menu-abierto");
  $("btn-menu").setAttribute("aria-expanded", "false");
}

// Rutas: #/resumen (por defecto), #/incidentes, #/bloqueados, #/laboratorio, #/auditoria, #/configuracion.
// #/incidentes/<id> abre el detalle a página completa (el enlace del correo depende de esta ruta).
// "Volver" regresa a la vista desde la que se abrió (por defecto, Incidentes).
const RUTA_DETALLE = /^#\/incidentes\/(\d+)$/;

function enrutar(evento) {
  const detalle = location.hash.match(RUTA_DETALLE);
  const pedida = detalle ? "detalle" : (location.hash.match(/^#\/([a-z]+)$/) || [])[1];
  const vista = detalle || VISTAS.includes(pedida) ? pedida : "resumen";
  const marcada = detalle ? "incidentes" : vista;
  document.querySelectorAll("[data-vista]").forEach((seccion) => { seccion.hidden = seccion.dataset.vista !== vista; });
  document.querySelectorAll("[data-nav]").forEach((enlace) => {
    if (enlace.dataset.nav === marcada) enlace.setAttribute("aria-current", "page");
    else enlace.removeAttribute("aria-current");
  });
  cerrarMenu();
  if (!detalle) {
    ocultarDetalle();
    return;
  }
  const anterior = evento?.oldURL ? new URL(evento.oldURL).hash : "";
  const desde = !evento || RUTA_DETALLE.test(anterior) ? null : anterior || "#/resumen";
  if (desde !== null || !evento) window.scrollTo(0, 0);
  mostrarDetalle(Number(detalle[1]), desde);
}

function alEventoDePrueba(evento) {
  const datos = evento.datos || {};
  if (evento.tipo === "prueba_progreso") {
    laboratorio.alProgreso(datos);
    return;
  }
  laboratorio.alTerminar(datos);
  refrescarCorridasComparables();
}

function alEvento(evento) {
  if (evento.tipo.startsWith("prueba_")) {
    alEventoDePrueba(evento);
    agregarEvento(evento);
    return;
  }
  agregarEvento(evento);
  refrescar.auditoria();
  const datos = evento.datos || {};
  const id = datos.incidente_id;
  const dePrueba = datos.origen === "prueba";
  switch (evento.tipo) {
    case "alerta_recibida":
      if (!datos.agrupada && !dePrueba) seguirIncidente(id);
      if (!dePrueba) refrescar.bloqueados();
      break;
    case "explicacion_lista":
      if (!dePrueba) refrescar.bloqueados(); // "Requiere atención" muestra el veredicto recién explicado
      break;
    case "notificacion_enviada":
    case "notificacion_fallida":
      break;
    case "host_desbloqueado":
      refrescar.bloqueados();
      (datos.incidentes || []).forEach((i) => {
        if (i === incidenteDelFlujo()) seguirIncidente(i);
        if (i === detalleAbierto()) mostrarDetalle(i);
      });
      break;
    case "config_cambiada":
    case "modo_decision_cambiado":
      refrescar.estado();
      refrescar.umbrales();
      refrescar.politica();
      refrescar.resumen(); // estado del LLM en el tablero
      return;
    case "recomendacion_creada":
    case "recomendacion_resuelta":
      refrescar.recomendaciones();
      break;
    case "accion_ejecutada":
    case "accion_fallida":
      if (!datos.origen_incidente) refrescar.bloqueados();
      break;
    default:
      return;
  }
  refrescar.incidentes();
  refrescar.resumen();
  refrescar.actividad();
  if (id && id === incidenteDelFlujo()) seguirIncidente(id);
  if (id && id === detalleAbierto()) mostrarDetalle(id);
}

async function arrancarPanel() {
  $("acceso").hidden = true;
  if (!iniciado) {
    iniciarEstado();
    iniciarBloqueados();
    iniciarIncidentes();
    iniciarAuditoria();
    laboratorio.iniciarLaboratorio();
    pruebas.iniciarPruebas();
    iniciarComparar();
    iniciarPolitica();
    iniciarRecomendaciones();
    window.addEventListener("hashchange", enrutar);
    iniciado = true;
  }
  const resumen = await refrescarEstado();
  if (resumen && resumen.ultimo_incidente_id) seguirIncidente(resumen.ultimo_incidente_id);
  refrescarBloqueados();
  refrescarIncidentes();
  refrescarActividad();
  refrescarUmbrales();
  refrescarAuditoria();
  verificarCadena();
  enrutar();
  if (conexion) conexion.cerrar();
  conexion = conectarEventos({ alEvento, alEstado: marcarConexion });
}

function mostrarAcceso(texto = "") {
  if (conexion) {
    conexion.cerrar();
    conexion = null;
  }
  $("acceso").hidden = false;
  const error = $("error-acceso");
  error.hidden = !texto;
  if (texto) mensaje(error, texto, "error");
  $("campo-token").value = "";
  $("campo-token").focus();
}

$("form-acceso").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  token.guardar($("campo-token").value.trim());
  try {
    await api("/api/resumen"); // valida el token antes de entrar
    arrancarPanel();
  } catch (error) {
    if (!(error instanceof ErrorApi) || error.estado !== 401) mostrarAcceso(error.message);
  }
});

window.addEventListener("token-requerido", () => mostrarAcceso("El token no es válido o expiró. Ingrésalo de nuevo."));
$("btn-salir").addEventListener("click", () => { token.borrar(); mostrarAcceso(); });
$("btn-menu").addEventListener("click", () => {
  const abierto = document.body.classList.toggle("menu-abierto");
  $("btn-menu").setAttribute("aria-expanded", String(abierto));
});

if (token.leer()) arrancarPanel();
else mostrarAcceso();
