#!/usr/bin/env python3
"""
Indicadores mensuales del area de PRODUCCION - Grupo Diforma SAS (Odoo 19)

Se calculan sobre el modelo personalizado crm.brief_approved ("Brief Aprobados") y los planes
analiticos de proyecto. Definiciones acordadas con Diforma:

  Proyecto ejecutado = proyecto (plan analitico) de tipo Produccion o Prototipo Facturable,
                       contado en el mes de su PRIMERA factura de cliente contabilizada.
  Garantia           = brief aprobado de Tipo = Garantia (se cuenta en el mes en que se crea).
  Costo proyectado   = campo "Costo Proyectado" x cantidad de los briefs aprobados del proyecto.
  Costo real         = suma neta de los apuntes del plan analitico del proyecto, SIN las categorias
                       "Venta" y "Servicio Exportacion" (no son costo).
  Proveedor nuevo    = proveedor cuya primera factura de proveedor (la primera en Odoo) lleva
                       distribucion analitica a proyectos, en el mes de esa factura.

  cero_garantias = (ejecutados - garantias) / ejecutados x 100          meta >= 100
  proveedores    = proveedores nuevos / meta de proveedores por mes x 100  meta >= 100 (1 por periodo)
                   (se puede digitar a mano el dato oficial; si no, se usa el de Odoo)
  eficiencia     = (costo proyectado - costo real) / costo proyectado x 100  meta >= 5
                   solo proyectos ejecutados con proyectado >= umbral y con costo real > 0
  costo_garantias = costo real (COP) de los planes de las garantias del mes. Informativo, sin meta.

Aqui solo se calculan conteos y sumas por mes; los cocientes se calculan en el dashboard.
Solo lee datos.

Uso directo (verificacion):  python indicadores_produccion.py [--anio 2026]
"""
import argparse
import json
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import informe_ventas as iv  # noqa: E402
import indicadores_comercial as ic  # noqa: E402

CONFIG = Path(__file__).resolve().parent / "metas_produccion.json"
RX_EJEC = r"^producci|facturable"
IND = ["garantias", "proveedores", "eficiencia", "costo_garantias"]
MAXFILAS = 80


def cargar_config():
    with open(CONFIG, encoding="utf-8-sig") as f:
        return json.load(f)


def _norm(s):
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().strip().lower()


def _ym(serie):
    s = pd.to_datetime(serie, errors="coerce")
    return s.dt.year * 100 + s.dt.month


def _leer_en_lotes(odoo, modelo, campo, ids, campos, extra=None, lote=500):
    out = []
    for i in range(0, len(ids), lote):
        dom = [[campo, "in", ids[i:i + lote]]] + (extra or [])
        out.extend(ic.leer_todo(odoo, modelo, dom, campos))
    return out


def _cargar_briefs(odoo):
    info = odoo.call("crm.brief_approved", "fields_get", attributes=["string", "type", "selection"])
    quiero = ["name", "tipo", "costo", "cantidad", "plan_analitico_id", "create_date", "state", "partner_id"]
    campos = [c for c in quiero if c in info]
    b = pd.DataFrame(ic.leer_todo(odoo, "crm.brief_approved", [], campos))
    if b.empty:
        return b
    etiq = {str(a): l for a, l in (info.get("tipo", {}).get("selection") or [])}
    b["tipo_"] = b["tipo"].astype(str).map(lambda x: etiq.get(x, x))
    b["plan_id"] = pd.to_numeric(b["plan_analitico_id"].map(iv.m2o_id), errors="coerce").fillna(0).astype(int)
    b["creado"] = ic._local(b["create_date"])
    b["cantidad"] = pd.to_numeric(b["cantidad"], errors="coerce").fillna(1)
    b["costo"] = pd.to_numeric(b["costo"], errors="coerce").fillna(0)
    b["proy"] = b["costo"] * b["cantidad"]
    b["cliente"] = b["partner_id"].map(lambda v: iv.m2o(v) or "") if "partner_id" in b else ""
    b["es_prod"] = b["tipo_"].str.contains(RX_EJEC, case=False, na=False, regex=True)
    b["es_gar"] = b["tipo_"].str.contains("garant", case=False, na=False)
    # los briefs en Borrador (state 1) todavia no son proyecto aprobado
    if "state" in b:
        b = b[b["state"].astype(str) != "1"]
    return b[b["plan_id"] > 0].copy()


