# -*- coding: utf-8 -*-
"""Diagnostico 3: detalle de costo real vs proyectado por proyecto (16849-C, 17471-A/B, 15658-L)."""
import os, sys, re
from collections import defaultdict
import informe_ventas as iv

PROYECTOS = ["16849-C", "17471-A", "17471-B", "15658-L"]
PMO = {"16849-C": (178022000, 155361729), "17471-B": (4090000, 9991709), "15658-L": (38400000, 40219552)}

def rd(odoo, model, dom, fields, limit=0):
    return odoo.call(model, "search_read", domain=dom, fields=fields, limit=limit, context={"active_test": False})

def main():
    iv.cargar_env()
    odoo = iv.Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])
    for p in PROYECTOS:
        print("=" * 100)
        print("PROYECTO", p, " PMO (proy, real):", PMO.get(p))
        planes = rd(odoo, "account.analytic.plan", [["name", "ilike", p]], ["name"])
        print(" planes:", [(x["id"], x["name"]) for x in planes])
        briefs = rd(odoo, "crm.brief_approved", [["name", "ilike", p]],
                    ["name", "tipo", "costo", "cantidad", "precio_venta", "state", "plan_analitico_id", "create_date"])
        for b in briefs:
            print("  BRIEF", b["id"], b["name"], "tipo", b["tipo"], "costo", b["costo"], "cant", b["cantidad"],
                  "pv", b["precio_venta"], "state", b["state"], "plan", b["plan_analitico_id"], b["create_date"])
        ids = [x["id"] for x in planes]
        if not ids:
            continue
        cuentas = rd(odoo, "account.analytic.account", ["|", ["plan_id", "in", ids], ["root_plan_id", "in", ids]], ["name"])
        cid = {c["id"]: c["name"] for c in cuentas}
        lines = rd(odoo, "account.analytic.line", [["account_id", "in", list(cid)]],
                   ["date", "name", "amount", "account_id", "ref", "move_line_id", "product_id", "general_account_id", "partner_id"])
        por = defaultdict(float)
        for l in lines:
            por[cid.get(l["account_id"][0] if l["account_id"] else 0, "?")] += l["amount"]
        print("  Neto por categoria:")
        for k, v in sorted(por.items(), key=lambda kv: kv[1]):
            print(f"     {k:<35}{v:>18,.0f}")
        grandes = sorted(lines, key=lambda l: -abs(l["amount"]))[:25]
        print("  25 apuntes mas grandes (fecha | categoria | monto | nombre | ref | cuenta contable | tercero):")
        for l in grandes:
            print("   ", l["date"], "|", cid.get(l["account_id"][0], "?"), "|", f"{l['amount']:,.0f}", "|",
                  (l["name"] or "")[:45], "|", (l["ref"] or "")[:25], "|",
                  iv.m2o(l.get("general_account_id")) if hasattr(iv, "m2o") else l.get("general_account_id"), "|",
                  iv.m2o(l.get("partner_id")) if hasattr(iv, "m2o") else l.get("partner_id"))
        # duplicados: mismo nombre y monto
        dup = defaultdict(list)
        for l in lines:
            dup[(l["name"], round(l["amount"]), l["date"])].append(l["id"])
        d = [(k, v) for k, v in dup.items() if len(v) > 1 and abs(k[1]) > 1000000]
        print("  Posibles duplicados (>1M):", d[:10])
    print("\nFIN. Envie diagnostico_produccion3.txt")

if __name__ == "__main__":
    main()
