// Acceso a la API del agente con X-Operador-Token. Un 401 borra el token y pide uno nuevo.

const CLAVE_TOKEN = "sdn_operador_token";

export const token = {
  leer() {
    try { return sessionStorage.getItem(CLAVE_TOKEN) || ""; } catch { return ""; }
  },
  guardar(valor) {
    try { sessionStorage.setItem(CLAVE_TOKEN, valor); } catch { /* sin almacenamiento: solo esta pestaña */ }
  },
  borrar() {
    try { sessionStorage.removeItem(CLAVE_TOKEN); } catch { /* nada que borrar */ }
  },
};

export class ErrorApi extends Error {
  constructor(estado, detalle) {
    super(detalle);
    this.estado = estado;
  }
}

export function pedirToken() {
  token.borrar();
  window.dispatchEvent(new Event("token-requerido"));
}

function textoDetalle(datos, estado) {
  const detalle = datos && datos.detail;
  if (typeof detalle === "string") return detalle;
  if (Array.isArray(detalle)) return detalle.map((e) => e.msg || JSON.stringify(e)).join("; ");
  return `HTTP ${estado}`;
}

export async function api(ruta, { metodo = "GET", cuerpo } = {}) {
  const cabeceras = { "X-Operador-Token": token.leer() };
  if (cuerpo !== undefined) cabeceras["Content-Type"] = "application/json";
  let respuesta;
  try {
    respuesta = await fetch(ruta, {
      method: metodo,
      headers: cabeceras,
      body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
    });
  } catch {
    throw new ErrorApi(0, "No se pudo contactar al agente");
  }
  if (respuesta.status === 401) {
    pedirToken();
    throw new ErrorApi(401, "Token de operador inválido");
  }
  let datos = null;
  try { datos = await respuesta.json(); } catch { datos = null; }
  if (!respuesta.ok) throw new ErrorApi(respuesta.status, textoDetalle(datos, respuesta.status));
  return datos;
}
