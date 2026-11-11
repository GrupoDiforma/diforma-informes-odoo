#!/usr/bin/env python3
"""
Informe mensual de ventas - Grupo Diforma SAS (Odoo 19, API JSON-2)

FUENTE DE DATOS: asientos contables de las FACTURAS DE CLIENTE publicadas (y notas credito,
que restan), no los pedidos de venta. El valor oficial es el COP del asiento contable
(moneda de la compania). El USD es el valor de la factura cuando fue emitida en USD; en
facturas emitidas en COP se convierte con la TRM de la tabla de tasas de Odoo a la fecha
de la factura.

Uso:
    python informe_ventas.py                  # informe del mes anterior
    python informe_ventas.py --mes 2026-10    # informe de un mes especifico
    python informe_ventas.py --diagnostico    # revisa la conexion y muestra totales para cuadrar con Odoo

Credenciales: variables de entorno o archivo "conexion.txt" (o ".env") junto a este script.
Solo LEE datos de Odoo. No crea ni modifica nada.
"""
import argparse
import base64
import html
import io
import os
import sys
from datetime import date, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------
TIPOS_VENTA = ["out_invoice", "out_refund"]  # facturas de cliente y notas credito
TRM_MIN, TRM_MAX = 500, 20000  # rango creible de la TRM (COP por USD)
COLOR = "#1f6f8b"
COLOR_2 = "#e08e45"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def cargar_env():
    """Lee un archivo sencillo (CLAVE=valor) junto al script: conexion.txt o .env"""
    for nombre in ("conexion.txt", ".env"):
        ruta = Path(__file__).with_name(nombre)
        if not ruta.exists():
            continue
        for linea in ruta.read_text(encoding="utf-8-sig").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# --------------------------------------------------------------------------
# Cliente Odoo (JSON-2)
# --------------------------------------------------------------------------
class Odoo:
    def __init__(self, url, db, key):
        url = url.strip().rstrip("/")
        if not url.startswith("http"):
            url = "https://" + url
        self.base = url + "/json/2"
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"bearer {key}",
            "Content-Type": "application/json; charset=utf-8",
            "X-Odoo-Database": db,
            "User-Agent": "diforma-informes/2.0",
        })

    def call(self, model, method, **params):
        r = self.s.post(f"{self.base}/{model}/{method}", json=params, timeout=300)
        if not r.ok:
            try:
                detalle = r.json().get("message") or r.text
            except Exception:
                detalle = r.text
            if r.status_code in (401, 403):
                detalle = "Clave API invalida, vencida o sin permisos. " + str(detalle)[:300]
            raise RuntimeError(f"Odoo respondio {r.status_code} en {model}/{method}: {str(detalle)[:500]}")
        return r.json()

    def search_read(self, model, domain, fields, order=None, lote=2000):
        """Trae todos los registros, de a lotes."""
        filas, offset = [], 0
        while True:
            params = dict(domain=domain, fields=fields, offset=offset, limit=lote)
            if order:
                params["order"] = order
            bloque = self.call(model, "search_read", **params)
            filas.extend(bloque)
            if len(bloque) < lote:
                return filas
            offset += lote


def m2o(valor, defecto="(sin dato)"):
    """Odoo devuelve los campos many2one como [id, 'nombre'] o False."""
    return valor[1] if isinstance(valor, (list, tuple)) and len(valor) > 1 else defecto


def m2o_id(valor):
    return valor[0] if isinstance(valor, (list, tuple)) and valor else None


# --------------------------------------------------------------------------
# Fechas
# --------------------------------------------------------------------------
def sumar_meses(anio, mes, n):
    t = anio * 12 + (mes - 1) + n
    return t // 12, t % 12 + 1


def nombre_mes(anio, mes):
    return f"{MESES[mes - 1]} de {anio}"


# --------------------------------------------------------------------------
# Formato (estilo colombiano: punto para miles)
# --------------------------------------------------------------------------
def n0(v):
    return f"{v:,.0f}".replace(",", ".")


