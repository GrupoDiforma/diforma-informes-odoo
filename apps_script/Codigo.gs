/**
 * Metas compartidas del Dashboard de Indicadores (Grupo Diforma).
 * Guarda en la pestaña "Config" de esta hoja una fila por clave (las metas de cada área y año).
 *  - GET  ?accion=leer  -> {ok:true, datos:{clave: valorJSON}}   (público, solo lectura)
 *  - POST {pin, clave, valor} -> guarda; exige la clave de edición (propiedad de script PIN)
 */
var HOJA = 'Config';

function respuesta_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  try {
    var h = SpreadsheetApp.getActive().getSheetByName(HOJA);
    var n = h.getLastRow();
    var datos = {};
    if (n > 1) {
      h.getRange(2, 1, n - 1, 2).getValues().forEach(function (f) {
        if (f[0] && f[1]) datos[String(f[0])] = String(f[1]);
      });
    }
    return respuesta_({ ok: true, datos: datos });
  } catch (err) {
    return respuesta_({ ok: false, error: String(err) });
  }
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(20000);
    var p = JSON.parse(e.postData.contents);
    var pin = PropertiesService.getScriptProperties().getProperty('PIN');
    if (!pin) return respuesta_({ ok: false, error: 'falta configurar la propiedad PIN en el script' });
    if (String(p.pin) !== pin) return respuesta_({ ok: false, error: 'clave incorrecta' });
    var clave = String(p.clave || '');
    var valor = String(p.valor || '');
    if (!/^diforma_metas_[a-z_0-9]*$/.test(clave)) return respuesta_({ ok: false, error: 'clave no permitida' });
    if (valor.length > 40000) return respuesta_({ ok: false, error: 'valor demasiado grande' });
    JSON.parse(valor); // debe ser JSON válido
    var h = SpreadsheetApp.getActive().getSheetByName(HOJA);
    var n = Math.max(h.getLastRow(), 1);
    var fila = 0;
    if (n > 1) {
      var claves = h.getRange(2, 1, n - 1, 1).getValues();
      for (var i = 0; i < claves.length; i++) if (String(claves[i][0]) === clave) { fila = i + 2; break; }
    }
    if (!fila) fila = n + 1;
    h.getRange(fila, 1, 1, 3).setValues([[clave, valor, new Date()]]);
    return respuesta_({ ok: true });
  } catch (err) {
    return respuesta_({ ok: false, error: String(err) });
  } finally {
    try { lock.releaseLock(); } catch (x) {}
  }
}
