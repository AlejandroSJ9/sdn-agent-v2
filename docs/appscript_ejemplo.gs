/**
 * Ejemplo mínimo del web app de Apps Script que recibe el payload C3 del agente y envía el correo.
 * Solo referencia: el agente no depende de este archivo.
 *
 * Publicar: Implementar > Nueva implementación > Aplicación web
 *   - Ejecutar como: yo   - Quién tiene acceso: cualquier usuario
 * Copiar la URL /exec en APPSCRIPT_URL del .env del agente.
 */

// Destinatarios del correo (separados por coma en el envío).
var DESTINATARIOS = ["operador1@ejemplo.com", "operador2@ejemplo.com"];

function doPost(e) {
  try {
    var datos = JSON.parse(e.postData.contents);
    var cuerpo = datos.mensaje + "\n\nPanel: " + datos.enlace_panel;
    MailApp.sendEmail(DESTINATARIOS.join(","), datos.asunto, cuerpo); // texto plano, sin HTML
    return responder({ ok: true });
  } catch (error) {
    return responder({ ok: false, error: String(error) });
  }
}

function responder(objeto) {
  return ContentService.createTextOutput(JSON.stringify(objeto)).setMimeType(ContentService.MimeType.JSON);
}