def usd(v):
    return "US$ " + n0(v)


def cop(v):
    return "COP " + n0(v)


def miles_usd(v):
    s = f"{v / 1e3:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"US$ {s} k"


def variacion(actual, previo):
    if not previo:
        return None
    return (actual - previo) / previo * 100


def texto_var(v):
    if v is None:
        return "<span class='muted'>sin base de comparacion</span>"
    clase = "up" if v >= 0 else "down"
    flecha = "▲" if v >= 0 else "▼"
    return f"<span class='{clase}'>{flecha} {abs(v):.1f}%</span>"


# --------------------------------------------------------------------------
# Extraccion de datos: facturas de cliente contabilizadas
# --------------------------------------------------------------------------
def tabla_trm(odoo, desde):
    """TRM (COP por 1 USD) desde las tasas de Odoo: (para compania en COP, para compania en USD)."""
    d = (pd.Timestamp(desde) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    filas = odoo.search_read("res.currency.rate",
                             [["currency_id.name", "in", ["USD", "COP"]], ["name", ">=", d]],
                             ["name", "rate", "currency_id"], order="name asc")
    vacio = pd.DataFrame({"f": pd.Series(dtype="datetime64[ns]"), "trm": pd.Series(dtype="float64")})
    if not filas:
        return vacio, vacio
    df = pd.DataFrame(filas)
    df["f"] = pd.to_datetime(df["name"]).astype("datetime64[ns]")
    df["mon"] = df["currency_id"].map(m2o)
    df = df[df["rate"] > 0]

    def serie(mon, f):
        x = df[df["mon"] == mon].drop_duplicates("f", keep="last").sort_values("f")
        return pd.DataFrame({"f": x["f"], "trm": x["rate"].map(f)}) if len(x) else vacio

    # Compania en COP: la tasa del USD es "USD por 1 COP"  ->  TRM = 1 / tasa
    # Compania en USD: la tasa del COP es "COP por 1 USD"  ->  TRM = tasa
    return serie("USD", lambda r: 1.0 / r), serie("COP", lambda r: r)


def trm_cercana(fechas, tabla):
    if tabla.empty:
        return np.full(len(fechas), np.nan)
    izq = pd.DataFrame({"f": fechas.dt.normalize().astype("datetime64[ns]").to_numpy()}).reset_index()
    izq = izq.sort_values("f")
    r = pd.merge_asof(izq, tabla, on="f", direction="nearest").sort_values("index")
    return r["trm"].to_numpy()


def campos_existentes(odoo, modelo, deseados):
    try:
        info = odoo.call(modelo, "fields_get", allfields=deseados, attributes=["type"])
        return [c for c in deseados if c in info]
    except RuntimeError:
        return [c for c in deseados if c == "move_type"]


def traer_encabezados(odoo, ids):
    extra = campos_existentes(odoo, "account.move",
                              ["invoice_user_id", "team_id", "commercial_partner_id", "move_type"])
    campos = ["name", "partner_id"] + extra
    filas = []
    for i in range(0, len(ids), 500):
        filas += odoo.call("account.move", "read", ids=ids[i:i + 500], fields=campos)
    h = pd.DataFrame(filas)
    cp = h["commercial_partner_id"] if "commercial_partner_id" in h else h["partner_id"]
    cliente = [m2o(c) if m2o_id(c) else m2o(p) for c, p in zip(cp, h["partner_id"])]
    return pd.DataFrame({
        "move_pk": h["id"],
        "cliente": cliente,
        "vendedor": h["invoice_user_id"].map(lambda v: m2o(v, "Sin vendedor")) if "invoice_user_id" in h else "Sin vendedor",
        "equipo": h["team_id"].map(lambda v: m2o(v, "Sin equipo")) if "team_id" in h else "Sin equipo",
        "tipo": h["move_type"] if "move_type" in h else "out_invoice",
    })


def traer_facturacion(odoo, desde, hasta, avisos):
    """
    Lineas de producto de facturas de cliente publicadas, con fecha contable en [desde, hasta).
    Devuelve un DataFrame con una fila por linea y columnas:
    fecha, empresa, cliente, vendedor, equipo, factura, tipo, producto, categoria, moneda, usd, cop, trm_origen.
    cop = valor contable (moneda de la compania). usd = valor en USD (ver docstring del modulo).
    """
    dominio = [["parent_state", "=", "posted"],
               ["move_id.move_type", "in", TIPOS_VENTA],
               ["display_type", "=", "product"],
               ["date", ">=", desde.isoformat()],
               ["date", "<", hasta.isoformat()]]
    campos = ["move_id", "date", "product_id", "balance", "amount_currency", "currency_id",
              "company_id", "company_currency_id"]
    df = pd.DataFrame(odoo.search_read("account.move.line", dominio, campos, order="date asc"))
    if df.empty:
        return df
    df["fecha"] = pd.to_datetime(df["date"])
    df["empresa"] = df["company_id"].map(m2o)
    df["moneda"] = df["currency_id"].map(lambda v: m2o(v, "?"))
    df["moneda_comp"] = df["company_currency_id"].map(lambda v: m2o(v, ""))
    df["move_pk"] = df["move_id"].map(m2o_id)
    df["factura"] = df["move_id"].map(m2o)
    df = df.merge(traer_encabezados(odoo, sorted(set(df["move_pk"]))), on="move_pk", how="left")
    df["producto"] = df["product_id"].map(lambda v: m2o(v, "(sin producto)"))

    ids = sorted({m2o_id(v) for v in df["product_id"] if m2o_id(v)})
    categorias = {}
    if ids:
        prods = []
        for i in range(0, len(ids), 500):
            prods += odoo.call("product.product", "read", ids=ids[i:i + 500], fields=["categ_id"],
                               context={"active_test": False})
        categorias = {p["id"]: m2o(p["categ_id"], "Sin categoria") for p in prods}
    df["categoria"] = df["product_id"].map(lambda v: categorias.get(m2o_id(v), "Sin categoria"))

    # --- Conversion de moneda ------------------------------------------------
    bal = -df["balance"].astype(float).to_numpy()            # valor contable, positivo = venta
    amt = -df["amount_currency"].astype(float).to_numpy()    # valor en la moneda de la factura
    mon = df["moneda"].to_numpy()
    comp = df["moneda_comp"].to_numpy()
    es_cop, es_usd = comp == "COP", comp == "USD"
    t_cop_comp, t_usd_comp = tabla_trm(odoo, desde)
    trm_t = np.where(es_cop, trm_cercana(df["fecha"], t_cop_comp),
                     np.where(es_usd, trm_cercana(df["fecha"], t_usd_comp), np.nan))
    usd_v = np.full(len(df), np.nan)
    cop_v = np.full(len(df), np.nan)
    origen = np.array(["factura"] * len(df), dtype=object)

    cop_v[es_cop] = bal[es_cop]                                  # compania en COP
    m = es_cop & (mon == "USD")
    usd_v[m] = amt[m]
    m = es_cop & (mon != "USD")
    usd_v[m] = bal[m] / trm_t[m]
    origen[m] = "tabla"
    usd_v[es_usd] = bal[es_usd]                                  # compania en USD
    m = es_usd & (mon == "COP")
    cop_v[m] = amt[m]
    m = es_usd & (mon != "COP")
    cop_v[m] = bal[m] * trm_t[m]
    origen[m] = "tabla"

    # TRM implicita de la factura fuera de rango creible -> usar la tabla de tasas
    with np.errstate(divide="ignore", invalid="ignore"):
        implicita = np.where(np.abs(usd_v) > 0.005, cop_v / usd_v, np.nan)
    par = (es_cop & (mon == "USD")) | (es_usd & (mon == "COP"))
    malas = par & (np.abs(usd_v) > 0.005) & ~((implicita >= TRM_MIN) & (implicita <= TRM_MAX))
    if malas.any():
        corr_usd = np.where(es_cop, bal / trm_t, usd_v)
        corr_cop = np.where(es_usd, bal * trm_t, cop_v)
        usd_v = np.where(malas, corr_usd, usd_v)
        cop_v = np.where(malas, corr_cop, cop_v)
        origen[malas] = "tabla (TRM de la factura invalida)"
        avisos.append(f"{df.loc[malas, 'factura'].nunique()} factura(s) en USD traian una TRM invalida "
                      "(valor contable en COP sin relacion con el USD); para esas se uso la TRM de la tabla de "
                      "tasas de Odoo a la fecha de la factura. El COP (valor contable) no se altera. "
                      "Revise la columna 'Origen TRM' en la hoja Facturas del Excel.")
    df["usd"], df["cop"], df["trm_origen"] = usd_v, cop_v, origen

    sin = df["usd"].isna() | df["cop"].isna()
    if sin.any():
        avisos.append(f"{df.loc[sin, 'factura'].nunique()} factura(s) no se pudieron convertir (sin TRM o moneda "
                      "de compania no soportada) y quedaron fuera: " + ", ".join(df.loc[sin, "factura"].drop_duplicates().head(10)))
        df = df[~sin].copy()
    return df


def nfacturas(df):
    return int(df.loc[df["tipo"] == "out_invoice", "factura"].nunique()) if len(df) else 0


# --------------------------------------------------------------------------
# Graficas
# --------------------------------------------------------------------------
def a_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def estilo(ax):
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.grid(axis="y", alpha=0.25)
    ax.set_axisbelow(True)


def grafica_barras_h(serie, titulo, n=10):
    serie = serie.sort_values(ascending=False).head(n)[::-1]
    fig, ax = plt.subplots(figsize=(8, max(2.2, 0.42 * len(serie) + 1)))
    ax.barh([s[:45] for s in serie.index], serie.values, color=COLOR)
    for i, v in enumerate(serie.values):
        ax.text(max(v, 0), i, " " + miles_usd(v), va="center", fontsize=8)
    ax.set_title(titulo, loc="left", fontsize=11, fontweight="bold")
    ax.xaxis.set_visible(False)
    for lado in ("top", "right", "bottom"):
        ax.spines[lado].set_visible(False)
    ax.set_xlim(min(0, serie.min()), max(serie.max(), 1) * 1.3)
    return a_base64(fig)


def grafica_diaria(df, anio, mes):
    ndias = (date(*sumar_meses(anio, mes, 1), 1) - date(anio, mes, 1)).days
    por_dia = df.groupby(df["fecha"].dt.day)["usd"].sum().reindex(range(1, ndias + 1), fill_value=0)
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.bar(por_dia.index, por_dia.values / 1e3, color=COLOR)
    ax.set_ylabel("Miles de USD por dia")
    ax.set_xlabel("Dia del mes")
    ax.set_title("Ventas facturadas por dia y acumulado (USD)", loc="left", fontsize=11, fontweight="bold")
    estilo(ax)
    ax2 = ax.twinx()
    ax2.plot(por_dia.index, por_dia.cumsum().values / 1e3, color=COLOR_2, linewidth=2)
    ax2.set_ylabel("Acumulado (miles de USD)", color=COLOR_2)
    ax2.spines["top"].set_visible(False)
    return a_base64(fig)


def grafica_evolucion(mensual_usd, anio, mes):
    claves = [sumar_meses(anio, mes, -k) for k in range(11, -1, -1)]
    etiquetas = [f"{MESES[m - 1][:3]} {str(a)[2:]}" for a, m in claves]
    valores = [mensual_usd.get(k, 0) / 1e3 for k in claves]
    fig, ax = plt.subplots(figsize=(9, 3.6))
    colores = [COLOR_2 if k == (anio, mes) else COLOR for k in claves]
    ax.bar(etiquetas, valores, color=colores)
    for i, v in enumerate(valores):
        ax.text(i, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=7)
    ax.set_ylabel("Miles de USD (sin IVA)")
    ax.set_title("Evolucion de ventas facturadas en USD, ultimos 12 meses", loc="left", fontsize=11, fontweight="bold")
    estilo(ax)
    plt.setp(ax.get_xticklabels(), fontsize=8)
    return a_base64(fig)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
def agrupar(df, col):
    return df.groupby(col)[["usd", "cop"]].sum().sort_values("usd", ascending=False)


def tabla_html(g, col1, total_usd, n=10):
    g = g.head(n)
    filas = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td class='num'>{usd(r.usd)}</td><td class='num'>{cop(r.cop)}</td>"
        f"<td class='num'>{(r.usd / total_usd * 100 if total_usd else 0):.1f}%</td></tr>"
        for k, r in g.iterrows())
    return (f"<table><thead><tr><th>{col1}</th><th class='num'>Ventas sin IVA (USD)</th>"
            f"<th class='num'>Valor contable (COP)</th><th class='num'>% del total</th></tr></thead>"
            f"<tbody>{filas}</tbody></table>")


