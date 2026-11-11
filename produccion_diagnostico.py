#!/usr/bin/env python3
"""
Diagnostico (solo lectura) de los indicadores del area de PRODUCCION.

Indicadores (hoja "Indicadores Area (Produccion)"):
  1) Cero garantias       = (proyectos ejecutados - garantias) / proyectos ejecutados   meta 100%
       ejecutado = proyecto facturado en el mes; garantia = brief/proyecto con etiqueta o tipo garantia.
  2) Proveedor de servicios nuevo (bimensual, meta 1 cada 2 meses)
       contactos proveedor creados en el mes (con detalle de cuales) + opcion manual.
  3) Eficiencia en el costo = (costo proyectado - costo real) / costo proyectado   meta ahorro >= 5%
       proyectado = campo "costo" del modelo crm.brief_approved; real = plan analitico del proyecto.

Este script solo DESCUBRE y compara: no escribe nada en Odoo.
Uso:  python produccion_diagnostico.py
"""
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import informe_ventas as iv  # noqa: E402
import indicadores_comercial as ic  # noqa: E402
from brief_diforma import leer_ids, seguro  # noqa: E402

DESDE = "2026-01-01"
RE_GAR = re.compile(r"garant|reclam|reproces|devoluci", re.I)

# Valores de la hoja para calibrar (mes AAAAMM): garantias, % cero garantias, costo proy., costo real, % eficiencia
HOJA = {
    202604: dict(gar=1, cero=91.7, proy=25161322, real=25009925, efi=0.6),
    202605: dict(gar=0, cero=100.0, proy=140119463, real=137198593, efi=2.1),
    202606: dict(gar=0, cero=100.0, proy=189325000, real=164722161, efi=13.0),
    202607: dict(gar=0, cero=100.0, proy=8800000, real=5341152, efi=8.5),
    202608: dict(gar=0, cero=100.0, proy=146883958, real=91167010, efi=37.9),
    202609: dict(gar=1, cero=88.9, proy=22219125, real=10199788, efi=54.1),
}


def titulo(t):
    print("\n" + "=" * 100 + "\n" + t + "\n" + "=" * 100)


def campos_de(odoo, modelo, patron=None):
    info = seguro(odoo.call, modelo, "fields_get", attributes=["string", "type", "relation"])
    if not info:
        print(f"   (no se pudo leer {modelo}: puede no existir o no tener permiso)")
        return {}
    if patron:
        rx = re.compile(patron, re.I)
        return {k: v for k, v in info.items() if rx.search(k) or rx.search(v.get("string", ""))}
    return info


def ym(serie):
    s = pd.to_datetime(serie, errors="coerce")
    return s.dt.year * 100 + s.dt.month


