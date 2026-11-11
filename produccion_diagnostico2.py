#!/usr/bin/env python3
"""
Diagnostico 2 (solo lectura) del area de PRODUCCION.
Resuelve lo que dejo abierto el diagnostico 1:
  I) Categorias de costo dentro de los planes analiticos de proyecto (que parte cuenta la hoja).
  J) Entregas (fecha de entrega, orden de produccion) y ordenes de produccion terminadas por mes.
  K) Costo proyectado = costo x cantidad, contra costo real, por mes de ULTIMA entrega.
  L) Proveedores nuevos que facturan a proyectos de produccion (con nombres).
Uso:  python produccion_diagnostico2.py
"""
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import informe_ventas as iv  # noqa: E402
import indicadores_comercial as ic  # noqa: E402
from brief_diforma import seguro  # noqa: E402
import produccion_diagnostico as P  # noqa: E402  (solo reutiliza utilidades)

DESDE = "2026-01-01"
RX_EJEC = r"^producci|facturable"
HOJA_PROV = {202604: 2, 202605: 5, 202606: 1, 202607: 1, 202608: 1, 202609: 0}
HOJA_EJEC = {202604: 12, 202609: 9}


def seccion(f, *a):
    try:
        f(*a)
    except Exception:  # noqa: BLE001 - diagnostico: mostrar y seguir
        print("   (la seccion fallo:)")
        traceback.print_exc(file=sys.stdout)


def cargar_base(odoo):
    f_ba = P.campos_de(odoo, "crm.brief_approved")
    pedir = [c for c in ("name", "tipo", "costo", "cantidad", "precio_venta", "plan_analitico_id", "create_date",
                         "state", "partner_id") if c in f_ba]
    b = pd.DataFrame(seguro(ic.leer_todo, odoo, "crm.brief_approved", [], pedir) or [])
    sel = seguro(odoo.call, "crm.brief_approved", "fields_get", attributes=["selection"], allfields=["tipo"])
    etiq = {str(a): l for a, l in (sel or {}).get("tipo", {}).get("selection", [])}
    b["tipo_"] = b["tipo"].astype(str).map(lambda x: etiq.get(x, x))
    b["plan_id"] = pd.to_numeric(b["plan_analitico_id"].map(iv.m2o_id), errors="coerce").fillna(0).astype(int)
    b["creado"] = ic._local(b["create_date"])
    b["mes_cre"] = P.ym(b["creado"])
    b["cantidad"] = pd.to_numeric(b["cantidad"], errors="coerce").fillna(1)
    b["costo"] = pd.to_numeric(b["costo"], errors="coerce").fillna(0)
    b["proy"] = b["costo"] * b["cantidad"]
    b["es_prod"] = b["tipo_"].str.contains(RX_EJEC, case=False, na=False, regex=True)
    b["es_gar"] = b["tipo_"].str.contains("garant", case=False, na=False)

    ids = sorted({int(i) for i in b["plan_id"] if i})
    f_cu = P.campos_de(odoo, "account.analytic.account")
    dom = ["|", ["plan_id", "in", ids], ["root_plan_id", "in", ids]] if "root_plan_id" in f_cu else [["plan_id", "in", ids]]
    cu = pd.DataFrame(seguro(ic.leer_todo, odoo, "account.analytic.account", dom,
                             [c for c in ("name", "plan_id", "root_plan_id") if c in f_cu]) or [])
    a2p = {}
    for _, r in cu.iterrows():
        p = iv.m2o_id(r["plan_id"])
        if p not in ids and "root_plan_id" in cu:
            p = iv.m2o_id(r["root_plan_id"])
        a2p[int(r["id"])] = int(p) if p else 0
    a2n = dict(zip(cu["id"], cu["name"]))

    f_al = P.campos_de(odoo, "account.analytic.line")
    aids = sorted(a2p)
    lin = []
    for i in range(0, len(aids), 500):
        lin += seguro(ic.leer_todo, odoo, "account.analytic.line", [["account_id", "in", aids[i:i + 500]]],
                      [c for c in ("account_id", "amount", "date", "name", "general_account_id") if c in f_al]) or []
    al = pd.DataFrame(lin)
    al["aid"] = al["account_id"].map(iv.m2o_id).astype(int)
    al["plan_id"] = al["aid"].map(a2p)
    al["cat"] = al["aid"].map(a2n)
    al["mes"] = P.ym(al["date"])
    return b, al, a2p, a2n