CSS = """
body{font-family:Segoe UI,Arial,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#222}
h1{margin-bottom:0}h2{margin-top:36px;border-bottom:2px solid #e5e5e5;padding-bottom:4px}
.sub{color:#666;margin-top:4px}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}
.kpi{background:#f4f7f9;border-radius:8px;padding:12px 14px}
.kpi .v{font-size:22px;font-weight:700}.kpi .l{font-size:12px;color:#666}
.cmp{font-size:12px;margin-top:4px}
.up{color:#1a7f37}.down{color:#c62828}.muted{color:#888}
table{border-collapse:collapse;width:100%;font-size:14px;margin:10px 0}
th,td{padding:6px 8px;border-bottom:1px solid #e5e5e5;text-align:left}
th{background:#f4f7f9}.num{text-align:right}
img{max-width:100%}
.aviso{background:#fff4e5;border-left:4px solid #e08e45;padding:8px 12px;margin:14px 0;font-size:13px}
.nota{font-size:12px;color:#777;margin-top:30px}
"""

NOTA_FUENTE = ("Cifras sin IVA de facturas de cliente publicadas; las notas credito restan. Fecha contable del asiento. "
               "El valor contable en COP es el oficial (moneda de la compania). El USD es el valor de la factura cuando "
               "fue emitida en USD; en facturas emitidas en COP se convierte con la TRM de la tabla de tasas de Odoo a la "
               "fecha de la factura. Fuente: Odoo 19, solo lectura.")