def _cuentas_de_planes(odoo, plan_ids):
    info = odoo.call("account.analytic.account", "fields_get", attributes=["string", "type"])
    dom = ["|", ["plan_id", "in", plan_ids], ["root_plan_id", "in", plan_ids]] if "root_plan_id" in info else [["plan_id", "in", plan_ids]]
    cu = pd.DataFrame(ic.leer_todo(odoo, "account.analytic.account", dom, [c for c in ("name", "plan_id", "root_plan_id") if c in info]))
    a2p, a2n = {}, {}
    for r in cu.itertuples():
        p = iv.m2o_id(r.plan_id)
        if p not in plan_ids and hasattr(r, "root_plan_id"):
            p = iv.m2o_id(r.root_plan_id)
        a2p[int(r.id)] = int(p) if p else 0
        a2n[int(r.id)] = r.name
    return a2p, a2n


def calcular(odoo, anio, cfg, avisos):
    """Devuelve dict listo para el dashboard (sumas y detalle por mes)."""
    ahora = datetime.now()
    b = _cargar_briefs(odoo)
    if b.empty:
        return {"ok": False}
    plan_ids = sorted(set(b["plan_id"]))
    a2p, a2n = _cuentas_de_planes(odoo, plan_ids)
    if not a2p:
        avisos.append("Produccion: no se encontraron cuentas analiticas dentro de los planes de los briefs.")
        return {"ok": False}

    excl = {_norm(x) for x in cfg.get("categorias_excluidas", [])}
    lin = pd.DataFrame(_leer_en_lotes(odoo, "account.analytic.line", "account_id", sorted(a2p), ["account_id", "amount", "date"]))
    if lin.empty:
        lin = pd.DataFrame(columns=["account_id", "amount", "date"])
    lin["aid"] = lin["account_id"].map(iv.m2o_id)
    lin["plan_id"] = lin["aid"].map(a2p)
    lin["cat"] = lin["aid"].map(a2n)
    costo = lin[~lin["cat"].map(_norm).isin(excl)]
    real_por_plan = (-costo.groupby("plan_id")["amount"].sum()).to_dict()

    # --- tabla por proyecto (plan analitico) ---
    g = b.sort_values("creado").groupby("plan_id")
    pl = pd.DataFrame({
        "nombre": g["name"].first(), "cliente": g["cliente"].first(), "tipo": g["tipo_"].first(),
        "es_prod": g["es_prod"].any() & ~g["es_gar"].any(), "es_gar": g["es_gar"].any(),
        "proy": g["proy"].sum(), "creado": g["creado"].min()})
    pl["real"] = pl.index.map(lambda p: float(real_por_plan.get(p, 0.0)))

    # --- apuntes anomalos: un solo costo mayor a N veces el proyectado del proyecto (ej. traslado de inventario mal valorado) ---
    f_anom = float(cfg.get("factor_apunte_anomalo", 3.0))
    if f_anom > 0:
        for pid, r in pl.iterrows():
            if r["proy"] < float(cfg.get("min_costo_proyectado", 100000)):
                continue
            c = costo[(costo["plan_id"] == pid) & (-costo["amount"] > f_anom * r["proy"])]
            if len(c):
                tot = float((-c["amount"]).sum())
                pl.at[pid, "real"] = float(r["real"]) - tot
                avisos.append(f"Produccion: {r['nombre']}: {len(c)} apunte(s) de costo por ${tot:,.0f} superan {f_anom}x el proyectado "
                              f"(${r['proy']:,.0f}); se excluyeron del costo real. Revisar en Odoo (posible traslado de inventario mal valorado).")

    # --- primera factura de cliente por proyecto ---
    ml = pd.DataFrame(ic.leer_todo(odoo, "account.move.line",
                                   [["move_id.move_type", "=", "out_invoice"], ["parent_state", "=", "posted"],
                                    ["analytic_distribution", "!=", False]], ["date", "analytic_distribution"]))
    filas = []
    for r in ml.itertuples():
        d = r.analytic_distribution
        if isinstance(d, dict):
            for k in d:
                for x in str(k).split(","):
                    if x.strip().isdigit() and int(x) in a2p:
                        filas.append((a2p[int(x)], r.date))
    fa = pd.DataFrame(filas, columns=["plan_id", "fecha"])
    fa["fecha"] = pd.to_datetime(fa["fecha"], errors="coerce")
    pl["primera"] = fa.groupby("plan_id")["fecha"].min().reindex(pl.index)

    ultimo = ahora.month if anio == ahora.year else 12
    desde = int((cfg.get("desde") or {}).get(str(anio), 1))
    desde_prov = int((cfg.get("prov_desde") or {}).get(str(anio), desde))
    min_proy = float(cfg.get("min_costo_proyectado", 100000))
    req_real = bool(cfg.get("requiere_costo_real", True))

    M = {k: [0] * 12 for k in ("ejec", "gar", "prov", "nefi", "proy", "real", "gcosto")}
    DET = {}

    def det(mes):
        return DET.setdefault(str(mes), {"ejec": [], "gar": [], "prov": [], "excl": []})

    # ejecutados y eficiencia
    ej = pl[pl["es_prod"] & pl["primera"].notna() & (pl["primera"].dt.year == anio)]
    for p, r in ej.sort_values("primera").iterrows():
        m = r["primera"].month - 1
        M["ejec"][m] += 1
        min_pct = float(cfg.get("min_real_pct_proy", 0.10))
        incl = r["proy"] >= min_proy and (r["real"] > 0 or not req_real) and r["real"] >= min_pct * r["proy"]
        ahorro = ((r["proy"] - r["real"]) / r["proy"] * 100) if r["proy"] > 0 else None
        if incl:
            M["nefi"][m] += 1
            M["proy"][m] += float(r["proy"])
            M["real"][m] += float(r["real"])
        else:
            motivo = "proyectado menor al umbral" if r["proy"] < min_proy else ("costo real insignificante (<%d%% del proyectado)" % round(min_pct * 100) if r["real"] > 0 else "sin costo real")
            det(m)["excl"].append([str(r["nombre"]), r["cliente"], round(float(r["proy"])), round(float(r["real"])), motivo])
        det(m)["ejec"].append([str(r["nombre"]), r["cliente"], r["tipo"], f"{r['primera']:%d/%m}", round(float(r["proy"])),
                               round(float(r["real"])), (None if ahorro is None else round(float(ahorro), 1)), 1 if incl else 0])

    # garantias
    gr = pl[pl["es_gar"] & (pl["creado"].dt.year == anio)]
    for p, r in gr.sort_values("creado").iterrows():
        m = r["creado"].month - 1
        M["gar"][m] += 1
        M["gcosto"][m] += float(r["real"])
        det(m)["gar"].append([str(r["nombre"]), r["cliente"], f"{r['creado']:%d/%m}", round(float(r["real"]))])

    # proveedores nuevos que facturan a proyectos
    try:
        mp = pd.DataFrame(ic.leer_todo(odoo, "account.move.line",
                                       [["move_id.move_type", "=", "in_invoice"], ["parent_state", "=", "posted"],
                                        ["analytic_distribution", "!=", False]], ["partner_id", "date", "analytic_distribution"]))
        f2 = []
        for r in mp.itertuples():
            d = r.analytic_distribution
            if isinstance(d, dict) and any(int(x) in a2p for k in d for x in str(k).split(",") if x.strip().isdigit()):
                f2.append((iv.m2o_id(r.partner_id), iv.m2o(r.partner_id), r.date))
        pr = pd.DataFrame(f2, columns=["pid", "nombre", "fecha"])
        pr["fecha"] = pd.to_datetime(pr["fecha"], errors="coerce")
        prim = pr.groupby(["pid", "nombre"])["fecha"].min().reset_index()
        hdr = pd.DataFrame(ic.leer_todo(odoo, "account.move", [["move_type", "=", "in_invoice"], ["state", "=", "posted"]],
                                        ["partner_id", "invoice_date"]))
        hdr["pid"] = hdr["partner_id"].map(iv.m2o_id)
        hdr["f"] = pd.to_datetime(hdr["invoice_date"], errors="coerce")
        gen = hdr.groupby("pid")["f"].min()
        prim["gen"] = prim["pid"].map(gen)
        prim = prim[(prim["fecha"].dt.year == anio) & (_ym(prim["fecha"]) == _ym(prim["gen"]))]
        for r in prim.sort_values("fecha").itertuples():
            m = r.fecha.month - 1
            if m + 1 < desde_prov:
                continue
            M["prov"][m] += 1
            det(m)["prov"].append([str(r.nombre), f"{r.fecha:%d/%m}"])
    except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
        avisos.append("Produccion: no se pudieron leer los proveedores nuevos: " + str(e)[:200])

    if not any(sum(v) for v in M.values()):
        return {"ok": False}

    def recorta(arr, d0=desde):
        return [(None if (j + 1 > ultimo or j + 1 < d0) else v) for j, v in enumerate(arr)]

    for k in M:
        M[k] = recorta(M[k], desde_prov if k == "prov" else desde)
    for mes in DET:
        for k in DET[mes]:
            DET[mes][k] = DET[mes][k][:MAXFILAS]

    manual = (cfg.get("manual", {}).get("proveedores", {}) or {}).get(str(anio), [None] * 12)
    return {
        "ok": True, "ultimo": ultimo, "desde": desde, "desde_prov": desde_prov, "m": M, "det": DET,
        "hoy": ahora.date().isoformat(), "meta": cfg["metas"], "tipo": cfg["tipo"],
        "amarillo_max": cfg.get("amarillo_max", 1.25), "amarillo_min": cfg.get("amarillo_min", 0.7),
        "prov_por_mes": float(cfg.get("prov_por_mes", 1)), "min_proy": min_proy,
        "excluidas": cfg.get("categorias_excluidas", []),
        "manual_prov": (manual + [None] * 12)[:12],
        "hoja": {str(m): cfg.get("hoja", {}).get(f"{anio}-{m:02d}") for m in range(1, 13)},
    }


