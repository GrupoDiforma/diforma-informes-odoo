#!/usr/bin/env python3
"""
Genera el sitio publico (carpeta site/): un unico index.html "Dashboard de Indicadores" con
selector de año (año actual y anterior si hay datos), periodos, empresa y pestañas por area
(comercial y administrativa).
Lo ejecuta GitHub Actions con los datos de conexion guardados como Secrets (nunca en el repositorio).
Solo lee datos de Odoo.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dashboard_anual as da  # noqa: E402
import informe_ventas as iv  # noqa: E402

SALIDA = Path("site")


def pagina(multi):
    js = json.dumps(multi, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = da.PLANTILLA.replace("__ANIO__", str(multi["anio_defecto"])).replace("__DATOS__", js)
    return html.replace("<head>", '<head><meta name="robots" content="noindex,nofollow">', 1)


def main():
    iv.cargar_env()
    faltan = [v for v in ("ODOO_URL", "ODOO_DB", "ODOO_KEY") if not os.environ.get(v)]
    if faltan:
        sys.exit("Faltan los Secrets de GitHub: " + ", ".join(faltan))
    odoo = iv.Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])
    hoy = datetime.now().year
    try:
        multi, avisos = da.construir_multi(odoo, hoy)
    except (RuntimeError, requests.RequestException) as e:
        sys.exit(f"Error consultando Odoo: {e}")
    if multi is None:
        sys.exit(f"No hay facturas publicadas en {hoy}.")
    for a in avisos:
        print("Aviso:", a)
    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "index.html").write_text(pagina(multi), encoding="utf-8")
    (SALIDA / ".nojekyll").write_text("", encoding="utf-8")
    print("Generado:", SALIDA / "index.html", "- años:", multi["anios"])


if __name__ == "__main__":
    main()
