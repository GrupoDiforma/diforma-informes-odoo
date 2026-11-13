#!/usr/bin/env python3
"""
Vista previa local del dashboard con datos reales de Odoo (solo lectura).

  python servidor_local.py            -> http://localhost:8000

- Al arrancar genera site/index.html con publicar_sitio.py (usa conexion.txt, que NO se sube al repo).
- Vigila los .py y .json del repo: si cambian, regenera solo.
- La pagina servida trae un pequeño script que recarga el navegador cuando hay una version nueva
  (el script se agrega al vuelo; no queda en site/index.html ni en lo publicado).
- Para regenerar a mano (p. ej. para traer datos frescos de Odoo): http://localhost:8000/__regenerar
"""
import http.server
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
SITE = RAIZ / "site"
PUERTO = int(os.environ.get("PORT", "8000"))

estado = {"version": 0, "generando": False, "error": ""}
candado = threading.Lock()

RECARGA = """<script>(function(){var v=null;setInterval(function(){fetch('/__version').then(function(r){return r.json()})
.then(function(d){if(v===null)v=d.version;else if(d.version!==v)location.reload();
document.title=(d.generando?'⏳ ':'')+document.title.replace(/^⏳ /,'');
if(d.error)console.error('Error generando el dashboard:\\n'+d.error);}).catch(function(){});},1500);})();</script>"""


def generar():
    with candado:
        if estado["generando"]:
            return
        estado["generando"] = True
    inicio = time.time()
    print("Generando dashboard desde Odoo...", flush=True)
    try:
        r = subprocess.run([sys.executable, "publicar_sitio.py"], cwd=RAIZ, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except Exception as e:  # noqa: BLE001
        r = subprocess.CompletedProcess([], 1, "", str(e))
    salida = (r.stdout + r.stderr).strip()
    if salida:
        print(salida, flush=True)
    with candado:
        estado["generando"] = False
        estado["error"] = "" if r.returncode == 0 else salida[-3000:]
        if r.returncode == 0:
            estado["version"] += 1
    print(f"{'Listo' if r.returncode == 0 else 'FALLO'} en {time.time() - inicio:.0f} s", flush=True)


def vigilar():
    def firma():
        return {p: p.stat().st_mtime for p in RAIZ.glob("*") if p.suffix in (".py", ".json")}
    previa = firma()
    while True:
        time.sleep(1.5)
        actual = firma()
        if actual != previa:
            cambios = [p.name for p in actual if previa.get(p) != actual[p]]
            print("Cambio en:", ", ".join(cambios) or "(archivo borrado)", flush=True)
            previa = actual
            generar()


class Manejador(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(SITE), **k)

    def _json(self, texto):
        datos = texto.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self):
        import json
        if self.path == "/__version":
            return self._json(json.dumps(estado))
        if self.path == "/__regenerar":
            threading.Thread(target=generar, daemon=True).start()
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if self.path in ("/", "/index.html"):
            archivo = SITE / "index.html"
            if not archivo.exists():
                html = "<p>Generando el dashboard desde Odoo, espere...</p>" + RECARGA
            else:
                html = archivo.read_text(encoding="utf-8").replace("</body>", RECARGA + "</body>", 1)
            datos = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)
            return
        return super().do_GET()

    def log_message(self, fmt, *args):
        if "/__version" not in str(args[0] if args else ""):
            super().log_message(fmt, *args)


def main():
    for flujo in (sys.stdout, sys.stderr):
        flujo.reconfigure(encoding="utf-8", errors="replace")
    if not (RAIZ / "conexion.txt").exists() and not os.environ.get("ODOO_KEY"):
        sys.exit("Falta conexion.txt (ODOO_URL=, ODOO_DB=, ODOO_KEY=) en la carpeta del repo.")
    SITE.mkdir(exist_ok=True)
    threading.Thread(target=generar, daemon=True).start()
    threading.Thread(target=vigilar, daemon=True).start()
    print(f"Vista previa en http://localhost:{PUERTO}", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", PUERTO), Manejador).serve_forever()


if __name__ == "__main__":
    main()
