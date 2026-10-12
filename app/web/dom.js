// Utilidades de DOM y tiempo. TODO texto del servidor entra como nodo de texto (nunca como HTML).

export function el(etiqueta, atributos = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  for (const [clave, valor] of Object.entries(atributos)) {
    if (valor === null || valor === undefined || valor === false) continue;
    if (clave === "class") nodo.className = valor;
    else if (clave.startsWith("on")) nodo.addEventListener(clave.slice(2), valor);
    else nodo.setAttribute(clave, valor === true ? "" : String(valor));
  }
  poner(nodo, ...hijos);
  return nodo;
}

// Agrega hijos: los nodos tal cual; cualquier otro valor como texto.
export function poner(nodo, ...hijos) {
  for (const hijo of hijos.flat(Infinity)) { // listas anidadas de nodos a cualquier profundidad
    if (hijo === null || hijo === undefined || hijo === false) continue;
    nodo.append(hijo instanceof Node ? hijo : document.createTextNode(String(hijo)));
  }
  return nodo;
}

export function vaciar(nodo, ...hijos) {
  nodo.replaceChildren();
  return poner(nodo, ...hijos);
}

export function $(id) {
  return document.getElementById(id);
}

// Hora local del navegador; la UTC queda en el title (al pasar el mouse).
export function hora(iso) {
  if (!iso) return el("span", {}, "—");
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return el("span", {}, String(iso));
  return el("span", { title: `${fecha.toISOString()} (UTC)` }, fecha.toLocaleString());
}

export function msEntre(desde, hasta) {
  if (!desde || !hasta) return null;
  return new Date(hasta).getTime() - new Date(desde).getTime();
}

export function duracion(ms) {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s (${ms} ms)`;
}

export function mensaje(nodo, texto, tipo = "") {
  nodo.className = `mensaje ${tipo}`.trim();
  vaciar(nodo, texto);
}

export function chip(texto, clase = "") {
  return el("span", { class: `chip ${clase || texto || ""}`.trim() }, texto ?? "—");
}

// Agrupa llamadas repetidas en una sola (evita refrescar 3 veces seguidas por 3 eventos).
export function agrupar(funcion, espera = 300) {
  let temporizador = null;
  return (...argumentos) => {
    clearTimeout(temporizador);
    temporizador = setTimeout(() => funcion(...argumentos), espera);
  };
}

// Badge de estado (severidad, notificación…) y texto monoespaciado (IPs, IDs, tiempos, métricas).
export function badge(texto, clase = "") {
  return el("span", { class: `badge ${clase}`.trim() }, texto ?? "—");
}

export function mono(texto) {
  return el("span", { class: "mono" }, texto ?? "—");
}
