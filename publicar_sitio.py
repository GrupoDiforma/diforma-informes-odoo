#!/usr/bin/env python3
"""
Genera el sitio publico (carpeta site/) con el dashboard de ventas del año actual y del anterior.
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


def pagina(anio, datos, otros):
    js = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = da.PLANTILLA.replace("__ANIO__", str(anio)).replace("__DATOS__", js)
    nav = '<div class="small muted" style="margin-top:6px">Otros años: ' + " · ".join(
        f'<a href="{n}">{a}</a>' for a, n in otros) + "</div>" if otros else ""
    html = html.replace('<div class="sub" id="sub"></div>', '<div class="sub" id="sub"></div>' + nav, 1)
    return html.replace("<head>", '<head><meta name="robots" content="noindex,nofollow">', 1)


def main():
    iv.cargar_env()
    faltan = [v for v in ("ODOO_URL", "ODOO_DB", "ODOO_KEY") if not os.environ.get(v)]
    if faltan:
        sys.exit("Faltan los Secrets de GitHub: " + ", ".join(faltan))
    odoo = iv.Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])
    hoy = datetime.now().year
    SALIDA.mkdir(exist_ok=True)
    resultados = {}
    for anio in (hoy, hoy - 1):
        try:
            datos, _ = da.construir_datos(odoo, anio)
        except (RuntimeError, requests.RequestException) as e:
            if anio == hoy:
                sys.exit(f"Error consultando Odoo: {e}")
            print(f"Sin datos para {anio}: {e}")
            continue
        if datos is None:
            print(f"No hay facturas en {anio}; se omite.")
            continue
        resultados[anio] = datos
    if hoy not in resultados:
        sys.exit(f"No hay facturas publicadas en {hoy}.")
    nombres = {a: ("index.html" if a == hoy else f"ventas_{a}.html") for a in resultados}
    for anio, datos in resultados.items():
        otros = [(a, n) for a, n in nombres.items() if a != anio]
        if anio != hoy:
            otros = [(a, n) for a, n in nombres.items() if a != anio]
        (SALIDA / nombres[anio]).write_text(pagina(anio, datos, otros), encoding="utf-8")
        print("Generado:", SALIDA / nombres[anio])
    (SALIDA / ".nojekyll").write_text("", encoding="utf-8")


if __name__ == "__main__":
    main()