def generar_html(anio, mes, k, graficos, tablas, avisos):
    def kpi(titulo, valor, comparativos):
        cmps = "".join(f"<div class='cmp'>{c}</div>" for c in comparativos)
        return f"<div class='kpi'><div class='l'>{titulo}</div><div class='v'>{valor}</div>{cmps}</div>"

    kpis = "".join([
        kpi("Ventas facturadas sin IVA (USD)", usd(k["usd"]),
            [f"vs mes anterior: {texto_var(k['var_mes_usd'])}", f"vs mismo mes {anio - 1}: {texto_var(k['var_anio_usd'])}"]),
        kpi("Valor contable (COP)", cop(k["cop"]),
            [f"vs mes anterior: {texto_var(k['var_mes_cop'])}", f"vs mismo mes {anio - 1}: {texto_var(k['var_anio_cop'])}"]),
        kpi("TRM promedio ponderada", cop(k["trm_prom"]),
            [f"mes anterior: {cop(k['trm_prev'])}" if k["trm_prev"] else "sin mes anterior"]),
        kpi("Facturas emitidas", n0(k["facturas"]), [f"mes anterior: {n0(k['facturas_prev'])}"]),
        kpi("Ticket promedio por factura (USD)", usd(k["ticket"]), [f"mes anterior: {usd(k['ticket_prev'])}"]),
        kpi("Clientes facturados", n0(k["clientes"]), [f"Top 5 = {k['conc_top5']:.0f}% de las ventas"]),
    ])
    avisos_html = "".join(f"<div class='aviso'>⚠ {html.escape(a)}</div>" for a in avisos)

    def img(b64):
        return f"<img src='data:image/png;base64,{b64}'>"

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Informe de ventas - {nombre_mes(anio, mes)}</title><style>{CSS}</style></head><body>
<h1>Informe de ventas facturadas</h1>
<div class="sub">Grupo Diforma SAS &middot; {nombre_mes(anio, mes).capitalize()} &middot; generado {datetime.now():%d/%m/%Y %H:%M}</div>
{avisos_html}
<div class="kpis">{kpis}</div>
<h2>Comportamiento en el tiempo</h2>
{img(graficos['diaria'])}
{img(graficos['evolucion'])}
<h2>Empresas</h2>
{tablas['empresas']}
<h2>Clientes</h2>
{img(graficos['clientes'])}
{tablas['clientes']}
<h2>Equipo comercial</h2>
{img(graficos['vendedores'])}
{tablas['vendedores']}
<h2>Equipos de venta</h2>
{tablas['equipos']}
<h2>Productos</h2>
{img(graficos['productos'])}
{tablas['productos']}
<h2>Categorias de producto</h2>
{tablas['categorias']}
<div class="nota">{NOTA_FUENTE}</div>
</body></html>"""


# --------------------------------------------------------------------------
# Programa principal
# --------------------------------------------------------------------------
def totales(df):
    if df.empty:
        return 0.0, 0.0, 0
    return float(df["usd"].sum()), float(df["cop"].sum()), nfacturas(df)


def facturas_resumen(df):
    g = df.groupby("factura").agg(
        Fecha=("fecha", "min"), Empresa=("empresa", "first"), Cliente=("cliente", "first"),
        Vendedor=("vendedor", "first"), Equipo=("equipo", "first"), Tipo=("tipo", "first"),
        Moneda=("moneda", "first"), USD=("usd", "sum"), COP=("cop", "sum"),
        Origen_TRM=("trm_origen", lambda s: "; ".join(sorted(set(s)))))
    g["TRM implicita"] = np.where(g["USD"].abs() > 0.005, g["COP"] / g["USD"], np.nan)
    g = g.rename(columns={"USD": "Sin IVA USD", "COP": "Sin IVA COP (contable)", "Origen_TRM": "Origen TRM"})
    return g.reset_index().rename(columns={"factura": "Factura"})


def construir_informe(odoo, anio, mes, carpeta):
    avisos = []
    a0, m0 = sumar_meses(anio, mes, -12)
    a1, m1 = sumar_meses(anio, mes, 1)
    hist = traer_facturacion(odoo, date(a0, m0, 1), date(a1, m1, 1), avisos)
    if hist.empty:
        print("No hay facturas publicadas en el periodo consultado.")
        return None
    hist["periodo"] = list(zip(hist["fecha"].dt.year, hist["fecha"].dt.month))
    mensual = hist.groupby("periodo")[["usd", "cop"]].sum()
    cuenta_mensual = hist[hist["tipo"] == "out_invoice"].groupby("periodo")["factura"].nunique().to_dict()

    actual = hist[hist["periodo"] == (anio, mes)].copy()
    if actual.empty:
        print(f"No hay facturas publicadas en {nombre_mes(anio, mes)}.")
        return None
    prev = hist[hist["periodo"] == sumar_meses(anio, mes, -1)]
    prev_anio = hist[hist["periodo"] == (anio - 1, mes)]

    t_usd, t_cop, nfac = totales(actual)
    p_usd, p_cop, nfac_prev = totales(prev)
    y_usd, y_cop, _ = totales(prev_anio)
    por_cliente = agrupar(actual, "cliente")
    k = dict(
        usd=t_usd, cop=t_cop, facturas=nfac, facturas_prev=nfac_prev,
        trm_prom=(t_cop / t_usd if t_usd else 0), trm_prev=(p_cop / p_usd if p_usd else 0),
        ticket=(t_usd / nfac if nfac else 0), ticket_prev=(p_usd / nfac_prev if nfac_prev else 0),
        clientes=len(por_cliente),
        conc_top5=(por_cliente["usd"].head(5).sum() / t_usd * 100 if t_usd else 0),
        var_mes_usd=variacion(t_usd, p_usd), var_anio_usd=variacion(t_usd, y_usd),
        var_mes_cop=variacion(t_cop, p_cop), var_anio_cop=variacion(t_cop, y_cop),
    )
    por_producto, por_categoria = agrupar(actual, "producto"), agrupar(actual, "categoria")
    por_vendedor, por_equipo, por_empresa = agrupar(actual, "vendedor"), agrupar(actual, "equipo"), agrupar(actual, "empresa")

    graficos = dict(
        diaria=grafica_diaria(actual, anio, mes),
        evolucion=grafica_evolucion(mensual["usd"].to_dict(), anio, mes),
        clientes=grafica_barras_h(por_cliente["usd"], "Top 10 clientes (USD sin IVA)"),
        vendedores=grafica_barras_h(por_vendedor["usd"], "Ventas por vendedor (USD sin IVA)", n=15),
        productos=grafica_barras_h(por_producto["usd"], "Top 10 productos (USD sin IVA)"),
    )
    tablas = dict(
        empresas=tabla_html(por_empresa, "Empresa", t_usd),
        clientes=tabla_html(por_cliente, "Cliente", t_usd),
        vendedores=tabla_html(por_vendedor, "Vendedor", t_usd, n=15),
        equipos=tabla_html(por_equipo, "Equipo de ventas", t_usd),
        productos=tabla_html(por_producto, "Producto", t_usd),
        categorias=tabla_html(por_categoria, "Categoria", t_usd, n=15),
    )

    carpeta.mkdir(parents=True, exist_ok=True)
    base = f"informe_ventas_{anio}-{mes:02d}"
    ruta_html = carpeta / f"{base}.html"
    ruta_html.write_text(generar_html(anio, mes, k, graficos, tablas, avisos), encoding="utf-8")

    ruta_xlsx = carpeta / f"{base}.xlsx"
    with pd.ExcelWriter(ruta_xlsx) as xw:
        pd.DataFrame({
            "Indicador": ["Ventas facturadas sin IVA (USD)", "Valor contable (COP)", "TRM promedio ponderada",
                          "Facturas emitidas", "Ticket promedio por factura (USD)", "Clientes facturados",
                          "Mes anterior (USD)", "Mes anterior (COP)",
                          f"Mismo mes {anio - 1} (USD)", f"Mismo mes {anio - 1} (COP)"],
            "Valor": [t_usd, t_cop, k["trm_prom"], nfac, k["ticket"], k["clientes"], p_usd, p_cop, y_usd, y_cop],
        }).to_excel(xw, sheet_name="Resumen", index=False)
        pd.DataFrame([{
            "Mes": f"{a}-{m:02d}",
            "Ventas sin IVA (USD)": mensual["usd"].get((a, m), 0),
            "Valor contable (COP)": mensual["cop"].get((a, m), 0),
            "Facturas": cuenta_mensual.get((a, m), 0)}
            for a, m in (sumar_meses(anio, mes, -j) for j in range(12, -1, -1))]).to_excel(
            xw, sheet_name="Evolucion", index=False)
        for nombre, g in [("Empresas", por_empresa), ("Clientes", por_cliente), ("Vendedores", por_vendedor),
                          ("Equipos", por_equipo), ("Productos", por_producto), ("Categorias", por_categoria)]:
            g.rename(columns={"usd": "Ventas sin IVA (USD)", "cop": "Valor contable (COP)"}).to_excel(
                xw, sheet_name=nombre)
        facturas_resumen(actual).to_excel(xw, sheet_name="Facturas", index=False)

    print(f"Ventas facturadas {nombre_mes(anio, mes)}: valor contable {cop(t_cop)}  ~  {usd(t_usd)}  ({nfac} facturas)")
    for a in avisos:
        print("AVISO:", a)
    return ruta_html, ruta_xlsx


def diagnostico(odoo):
    """Revisa la conexion y muestra totales mensuales por empresa, para cuadrarlos con Odoo."""
    print("== Companias y su moneda ==")
    for f in odoo.search_read("res.company", [], ["name", "currency_id"]):
        print(f"  {f['name']}: {m2o(f['currency_id'])}")
    hoy = date.today()
    a, m = sumar_meses(hoy.year, hoy.month, -5)
    avisos = []
    df = traer_facturacion(odoo, date(a, m, 1), date(*sumar_meses(hoy.year, hoy.month, 1), 1), avisos)
    if df.empty:
        print("\n(No hay facturas de cliente publicadas en los ultimos 6 meses)")
        return
    print("\n== Facturas de cliente publicadas por moneda (ultimos 6 meses) ==")
    for mon, g in df.groupby("moneda"):
        print(f"  {mon}: {g['factura'].nunique()} facturas")
    print("\n== Ventas sin IVA en COP por mes y empresa (valor contable) ==")
    print("   Cuadre: compare con Odoo > Facturacion > Informes > Analisis de facturas (solo facturas de cliente,")
    print("   publicadas, sin impuestos, por mes de fecha contable).")
    piv = df.assign(mes=df["fecha"].dt.strftime("%Y-%m")).pivot_table(
        index="mes", columns="empresa", values="cop", aggfunc="sum", fill_value=0)
    piv["TOTAL"] = piv.sum(axis=1)
    print(piv.round(0).map(lambda v: f"{v:,.0f}".replace(",", ".")).to_string())
    print("\n== Facturas en USD: TRM implicita (COP contable / USD de la factura) ==")
    g = df[(df["moneda"] == "USD") & (df["usd"].abs() > 0.005)]
    if len(g):
        r = (g["cop"] / g["usd"])
        print(f"  minimo {r.min():,.0f}  mediana {r.median():,.0f}  maximo {r.max():,.0f}".replace(",", "."))
    for a_ in avisos:
        print("AVISO:", a_)
    print("\nCopie y pegue este resultado a Claude (no contiene la clave).")


def main():
    cargar_env()
    ap = argparse.ArgumentParser(description="Informe mensual de ventas facturadas (Odoo 19)")
    ap.add_argument("--mes", help="Mes a reportar, formato AAAA-MM (por defecto: el mes anterior)")
    ap.add_argument("--salida", default="informes", help="Carpeta de salida (por defecto: informes)")
    ap.add_argument("--diagnostico", action="store_true", help="Revisa la conexion y muestra totales para cuadrar con Odoo")
    args = ap.parse_args()

    faltan = [v for v in ("ODOO_URL", "ODOO_DB", "ODOO_KEY") if not os.environ.get(v)]
    if faltan:
        sys.exit("Faltan datos de conexion: " + ", ".join(faltan) + ". Complete el archivo conexion.txt.")
    odoo = Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])

    if args.diagnostico:
        try:
            diagnostico(odoo)
        except (RuntimeError, requests.RequestException) as e:
            sys.exit(f"Error: {e}")
        return

    if args.mes:
        try:
            anio, mes = (int(x) for x in args.mes.split("-"))
            assert 1 <= mes <= 12
        except Exception:
            sys.exit("Formato de --mes invalido. Use AAAA-MM, por ejemplo 2026-10")
    else:
        hoy = date.today()
        anio, mes = sumar_meses(hoy.year, hoy.month, -1)

    print(f"Consultando Odoo para {nombre_mes(anio, mes)} ...")
    try:
        resultado = construir_informe(odoo, anio, mes, Path(args.salida))
    except (RuntimeError, requests.RequestException) as e:
        sys.exit(f"Error: {e}")
    if resultado:
        print("Listo:")
        for r in resultado:
            print("  ", r.resolve())


if __name__ == "__main__":
    main()