def sec_categorias(al):
    P.titulo("I) Categorias de costo dentro de los planes de proyecto (cuentas analiticas hijas)")
    print(f"   Apuntes: {len(al)}   planes: {al['plan_id'].nunique()}")
    g = al.groupby("cat")["amount"].agg(lineas="count", gastos=lambda s: -s[s < 0].sum(), ingresos=lambda s: s[s > 0].sum())
    print("   Totales por categoria (gastos = suma de importes negativos; ingresos = positivos):")
    print(g.sort_values("gastos", ascending=False).round(0).to_string())
    neg = al[al["amount"] < 0]
    pv = neg.pivot_table(index="cat", columns="mes", values="amount", aggfunc="sum", fill_value=0).mul(-1)
    cols = [c for c in pv.columns if 202604 <= c <= 202609]
    print("\n   Gasto por categoria y mes (abril a septiembre), para comparar con la hoja:")
    print(pv[cols].round(0).to_string())
    print("   TOTAL                " + "  ".join(f"{pv[c].sum():>14,.0f}" for c in cols))
    print("   HOJA costo real      " + "  ".join(f"{P.HOJA[c]['real']:>14,.0f}" for c in cols))
    pos = al[al["amount"] > 0].sort_values("amount", ascending=False).head(12)
    print("\n   12 apuntes positivos mas grandes (que son?):")
    for _, r in pos.iterrows():
        print(f"     {str(r['date'])[:10]}  {str(r['cat'])[:28]:28s} {r['amount']:>14,.0f}  {str(r.get('name', ''))[:60]}")


def sec_entregas(odoo, b):
    P.titulo("J) Entregas y ordenes de produccion")
    f_en = P.campos_de(odoo, "crm.brief_approved_entregas")
    for k, v in f_en.items():
        if not k.startswith(("message_", "activity_", "write_", "create_", "__")):
            print(f"   {k:28s} {v['type']:10s} {v.get('relation', '') or ''} | {v['string']}")
    padre = next((k for k, v in f_en.items() if v.get("relation") == "crm.brief_approved"), None)
    c_fecha = next((k for k, v in f_en.items() if v["type"] in ("date", "datetime") and re.search("entrega", k + v.get("string", ""), re.I)), None)
    if not padre or not c_fecha:
        print("   (no se detecto el campo del brief padre o la fecha de entrega)")
        return None
    en = pd.DataFrame(seguro(ic.leer_todo, odoo, "crm.brief_approved_entregas", [], [padre, c_fecha]) or [])
    en["bid"] = en[padre].map(iv.m2o_id)
    en["f"] = pd.to_datetime(en[c_fecha].where(en[c_fecha].astype(bool)), errors="coerce")
    en["mes"] = P.ym(en["f"])
    en["tipo"] = en["bid"].map(dict(zip(b["id"], b["tipo_"])))
    print(f"\n   Entregas: {len(en)}   con fecha: {en['f'].notna().sum()}")
    ok = en[en["tipo"].fillna("").str.contains(RX_EJEC, case=False, regex=True)]
    desde = ok[ok["mes"] >= 202602]
    ult = ok.groupby("bid")["f"].max().dropna()
    ult_m = (ult.dt.year * 100 + ult.dt.month)
    print("\n   Proyectos Produccion/Prototipo Facturable por mes de entrega:")
    print("   mes     con alguna entrega   ultima entrega en el mes   (hoja implicita: abril 12, septiembre 9)")
    a1 = desde.groupby("mes")["bid"].nunique()
    a2 = ult_m[ult_m >= 202602].value_counts().sort_index()
    for m in sorted(set(a1.index) | set(a2.index)):
        print(f"   {int(m)}   {int(a1.get(m, 0)):>10}   {int(a2.get(m, 0)):>20}")
    g = en[en["tipo"].fillna("").str.contains("garant", case=False)]
    print("\n   Garantias por mes de entrega:")
    print(g.groupby("mes")["bid"].nunique().to_string() if len(g) else "   ninguna")
    f_mo = P.campos_de(odoo, "mrp.production")
    cmo = [c for c in ("name", "state", "date_finished", "product_qty", "origin") if c in f_mo]
    mo = pd.DataFrame(seguro(ic.leer_todo, odoo, "mrp.production", [["state", "=", "done"]], cmo) or [])
    if len(mo) and "date_finished" in mo:
        mo["mes"] = P.ym(ic._local(mo["date_finished"]))
        print("\n   Ordenes de produccion TERMINADAS por mes (mrp.production, state=done):")
        print(mo[mo["mes"] >= 202602].groupby("mes").size().to_string())
    return en


