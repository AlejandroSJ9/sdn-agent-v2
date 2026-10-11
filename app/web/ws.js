// WebSocket de eventos C5. El token va en el PRIMER mensaje (nunca en la URL).
// Reconexión con espera creciente (1 s, 2 s, 4 s… máx. 30 s) y keepalive ping/pong.

import { pedirToken, token } from "./api.js";

const ESPERA_MAXIMA = 30000;
const CADA_PING = 25000;
const ESPERA_PONG = 10000;

export function conectarEventos({ alEvento, alEstado }) {
  let socket = null;
  let espera = 1000;
  let temporizadorPing = null;
  let temporizadorPong = null;
  let cerradoAPropósito = false;

  function detenerPing() {
    clearInterval(temporizadorPing);
    clearTimeout(temporizadorPong);
  }

  function iniciarPing() {
    detenerPing();
    temporizadorPing = setInterval(() => {
      if (socket.readyState !== WebSocket.OPEN) return;
      socket.send(JSON.stringify({ control: "ping" }));
      temporizadorPong = setTimeout(() => socket.close(), ESPERA_PONG); // sin pong: se reconecta
    }, CADA_PING);
  }

  function abrir() {
    const protocolo = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${protocolo}://${location.host}/ws/eventos`);
    socket.onopen = () => socket.send(JSON.stringify({ token: token.leer() }));
    socket.onmessage = (mensaje) => {
      let datos;
      try { datos = JSON.parse(mensaje.data); } catch { return; }
      if (datos.control === "listo") {
        espera = 1000;
        alEstado({ conectado: true });
        iniciarPing();
      } else if (datos.control === "pong") {
        clearTimeout(temporizadorPong);
      } else if (typeof datos.tipo === "string") {
        alEvento(datos);
      }
    };
    socket.onclose = (cierre) => {
      detenerPing();
      if (cerradoAPropósito) return;
      if (cierre.code === 1008) {
        alEstado({ conectado: false, reintentoEn: null });
        pedirToken();
        return;
      }
      alEstado({ conectado: false, reintentoEn: espera });
      setTimeout(abrir, espera);
      espera = Math.min(espera * 2, ESPERA_MAXIMA);
    };
  }

  abrir();
  return {
    cerrar() {
      cerradoAPropósito = true;
      detenerPing();
      if (socket) socket.close();
    },
  };
}
