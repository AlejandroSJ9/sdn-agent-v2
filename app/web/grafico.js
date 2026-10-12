// Mini-gráfico de incidentes por hora (últimas 24 h) en SVG propio, sin librerías.
// Una sola serie: barras finas con extremo redondeado, tooltip por barra (<title>) y texto solo como textContent.

const NS = "http://www.w3.org/2000/svg";
const ANCHO = 320;
const ALTO = 130;
const MARGEN = { arriba: 10, abajo: 26, izquierda: 22, derecha: 4 };

function svg(etiqueta, atributos = {}, texto = null) {
  const nodo = document.createElementNS(NS, etiqueta);
  for (const [clave, valor] of Object.entries(atributos)) nodo.setAttribute(clave, String(valor));
  if (texto !== null) nodo.textContent = texto;
  return nodo;
}

function horaLocal(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Barra con los extremos de arriba redondeados (4 px) y la base plana sobre el eje.
function barra(x, y, ancho, alto) {
  const r = Math.min(4, ancho / 2, alto);
  return `M${x},${y + alto}V${y + r}Q${x},${y} ${x + r},${y}H${x + ancho - r}Q${x + ancho},${y} ${x + ancho},${y + r}V${y + alto}Z`;
}

export function dibujarPorHora(contenedor, franjas) {
  const maximo = Math.max(1, ...franjas.map((f) => f.cantidad));
  const total = franjas.reduce((suma, f) => suma + f.cantidad, 0);
  const alto = ALTO - MARGEN.arriba - MARGEN.abajo;
  const paso = (ANCHO - MARGEN.izquierda - MARGEN.derecha) / franjas.length;
  const base = MARGEN.arriba + alto;
  const grafico = svg("svg", { viewBox: `0 0 ${ANCHO} ${ALTO}`, role: "img", class: "svg-horas" });
  grafico.append(svg("title", {}, `Incidentes por hora, últimas 24 h: ${total} en total, máximo ${maximo} en una hora.`));

  // Guías recesivas: base y máximo
  [[0, base], [maximo, MARGEN.arriba]].forEach(([valor, y]) => {
    grafico.append(svg("line", { x1: MARGEN.izquierda, x2: ANCHO - MARGEN.derecha, y1: y, y2: y, class: "guia" }));
    grafico.append(svg("text", { x: MARGEN.izquierda - 6, y: y + 4, class: "eje", "text-anchor": "end" }, String(valor)));
  });

  franjas.forEach((franja, i) => {
    const x = MARGEN.izquierda + i * paso;
    const grupo = svg("g", { class: "franja" });
    const texto = `${horaLocal(franja.hora)} · ${franja.cantidad} incidente${franja.cantidad === 1 ? "" : "s"}`;
    grupo.append(svg("title", {}, texto));
    grupo.append(svg("rect", { x, y: MARGEN.arriba, width: paso, height: alto, class: "zona" })); // área de hover mayor que la barra
    if (franja.cantidad > 0) {
      const h = Math.max(2, (franja.cantidad / maximo) * alto);
      grupo.append(svg("path", { d: barra(x + 1, base - h, Math.max(paso - 2, 1), h), class: "dato" })); // 2 px de separación
    }
    if (i % 8 === 0 || i === franjas.length - 1) {
      grupo.append(svg("text", { x: x + paso / 2, y: ALTO - 6, class: "eje", "text-anchor": "middle" }, horaLocal(franja.hora)));
    }
    grafico.append(grupo);
  });
  contenedor.replaceChildren(grafico);
}