def sec_costos(b, al, en):
    P.titulo("K) Costo proyectado (costo x cantidad) contra costo real, por mes de ultima entrega")
    prod = b[b["es_prod"] & (b["plan_id"] > 0)].copy()
    ult = en.groupby("bid")["f"].max() if en is not None else pd.Series(dtype="datetime64[ns]")
    prod["f_ult"] = prod["id"].map(ult)
    prod["mes_ent"] = P.ym(prod["f_ult"])
    prod["mes_ent"] = prod["mes_ent"].fillna(prod["mes_cre"]).astype(int)
    bruto = -al[al["amount"] < 0].groupby("plan_id")["amount"].sum()
    neto = -al.groupby("plan_id")["amount"].sum()
    pl = prod.groupby("plan_id").agg(proy=("proy", "sum"), mes=("mes_ent", "max"))
    pl["bruto"] = pl.index.map(bruto).values
    pl["neto"] = pl.index.map(neto).values
    pl = pl.fillna(0)
    g = pl.groupby("mes").agg(proyectos=("proy", "count"), proy=("proy", "sum"), bruto=("bruto", "sum"), neto=("neto", "sum"))
    den = g["proy"].where(g["proy"] > 0)
    g["ahorro_bruto_%"] = ((g["proy"] - g["bruto"]) / den * 100).round(1)
    g["ahorro_neto_%"] = ((g["proy"] - g["neto"]) / den * 100).round(1)
    print(g[g.index >= 202602].round(0).to_string())
    print("\n   Hoja: mes  proyectado  real  eficiencia%")
    for m, h in P.HOJA.items():
        print(f"   {m}  {h['proy']:>14,.0f}  {h['real']:>14,.0f}  {h['efi']:5.1f}")
    print("\n   Proyectos de septiembre (mes de ultima entrega): nombre | proyectado | real bruto | real neto")
    nm = prod.groupby("plan_id")["name"].first()
    for pid, r in pl[pl["mes"] == 202609].iterrows():
        print(f"     {str(nm.get(pid, ''))[:40]:40s} {r['proy']:>13,.0f} {r['bruto']:>13,.0f} {r['neto']:>13,.0f}")


def sec_proveedores(odoo, a2p):
    P.titulo("L) Proveedores nuevos que facturan a proyectos de produccion")
    ml = pd.DataFrame(seguro(ic.leer_todo, odoo, "account.move.line",
                             [["move_id.move_type", "=", "in_invoice"], ["parent_state", "=", "posted"],
                              ["analytic_distribution", "!=", False]],
                             ["partner_id", "date", "analytic_distribution"]) or [])
    filas = []
    for _, r in ml.iterrows():
        d = r["analytic_distribution"]
        if isinstance(d, dict) and any(int(x) in a2p for k in d for x in str(k).split(",") if x.strip().isdigit()):
            filas.append((iv.m2o_id(r["partner_id"]), iv.m2o(r["partner_id"]), r["date"]))
    pr = pd.DataFrame(filas, columns=["pid", "nombre", "fecha"])
    print(f"   Lineas de factura de proveedor a proyectos: {len(pr)}   proveedores distintos: {pr['pid'].nunique()}")
    if not len(pr):
        return
    pr["fecha"] = pd.to_datetime(pr["fecha"], errors="coerce")
    primera = pr.groupby(["pid", "nombre"])["fecha"].min().reset_index()
    primera["mes"] = P.ym(primera["fecha"])
    hdr = pd.DataFrame(seguro(ic.leer_todo, odoo, "account.move", [["move_type", "=", "in_invoice"], ["state", "=", "posted"]],
                              ["partner_id", "invoice_date"]) or [])
    hdr["pid"] = hdr["partner_id"].map(iv.m2o_id)
    hdr["f"] = pd.to_datetime(hdr["invoice_date"], errors="coerce")
    primera_gral = hdr.groupby("pid")["f"].min()
    primera["mes_primera_factura_general"] = P.ym(primera["pid"].map(primera_gral))
    print("   (Odoo solo tiene datos desde 2026-01: en febrero 'nuevo' incluye a todos los que ya existian.)")
    print("\n   mes     a_proyectos(primera vez)  ademas_primera_factura_general   hoja")
    for m in sorted(primera["mes"].dropna().unique()):
        if m < 202602:
            continue
        sub = primera[primera["mes"] == m]
        nuevos = sub[sub["mes_primera_factura_general"] == m]
        print(f"   {int(m)}   {len(sub):>10}   {len(nuevos):>20}   {HOJA_PROV.get(int(m), '-')}")
        print("       a proyectos :", sorted(sub["nombre"].tolist())[:15])
        print("       realmente nuevos:", sorted(nuevos["nombre"].tolist())[:15])


def main():
    iv.cargar_env()
    faltan = [v for v in ("ODOO_URL", "ODOO_DB", "ODOO_KEY") if not os.environ.get(v)]
    if faltan:
        sys.exit("Faltan datos de conexion: " + ", ".join(faltan))
    odoo = iv.Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])
    print("DIAGNOSTICO 2 DEL AREA PRODUCCION  -", datetime.now().strftime("%d/%m/%Y %H:%M"))
    b, al, a2p, a2n = cargar_base(odoo)
    print(f"   Briefs: {len(b)}   cuentas analiticas de proyecto: {len(a2p)}   apuntes: {len(al)}")
    seccion(sec_categorias, al)
    en = None
    try:
        en = sec_entregas(odoo, b)
    except Exception:  # noqa: BLE001
        print("   (la seccion J fallo:)")
        traceback.print_exc(file=sys.stdout)
    seccion(sec_costos, b, al, en)
    seccion(sec_proveedores, odoo, a2p)
    print("\nFIN. Envie diagnostico_produccion2.txt para continuar.")


if __name__ == "__main__":
    main()
