// Arranque del panel: acceso con token, rutas con hash y reparto de los eventos en vivo a cada sección.

import { api, ErrorApi, token } from "./api.js";
import { iniciarAuditoria, refrescarAuditoria, verificarCadena } from "./auditoria.js";
import { iniciarBloqueados, refrescarBloqueados } from "./bloqueados.js";
import { $, agrupar, mensaje } from "./dom.js";
import { iniciarEstado, marcarConexion, refrescarEstado, refrescarResumen } from "./estado.js";
import { detalleAbierto, iniciarIncidentes, mostrarDetalle, ocultarDetalle, refrescarIncidentes } from "./incidentes.js";
import { agregarEvento, incidenteDelFlujo, seguirIncidente } from "./timeline.js";
import { iniciarUmbrales, refrescarUmbrales } from "./umbrales.js";
import { conectarEventos } from "./ws.js";

let conexion = null;
let iniciado = false;

const refrescar = {
  bloqueados: agrupar(refrescarBloqueados),
  incidentes: agrupar(refrescarIncidentes),
  resumen: agrupar(refrescarResumen),
  auditoria: agrupar(refrescarAuditoria),
  estado: agrupar(refrescarEstado),
  umbrales: agrupar(refrescarUmbrales),
};

function enrutar() {
  const coincidencia = location.hash.match(/^#\/incidentes\/(\d+)$/);
  if (coincidencia) mostrarDetalle(Number(coincidencia[1]));
  else ocultarDetalle();
}

function alEvento(evento) {
  agregarEvento(evento);
  refrescar.auditoria();
  const datos = evento.datos || {};
  const id = datos.incidente_id;
  switch (evento.tipo) {
    case "alerta_recibida":
      if (!datos.agrupada) seguirIncidente(id);
      refrescar.bloqueados();
      break;
    case "explicacion_lista":
    case "notificacion_enviada":
    case "notificacion_fallida":
      break;
    case "host_desbloqueado":
      refrescar.bloqueados();
      (datos.incidentes || []).forEach((i) => { if (i === incidenteDelFlujo()) seguirIncidente(i); });
      break;
    case "config_cambiada":
      refrescar.estado();
      refrescar.umbrales();
      return;
    default:
      return;
  }
  refrescar.incidentes();
  refrescar.resumen();
  if (id && id === incidenteDelFlujo()) seguirIncidente(id);
  if (id && id === detalleAbierto()) mostrarDetalle(id);
}

async function arrancarPanel() {
  $("acceso").hidden = true;
  if (!iniciado) {
    iniciarEstado();
    iniciarBloqueados();
    iniciarIncidentes();
    iniciarUmbrales();
    iniciarAuditoria();
    window.addEventListener("hashchange", enrutar);
    iniciado = true;
  }
  const resumen = await refrescarEstado();
  if (resumen && resumen.ultimo_incidente_id) seguirIncidente(resumen.ultimo_incidente_id);
  refrescarBloqueados();
  refrescarIncidentes();
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

if (token.leer()) arrancarPanel();
else mostrarAcceso();