def main():
    iv.cargar_env()
    faltan = [v for v in ("ODOO_URL", "ODOO_DB", "ODOO_KEY") if not os.environ.get(v)]
    if faltan:
        sys.exit("Faltan datos de conexion: " + ", ".join(faltan))
    odoo = iv.Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])
    print("DIAGNOSTICO DEL AREA PRODUCCION  -", datetime.now().strftime("%d/%m/%Y %H:%M"))

    # ------------------------------------------------------------------ A. Modelos
    titulo("A) Modelos relacionados (nombre tecnico)")
    mods = seguro(odoo.search_read, "ir.model", [["model", "ilike", "brief"]], ["model", "name"]) or []
    for m in mods:
        print("  ", m["model"], "-", m["name"])
    mods = seguro(odoo.search_read, "ir.model",
                  ["|", "|", ["model", "ilike", "garant"], ["model", "ilike", "analytic.plan"], ["model", "ilike", "project"]],
                  ["model", "name"]) or []
    for m in mods[:40]:
        print("  ", m["model"], "-", m["name"])

    # ------------------------------------------------------------------ B. crm.brief_approved
    titulo("B) Modelo crm.brief_approved: campos (el costo proyectado esta aqui)")
    f_ba = campos_de(odoo, "crm.brief_approved")
    for k, v in sorted(f_ba.items()):
        if k.startswith(("message_", "activity_", "__", "write_", "create_")):
            continue
        print(f"   {k:34s} {v['type']:10s} {v.get('relation', '') or ''}  | {v['string']}")
    ba = pd.DataFrame()
    if f_ba:
        pedir = [c for c in f_ba if f_ba[c]["type"] not in ("one2many", "many2many", "binary", "html")
                 and c not in ("display_name", "cantidad_total", "has_message")][:60]
        filas = seguro(ic.leer_todo, odoo, "crm.brief_approved", [], pedir)
        ba = pd.DataFrame(filas or [])
        print(f"\n   Registros en crm.brief_approved: {len(ba)}")
        if len(ba):
            print(ba.head(8).to_string(max_colwidth=28))

    # ------------------------------------------------------------------ B2. Brief aprobado: tipo, costo, plan analitico
    titulo("B2) Brief aprobado: Tipo, Costo Proyectado y Plan Analitico (campos detectados por su etiqueta)")

    def buscar(patron):
        for k, v in f_ba.items():
            if re.search(patron, v.get("string", ""), re.I):
                return k
        return None

    c_tipo = buscar(r"^tipo$")
    c_costo = buscar(r"costo proyectado")
    c_plan = buscar(r"plan anal")
    c_precio = buscar(r"precio venta")
    c_nombre = buscar(r"nombre del brief")
    c_estado = "state" if "state" in f_ba else buscar(r"^estado$")
    print("   Campos detectados:", dict(tipo=c_tipo, costo=c_costo, plan=c_plan, precio=c_precio, nombre=c_nombre, estado=c_estado))
    if c_tipo:
        sel = seguro(odoo.call, "crm.brief_approved", "fields_get", attributes=["selection"], allfields=[c_tipo])
        print("   Valores posibles de Tipo:", (sel or {}).get(c_tipo, {}).get("selection"))
    if c_tipo and c_costo and c_plan:
        pedir2 = [c for c in ("create_date", c_tipo, c_costo, c_plan, c_precio, c_nombre, c_estado) if c]
        b2 = pd.DataFrame(seguro(ic.leer_todo, odoo, "crm.brief_approved", [], pedir2) or [])
        if len(b2):
            b2["creado"] = ic._local(b2["create_date"])
            b2["mes"] = ym(b2["creado"])
            etiquetas = {str(a): b for a, b in ((sel or {}).get(c_tipo, {}).get("selection") or [])}
            b2["tipo_"] = b2[c_tipo].astype(str).map(lambda x: etiquetas.get(x, x))
            b2["plan_id"] = pd.to_numeric(b2[c_plan].map(iv.m2o_id), errors="coerce").fillna(0).astype(int)
            b2["cuenta_id"] = b2["plan_id"]  # el plan identifica el proyecto
            print(f"\n   Briefs aprobados: {len(b2)}")
            print("   Por tipo y estado:")
            print(b2.groupby(["tipo_"] + ([c_estado] if c_estado else [])).size().to_string())
            print("\n   Por mes de creacion y tipo:")
            print(b2[b2["mes"] >= 202602].pivot_table(index="mes", columns="tipo_", values=c_costo,
                                                      aggfunc="count", fill_value=0).to_string())
            es_gar = b2["tipo_"].str.contains("garant", case=False, na=False)
            # Definicion de Diforma: solo cuentan Produccion y Prototipo Facturable
            es_prod = b2["tipo_"].str.contains(r"^producci|facturable", case=False, na=False, regex=True)
            print("   Tipos que cuentan como ejecutados:", sorted(b2.loc[es_prod, "tipo_"].unique()))
            ids = sorted({int(i) for i in b2["plan_id"] if i})  # incluye garantias para medir su costo real
            f_cu = campos_de(odoo, "account.analytic.account")
            dom_cu = ["|", ["plan_id", "in", ids], ["root_plan_id", "in", ids]] if "root_plan_id" in f_cu else [["plan_id", "in", ids]]
            cu = pd.DataFrame(seguro(ic.leer_todo, odoo, "account.analytic.account", dom_cu,
                                     [c for c in ("name", "plan_id", "root_plan_id") if c in f_cu]) or [])
            print(f"\n   Planes de brief: {len(ids)}   cuentas analiticas dentro de ellos: {len(cu)}")
            cuenta_a_plan = {}
            for _, r in cu.iterrows():
                p = iv.m2o_id(r["plan_id"])
                if p not in ids and "root_plan_id" in cu:
                    p = iv.m2o_id(r["root_plan_id"])
                cuenta_a_plan[int(r["id"])] = int(p) if p else 0
            if len(cu):
                print("   Ejemplo:", cu.head(5)[["name"]].to_dict("records"))
            aids = sorted(cuenta_a_plan)
            lin = []
            for i in range(0, len(aids), 500):
                lin += seguro(ic.leer_todo, odoo, "account.analytic.line", [["account_id", "in", aids[i:i + 500]]],
                              ["account_id", "amount", "date"]) or []
            real = pd.DataFrame(lin)
            print(f"   Apuntes analiticos en esos planes: {len(real)}")
            if len(real):
                real["cid"] = real["account_id"].map(iv.m2o_id).map(cuenta_a_plan)
                costo_real = real[real["amount"] < 0].groupby("cid")["amount"].sum().mul(-1)
                sin_plan = (b2.loc[es_prod, "cuenta_id"] == 0).sum() if "cuenta_id" in b2 else 0
                nn = b2[es_prod].copy()
                nn["real"] = nn["cuenta_id"].map(costo_real).fillna(0)
                nn["proy"] = pd.to_numeric(nn[c_costo], errors="coerce").fillna(0)
                print(f"\n   Sin plan analitico (solo Produccion y Prototipo Facturable): {sin_plan}")
                print("   Costo proyectado vs real por mes de creacion del brief (solo Produccion y Prototipo Facturable):")
                g = nn[nn["mes"] >= 202602].groupby("mes").agg(proyectos=("proy", "count"), proy=("proy", "sum"), real=("real", "sum"))
                g["ahorro_%"] = ((g["proy"] - g["real"]) / g["proy"].where(g["proy"] > 0) * 100).round(1)
                print(g.round(0).to_string())
                print("\n   Ultimos 15 proyectos: nombre | proyectado | real | ahorro %")
                for _, r in nn.sort_values("creado").tail(15).iterrows():
                    a = (r["proy"] - r["real"]) / r["proy"] * 100 if r["proy"] else float("nan")
                    print(f"     {str(r.get(c_nombre, ''))[:38]:38s} {r['proy']:>14,.0f} {r['real']:>14,.0f} {a:7.1f}")

                # ---- costo de las garantias (su plan analitico acumula el costo real)
                print("\n   COSTO DE LAS GARANTIAS (costo real de su plan analitico):")
                gg = b2[es_gar].copy()
                gg["real"] = gg["cuenta_id"].map(costo_real).fillna(0)
                print(f"   Garantias: {len(gg)}   costo real total: {gg['real'].sum():,.0f}")
                gm = gg[gg["mes"] >= 202602].groupby("mes").agg(garantias=("real", "count"), costo=("real", "sum"))
                print(gm.round(0).to_string())
                print("   Detalle (mas recientes): brief | creado | costo real")
                for _, r in gg.sort_values("creado").tail(25).iterrows():
                    print(f"     {str(r.get(c_nombre, ''))[:42]:42s} {r['creado']:%Y-%m-%d} {r['real']:>14,.0f}")

                # ---- proyectos ejecutados = facturados en el mes, sin garantias
                print("\n   PROYECTOS EJECUTADOS (facturados, solo Produccion y Prototipo Facturable), por mes de PRIMERA factura:")
                ml = pd.DataFrame(seguro(ic.leer_todo, odoo, "account.move.line",
                                         [["move_id.move_type", "=", "out_invoice"], ["parent_state", "=", "posted"],
                                          ["date", ">=", DESDE], ["analytic_distribution", "!=", False]],
                                         ["date", "analytic_distribution"]) or [])
                if len(ml):
                    filas_ej = []
                    for _, r in ml.iterrows():
                        d = r["analytic_distribution"]
                        if isinstance(d, dict):
                            for k in d:
                                for x in str(k).split(","):
                                    if x.strip().isdigit():
                                        filas_ej.append((int(x), r["date"]))
                    ej = pd.DataFrame(filas_ej, columns=["cid", "fecha"])
                    ej["cid"] = ej["cid"].map(cuenta_a_plan)  # cuenta analitica -> plan del brief
                    ej = ej.dropna(subset=["cid"])
                    ej["cid"] = ej["cid"].astype(int)
                    tipo_de = dict(zip(b2["cuenta_id"], b2["tipo_"]))
                    ej["tipo"] = ej["cid"].map(tipo_de)
                    ej = ej[ej["tipo"].notna()]
                    ej = ej[ej["tipo"].str.contains(r"^producci|facturable", case=False, na=False, regex=True)]
                    pri = ej.groupby("cid")["fecha"].min().reset_index()
                    pri["mes"] = ym(pri["fecha"])
                    cuenta_ej = pri[pri["mes"] >= 202602].groupby("mes").size()
                    print("   mes      ejecutados Odoo   (hoja implicita: abril 12, septiembre 9)")
                    for mes_, n_ in cuenta_ej.items():
                        print(f"   {mes_}   {n_:>8}")
                    todas = ej.assign(mes=ym(ej["fecha"])).groupby("mes")["cid"].nunique()
                    print("   Alternativa: proyectos distintos con alguna factura en el mes:")
                    print(todas[todas.index >= 202602].to_string())
                else:
                    print("   (no hay lineas con distribucion analitica; habra que enlazar factura-proyecto de otra forma)")

    # ------------------------------------------------------------------ C. Garantias
    titulo("C) Garantias: etiquetas, campos y tipos con la palabra garantia / reclamo / reproceso")
    tags = seguro(odoo.search_read, "crm.tag", [], ["name"]) or []
    print("   Etiquetas crm.tag:", [t["name"] for t in tags])
    print("   Coinciden:", [t["name"] for t in tags if RE_GAR.search(t["name"] or "")])
    f_lead = campos_de(odoo, "crm.lead")
    cand = {k: v for k, v in f_lead.items() if RE_GAR.search(k) or RE_GAR.search(v.get("string", ""))}
    print("   Campos de crm.lead que parecen garantia:", {k: v["string"] for k, v in cand.items()} or "ninguno")
    for k, v in f_ba.items():
        if RE_GAR.search(k) or RE_GAR.search(v.get("string", "")):
            print("   Campo en crm.brief_approved:", k, "-", v["string"])
    tipos = [k for k, v in f_lead.items() if v["type"] == "selection" and k in ("tipo", "tipo_brief", "tipo_etapa", "tipo_proyecto")]
    for k in tipos:
        sel = seguro(odoo.call, "crm.lead", "fields_get", attributes=["selection"], allfields=[k])
        print(f"   Valores de {k}:", (sel or {}).get(k, {}).get("selection"))
    gar_ids = [t["id"] for t in tags if RE_GAR.search(t["name"] or "")]
    gar = pd.DataFrame()
    if gar_ids and "tag_ids" in f_lead:
        filas = seguro(ic.leer_todo, odoo, "crm.lead", [["tag_ids", "in", gar_ids], ["create_date", ">=", DESDE]],
                       [c for c in ("name", "create_date", "stage_id", "tag_ids", "proyecto", "letra") if c in f_lead])
        gar = pd.DataFrame(filas or [])
        print(f"\n   Briefs con etiqueta de garantia desde {DESDE}: {len(gar)}")
        if len(gar):
            gar["mes"] = ym(ic._local(gar["create_date"]))
            print(gar.groupby("mes").size().to_string())
            print(gar.head(15).to_string(max_colwidth=40))

    # ------------------------------------------------------------------ D. Proyectos ejecutados (facturados)
    titulo("D) Proyectos facturados por mes (facturas de cliente contabilizadas)")
    f_mv = campos_de(odoo, "account.move")
    cols = [c for c in ("name", "invoice_date", "date", "move_type", "state", "partner_id", "amount_untaxed_signed",
                        "invoice_origin", "ref", "company_id", "currency_id", "invoice_line_ids") if c in f_mv]
    cols = [c for c in cols if c != "invoice_line_ids"]
    # campos de proyecto en facturas
    cand_p = {k: v["string"] for k, v in f_mv.items() if re.search(r"proyec|brief|analyt", k + v.get("string", ""), re.I)}
    print("   Campos de account.move relacionados con proyecto/brief/analitica:", cand_p or "ninguno")
    mv = pd.DataFrame(seguro(ic.leer_todo, odoo, "account.move",
                             [["move_type", "in", ["out_invoice", "out_refund"]], ["state", "=", "posted"],
                              ["invoice_date", ">=", DESDE]], cols + [c for c in cand_p if c not in cols]) or [])
    if len(mv):
        mv["mes"] = ym(mv["invoice_date"])
        fa = mv[mv["move_type"] == "out_invoice"]
        print(f"   Facturas de cliente contabilizadas: {len(fa)}")
        print("   Por mes: facturas / clientes distintos")
        resumen = fa.groupby("mes").agg(facturas=("name", "count"),
                                        clientes=("partner_id", lambda s: s.map(iv.m2o_id).nunique()))
        print(resumen.to_string())

    # lineas con cuenta analitica / plan
    titulo("E) Lineas de factura de cliente con distribucion analitica (candidato: 'proyecto' = cuenta analitica)")
    f_ml = campos_de(odoo, "account.move.line")
    print("   Campos analiticos en account.move.line:",
          {k: v["string"] for k, v in f_ml.items() if "analytic" in k})
    planes = seguro(odoo.search_read, "account.analytic.plan", [], ["name", "parent_id"]) or []
    print("   Planes analiticos:", [(p["id"], p["name"]) for p in planes])
    cuentas = seguro(odoo.search_read, "account.analytic.account", [], ["name", "plan_id", "company_id"]) or []
    ca = pd.DataFrame(cuentas)
    if len(ca):
        ca["plan"] = ca["plan_id"].map(iv.m2o)
        print("   Cuentas analiticas por plan:")
        print(ca.groupby("plan").size().to_string())
        print(ca.head(12).to_string(max_colwidth=30))

    # ------------------------------------------------------------------ F. Costo real (lineas analiticas)
    titulo("F) Costo real: apuntes analiticos (account.analytic.line) por plan y mes")
    f_al = campos_de(odoo, "account.analytic.line")
    cols_al = [c for c in ("name", "date", "amount", "account_id", "plan_id", "general_account_id", "partner_id",
                           "move_line_id", "company_id", "category") if c in f_al]
    al = pd.DataFrame(seguro(ic.leer_todo, odoo, "account.analytic.line", [["date", ">=", DESDE]], cols_al) or [])
    print(f"   Apuntes analiticos desde {DESDE}: {len(al)}")
    if len(al):
        al["mes"] = ym(al["date"])
        al["cuenta"] = al["account_id"].map(iv.m2o)
        if len(ca):
            al = al.merge(ca[["id", "plan"]].rename(columns={"id": "_cid"}), left_on=al["account_id"].map(iv.m2o_id),
                          right_on="_cid", how="left")
        pv = al.pivot_table(index="plan" if "plan" in al else "cuenta", columns="mes", values="amount",
                            aggfunc="sum", fill_value=0)
        print("   Suma de 'amount' (negativo = costo) por plan y mes:")
        print(pv.round(0).to_string())
        print("\n   Costos (amount<0) por mes en total vs hoja:")
        costo = al[al["amount"] < 0].groupby("mes")["amount"].sum().mul(-1)
        for mes, h in HOJA.items():
            print(f"     {mes}: Odoo {costo.get(mes, 0):>15,.0f}   hoja costo real {h['real']:>15,.0f}")

    # ------------------------------------------------------------------ G. Proveedores nuevos
    titulo("G) Proveedores nuevos: contactos con rango de proveedor, por mes de creacion")
    f_rp = campos_de(odoo, "res.partner")
    cols_p = [c for c in ("name", "create_date", "supplier_rank", "customer_rank", "is_company", "company_id", "category_id",
                          "active") if c in f_rp]
    dom_p = [["supplier_rank", ">", 0]] if "supplier_rank" in f_rp else []
    pr = pd.DataFrame(seguro(ic.leer_todo, odoo, "res.partner", dom_p + [["create_date", ">=", DESDE]], cols_p) or [])
    print(f"   Proveedores creados desde {DESDE}: {len(pr)}")
    if len(pr):
        pr["creado"] = ic._local(pr["create_date"])
        pr["mes"] = ym(pr["creado"])
        print(pr.groupby("mes").size().to_string())
        print("   (ojo: cargas masivas, p. ej. 2026-01-01..04, inflan el conteo)")
        print("   Dias con mas de 20 creaciones:")
        dia = pr.groupby(pr["creado"].dt.date).size()
        print(dia[dia > 20].to_string() or "   ninguno")
        for mes in sorted(pr["mes"].unique())[-9:]:
            nombres = pr.loc[pr["mes"] == mes, "name"].tolist()
            print(f"   {mes}: {len(nombres)} -> {nombres[:12]}")

    # primera factura de proveedor (alternativa)
    fb = pd.DataFrame(seguro(ic.leer_todo, odoo, "account.move",
                             [["move_type", "=", "in_invoice"], ["state", "=", "posted"]],
                             ["partner_id", "invoice_date"]) or [])
    if len(fb):
        fb["pid"] = fb["partner_id"].map(iv.m2o_id)
        primera = fb.groupby("pid")["invoice_date"].min().reset_index()
        primera["mes"] = ym(primera["invoice_date"])
        print("\n   Alternativa (primera factura de proveedor por mes; incluye carga inicial):")
        print(primera[primera["mes"] >= 202602].groupby("mes").size().to_string())

    # ------------------------------------------------------------------ H. Resumen contra la hoja
    titulo("H) Lo que dice la hoja (para calibrar con lo anterior)")
    print("   mes      garantias  %cero   costo proyectado   costo real     %eficiencia")
    for mes, h in HOJA.items():
        calc = (h["proy"] - h["real"]) / h["proy"] * 100
        print(f"   {mes}   {h['gar']:>5}   {h['cero']:6.1f}   {h['proy']:>15,.0f}   {h['real']:>14,.0f}   {h['efi']:5.1f}  (con sus cifras: {calc:5.1f})")
    print("\nFIN. Envie diagnostico_produccion.txt para continuar.")


if __name__ == "__main__":
    main()