def valores(pro, k, manual=None):
    """Misma logica que el dashboard (JS), para verificar desde la consola. k = mes 0..11."""
    m = pro["m"]
    g = lambda c: (m[c][k] or 0)  # noqa: E731
    r = lambda a, b: (a / b * 100) if b else None  # noqa: E731
    man = (manual or pro["manual_prov"])[k]
    nuevos = man if man is not None else g("prov")
    return {"garantias": r(g("ejec") - g("gar"), g("ejec")), "proveedores": nuevos / pro["prov_por_mes"] * 100,
            "eficiencia": r(g("proy") - g("real"), g("proy")), "costo_garantias": g("gcosto")}


def main():
    iv.cargar_env()
    ap = argparse.ArgumentParser(description="Indicadores del area de Produccion (Odoo 19)")
    ap.add_argument("--anio", type=int, default=datetime.now().year)
    args = ap.parse_args()
    odoo = iv.Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])
    avisos = []
    pro = calcular(odoo, args.anio, cargar_config(), avisos)
    for a in avisos:
        print("AVISO:", a)
    if not pro.get("ok"):
        sys.exit("Sin datos de Produccion para ese ano.")
    m = pro["m"]
    print("Mes  ejec  gar  prov  proyectos_efi   proyectado          real   | cero_gar  prov%   ahorro%   costo_garantias   (hoja)")
    for k in range(pro["desde"] - 1, pro["ultimo"]):
        v = valores(pro, k)
        h = pro["hoja"].get(str(k + 1)) or {}
        f = lambda x: "   -  " if x is None else f"{x:6.1f}"  # noqa: E731
        print(f"{k + 1:>3}  {m['ejec'][k] or 0:>4} {m['gar'][k] or 0:>4} {m['prov'][k] if m['prov'][k] is not None else 0:>5} {m['nefi'][k] or 0:>10}"
              f" {m['proy'][k] or 0:>14,.0f} {m['real'][k] or 0:>13,.0f}   | {f(v['garantias'])} {f(v['proveedores'])}  {f(v['eficiencia'])}"
              f"   {v['costo_garantias']:>14,.0f}   {h}")


if __name__ == "__main__":
    main()
