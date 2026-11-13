#!/usr/bin/env python3
"""
Indicadores mensuales del area administrativa - Grupo Diforma SAS (Odoo 19)

Calcula desde la contabilidad de Odoo (asientos contabilizados) los componentes de los
8 indicadores de la hoja "Indicadores Area (Administrativa)", por empresa y por mes:

  razon      Razon de endeudamiento      = Pasivo total / Activo total
  fin        Endeudamiento financiero    = Obligaciones financieras (cuentas 21*) / Activo total
  apal       Apalancamiento total        = Pasivo total / Patrimonio (incluye resultado del ejercicio)
  rot        Rotacion de cuentas x cobrar= Ventas a credito acumuladas / saldo promedio CxC (anualizada)
  cobro      Dias promedio de cobro      = dias transcurridos x saldo promedio CxC / ventas a credito
  pago       Dias de mora al pagar a proveedores (pagos del mes vs. fecha de vencimiento)
  ciclo      Ciclo de efectivo           = dias de cobro + dias de inventario (cuentas 14* / costo de ventas)
  bienestar  Manual (no sale de Odoo)

Aqui solo se calculan los componentes (saldos y flujos); los cocientes se calculan en el
dashboard para poder filtrar por empresa o ver todas juntas. Solo lee datos.

Uso directo (verificacion):  python indicadores_admin.py [--anio 2026]
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import informe_ventas as iv  # noqa: E402

CONFIG = Path(__file__).resolve().parent / "metas_admin.json"
IND = ["razon", "fin", "apal", "rot", "cobro", "pago", "ciclo", "bienestar"]
CAMPOS = ["act", "pas", "pat", "fin", "cxc", "cxp", "inv", "vc", "comp", "cogs", "pa", "pd"]
CTX = {"active_test": False}


def cargar_config():
    with open(CONFIG, encoding="utf-8-sig") as f:
        return json.load(f)


def leer(odoo, modelo, dominio, campos, lote=4000):
    filas, offset = [], 0
    while True:
        b = odoo.call(modelo, "search_read", domain=dominio, fields=campos, offset=offset,
                      limit=lote, order="id", context=CTX)
        filas.extend(b)
        if len(b) < lote:
            return filas
        offset += lote


def _id(v):
    return v[0] if isinstance(v, (list, tuple)) and v else None


def _tipo_puc(codigo, tipo):
    """Normaliza el tipo de cuenta segun el PUC colombiano: la clase (primer digito del codigo) manda.
    1 activo, 2 pasivo, 3 patrimonio, 4 ingresos, 5/6/7 gastos y costos. En Odoo hay cuentas con el
    tipo mal configurado (p.ej. prestaciones sociales 25* como 'activo'); se clasifican por su codigo.
    Si el tipo de Odoo ya es de la misma familia se respeta (conserva 'por cobrar', 'por pagar', 'costo directo')."""
    c = (codigo or "")[:1]
    familias = {"1": "asset", "2": "liability", "3": "equity", "4": "income", "5": "expense", "6": "expense", "7": "expense"}
    fam = familias.get(c)
    if not fam or (tipo or "").startswith(fam):
        return tipo
    return {"asset": "asset_current", "liability": "liability_current", "equity": "equity",
            "income": "income", "expense": "expense"}[fam]


def _claves(codigo, tipo, cfg):
    """Que acumuladores toca una linea contable (segun cuenta)."""
    ks = []
    if tipo.startswith("asset"):
        ks.append("act")
        if any(codigo.startswith(p) for p in cfg.get("inventario_prefijos", ["14"])):
            ks.append("inv")
        if tipo == "asset_receivable":
            ks.append("cxc")
    elif tipo.startswith("liability"):
        ks.append("pas")
        if any(codigo.startswith(p) for p in cfg.get("obligaciones_financieras_prefijos", ["21"])):
            ks.append("fin")
        if tipo == "liability_payable":
            ks.append("cxp")
    elif tipo.startswith("equity"):
        ks.append("eq")
    elif tipo.startswith("income") or tipo.startswith("expense"):
        ks.append("res")
    return ks


def _nom(v, defecto="(sin tercero)"):
    return v[1] if isinstance(v, (list, tuple)) and len(v) > 1 else defecto


def calcular(odoo, anio, cfg, avisos):
    """Devuelve dict listo para el dashboard (componentes y detalle por empresa y mes)."""
    ahora = datetime.now()
    empresas = {c["id"]: c["name"] for c in leer(odoo, "res.company", [], ["name"])}
    # El codigo de la cuenta depende de la empresa (plan de cuentas por compania): se lee una vez por empresa.
    cuentas = {}   # (id_empresa, id_cuenta) -> (codigo, tipo)
    nombres = {}
    tipos = {}
    for cid in empresas:
        ctx = dict(CTX, allowed_company_ids=[cid])
        try:
            filas = odoo.call("account.account", "search_read", domain=[],
                              fields=["code", "name", "display_name", "account_type"], limit=100000, context=ctx)
        except (RuntimeError, requests.RequestException):
            filas = odoo.call("account.account", "search_read", domain=[], fields=["display_name", "account_type"],
                              limit=100000, context=ctx)
        for a in filas:
            cod, nom = a.get("code") or "", a.get("name") or ""
            if not cod:
                m = re.match(r"\s*(\S+)\s+(.*)", a.get("display_name") or "")
                cod, nom = (m.group(1), m.group(2)) if m else ("", a.get("display_name") or "")
            cuentas[(cid, a["id"])] = (cod, a.get("account_type") or "")
            nombres[(cid, a["id"])] = nom
            tipos[a["id"]] = a.get("account_type") or ""

    lineas = leer(odoo, "account.move.line",
                  [("parent_state", "=", "posted"), ("date", "<=", f"{anio}-12-31")],
                  ["date", "account_id", "balance", "company_id", "partner_id"])
    ini = f"{anio}-01-01"
    del_anio = [l for l in lineas if l["date"] >= ini]
    if len(del_anio) < 50:
        return {"ok": False}
    ult_fecha = max(l["date"] for l in lineas)
    ultimo = 12 if ult_fecha[:4] > str(anio) else (int(ult_fecha[5:7]) if ult_fecha[:4] == str(anio) else 12)
    if anio == ahora.year:
        ultimo = min(ultimo, ahora.month)

    base = defaultdict(lambda: defaultdict(float))                       # cid -> clave -> saldo previo al ano
    mov = defaultdict(lambda: defaultdict(lambda: [0.0] * 13))           # cid -> clave -> [mes] flujo del ano
    cogs = defaultdict(lambda: [0.0] * 13)
    gpref = defaultdict(lambda: defaultdict(lambda: [0.0] * 13))         # cid -> (lado, prefijo2) -> [0=base, 1..12]
    cta = defaultdict(lambda: defaultdict(lambda: [0.0] * 13))           # cid -> (lado, id_cuenta) -> [0=base, 1..12]
    ter = defaultdict(lambda: defaultdict(lambda: [0.0] * 13))           # cid -> (grupo, tercero) -> [0=base, 1..12]
    at = defaultdict(lambda: defaultdict(lambda: [0.0] * 13))            # cid -> (codigo, tercero) -> [0=base, 1..12]
    nmcod = {}                                                           # codigo -> nombre de la cuenta
    CLASES_AT = ("13", "21", "22", "23", "24", "25", "26", "28")          # cuentas con terceros: quien nos debe / a quien debemos
    for l in lineas:
        cid = _id(l["company_id"])
        aid = _id(l["account_id"])
        codigo, tipo = cuentas.get((cid, aid), ("", tipos.get(aid, "")))
        tipo = _tipo_puc(codigo, tipo)
        ks = _claves(codigo, tipo, cfg)
        idx = 0 if l["date"] < ini else int(l["date"][5:7])
        bal = l["balance"]
        for k in ks:
            if idx == 0:
                base[cid][k] += bal
            else:
                mov[cid][k][idx] += bal
        if idx and tipo == "expense_direct_cost":
            cogs[cid][idx] += bal
        lado = "act" if tipo.startswith("asset") else "pas" if tipo.startswith("liability") else \
            "eq" if tipo.startswith("equity") else None
        if lado:
            gpref[cid][(lado, (codigo[:2] or "??"))][idx] += bal
            if lado != "eq":
                cta[cid][(lado, aid)][idx] += bal
        g = None
        if tipo.startswith("liability"):
            g = "fin" if any(codigo.startswith(p) for p in cfg.get("obligaciones_financieras_prefijos", ["21"])) \
                else ("prov" if tipo == "liability_payable" else "otp")
        elif tipo == "asset_receivable":
            g = "cxc"
        if g:
            ter[cid][(g, _nom(l.get("partner_id")))][idx] += bal
        if codigo[:2] in CLASES_AT:
            at[cid][(codigo, _nom(l.get("partner_id")))][idx] += bal
            nmcod.setdefault(codigo, nombres.get((cid, aid), ""))

    # --- facturas del ano: ventas a credito y compras ---
    vc = defaultdict(lambda: [0.0] * 13)
    comp = defaultdict(lambda: [0.0] * 13)
    try:
        facs = leer(odoo, "account.move",
                    [("move_type", "in", ["out_invoice", "in_invoice", "in_refund"]), ("state", "=", "posted"),
                     ("invoice_date", ">=", ini), ("invoice_date", "<=", f"{anio}-12-31")],
                    ["move_type", "invoice_date", "invoice_date_due", "amount_total_signed", "company_id"])
        for f in facs:
            cid, mes = _id(f["company_id"]), int(f["invoice_date"][5:7])
            if f["move_type"] == "out_invoice":
                if f["invoice_date_due"] and f["invoice_date_due"] > f["invoice_date"]:
                    vc[cid][mes] += f["amount_total_signed"]
            else:
                comp[cid][mes] += abs(f["amount_total_signed"]) * (-1 if f["move_type"] == "in_refund" else 1)
    except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
        avisos.append("Administrativa: no se pudieron leer las facturas (ventas a credito/compras): " + str(e)[:200])

    # --- pagos a proveedores: dias de mora frente al vencimiento, por mes de pago ---
    pa = defaultdict(lambda: [0.0] * 13)
    pd = defaultdict(lambda: [0.0] * 13)
    try:
        parts = leer(odoo, "account.partial.reconcile", [("max_date", ">=", ini), ("max_date", "<=", f"{anio}-12-31")],
                     ["debit_move_id", "credit_move_id", "amount", "max_date"])
        ids = list({_id(x[k]) for x in parts for k in ("debit_move_id", "credit_move_id")})
        lin = {}
        for i in range(0, len(ids), 3000):
            for r in odoo.call("account.move.line", "read", ids=ids[i:i + 3000], fields=["move_id"], context=CTX):
                lin[r["id"]] = _id(r["move_id"])
        mids = list(set(v for v in lin.values() if v))
        movs = {}
        for i in range(0, len(mids), 3000):
            for r in odoo.call("account.move", "read", ids=mids[i:i + 3000],
                               fields=["move_type", "invoice_date", "invoice_date_due", "company_id"], context=CTX):
                movs[r["id"]] = r
        for x in parts:
            for k in ("debit_move_id", "credit_move_id"):
                mv = movs.get(lin.get(_id(x[k])))
                if mv and mv["move_type"] == "in_invoice" and mv["invoice_date"]:
                    venc = datetime.fromisoformat(mv["invoice_date_due"] or mv["invoice_date"])
                    atraso = (datetime.fromisoformat(x["max_date"]) - venc).days
                    cid, mes = _id(mv["company_id"]), int(x["max_date"][5:7])
                    pa[cid][mes] += x["amount"]
                    pd[cid][mes] += x["amount"] * atraso
    except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
        avisos.append("Administrativa: no se pudieron leer las conciliaciones (dias de pago): " + str(e)[:200])

    # --- saldos abiertos HOY por tercero (cartera y proveedores) con sus facturas ---
    antig, abiertos = {}, {}
    try:
        ab = leer(odoo, "account.move.line",
                  [("parent_state", "=", "posted"), ("reconciled", "=", False),
                   ("account_id.account_type", "in", ["asset_receivable", "liability_payable"])],
                  ["account_id", "company_id", "partner_id", "move_id", "date", "date_maturity", "amount_residual"])
        cubos = ["nv", "d30", "d60", "d90", "d91"]
        por = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {
            "ab": 0.0, "fav": 0.0, "apl": 0.0, "ant": 0.0, "antsob": 0.0, "nv": 0.0, "d30": 0.0, "d60": 0.0, "d90": 0.0, "d91": 0.0, "pond": 0.0, "base": 0.0, "docs": []})))
        anticipos = defaultdict(dict)                       # empresa -> tercero -> anticipo recibido (28*, saldo acreedor)
        for cid_, d_ in at.items():
            for (cod_, t_), arr_ in d_.items():
                if cod_.startswith("28"):
                    anticipos[empresas.get(cid_)][t_] = anticipos[empresas.get(cid_)].get(t_, 0.0) - sum(arr_)
        crudo = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"fav": 0.0, "items": []})))
        for r in ab:
            nombre = empresas.get(_id(r["company_id"]))
            lado = "cxc" if tipos.get(_id(r["account_id"]), "") == "asset_receivable" else "cxp"
            v = r["amount_residual"] * (1 if lado == "cxc" else -1)
            t = crudo[nombre][lado][_nom(r.get("partner_id"))]
            if v <= 0:
                t["fav"] += v
            else:
                t["items"].append((r.get("date_maturity") or r["date"], r["date"], _nom(r.get("move_id"), "(sin documento)"), v))
        for nombre, lados in crudo.items():
            for lado, ts in lados.items():
                for tn, c in ts.items():
                    # los pagos / anticipos / notas sin conciliar (saldo a favor) se aplican a las facturas mas antiguas
                    credito = -c["fav"]
                    aplicado = 0.0
                    antic = max(anticipos.get(nombre, {}).get(tn, 0.0), 0.0) if lado == "cxc" else 0.0
                    ant_ap = 0.0
                    t = por[nombre][lado][tn]
                    for venc, fecha, doc, v in sorted(c["items"]):
                        if credito > 0:
                            usar = min(credito, v)
                            credito -= usar
                            aplicado += usar
                            v -= usar
                        if antic > 0 and v > 0:        # anticipos recibidos del cliente (28*) aplicados a sus facturas
                            usar = min(antic, v)
                            antic -= usar
                            ant_ap += usar
                            v -= usar
                        if v <= 0.5:
                            continue
                        d = (ahora.date() - datetime.fromisoformat(venc).date()).days
                        cb = "nv" if d <= 0 else "d30" if d <= 30 else "d60" if d <= 60 else "d90" if d <= 90 else "d91"
                        t["ab"] += v
                        t[cb] += v
                        if d > 0:
                            t["pond"] += v * d
                            t["base"] += v
                        t["docs"].append([doc, fecha, venc, round(v)])
                        a = antig.setdefault(nombre, {}).setdefault(lado, {k: 0 for k in cubos + ["pond", "base"]})
                        a[cb] += v
                        if d > 0:
                            a["pond"] += v * d
                            a["base"] += v
                    t["fav"] -= credito          # lo que sobra a favor del tercero tras aplicar
                    t["apl"] += aplicado
                    t["ant"] += ant_ap
                    t["antsob"] += antic
        for e in antig.values():
            for a in e.values():
                for k in a:
                    a[k] = round(a[k])
        for nombre, lados in por.items():
            for lado, ts in lados.items():
                filas = sorted(ts.items(), key=lambda x: -x[1]["ab"])
                lista = []
                for t, d in filas:
                    docs = sorted(d["docs"], key=lambda x: x[2])       # todas, las que vencen primero al inicio
                    lista.append({"t": t, "n": len(d["docs"]), "docs": docs,
                                  **{k: round(d[k]) for k in ("ab", "fav", "apl", "ant", "antsob", "nv", "d30", "d60", "d90", "d91", "pond", "base")}})
                resto = []
                if resto:
                    lista.append({"t": f"(otros {len(resto)} terceros)", "n": 0, "docs": [],
                                  **{k: round(sum(d[k] for _, d in resto)) for k in
                                     ("ab", "fav", "apl", "ant", "antsob", "nv", "d30", "d60", "d90", "d91", "pond", "base")}})
                abiertos.setdefault(nombre, {})[lado] = lista
    except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
        avisos.append("Administrativa: no se pudo calcular el detalle de saldos abiertos: " + str(e)[:200])

    # --- armar series por empresa (None en meses futuros) ---
    def acum(cid, k, signo=1):
        out, s = [], base[cid][k]
        for m in range(1, 13):
            s += mov[cid][k][m]
            out.append(round(signo * s) if m <= ultimo else None)
        return out

    def flujo(arr, signo=1):
        return [round(signo * arr[m]) if m <= ultimo else None for m in range(1, 13)]

    def cum13(arr, signo):
        out, s = [], arr[0]
        for m in range(1, 13):
            s += arr[m]
            out.append(round(signo * s) if m <= ultimo else None)
        return out

    datos_emp = {}
    for cid, nombre in empresas.items():
        eq, res = acum(cid, "eq", -1), acum(cid, "res", -1)
        grp = {"act": {}, "pas": {}, "pat": {}}
        for (lado, pref), arr in gpref[cid].items():
            sg = 1 if lado == "act" else -1
            v = cum13(arr, sg)
            if any(abs(x or 0) > 500 for x in v):
                grp["pat" if lado == "eq" else lado][pref] = v
        grp["pat"]["res"] = res
        ctas = []
        for (lado, aid), arr in cta[cid].items():
            v = cum13(arr, 1 if lado == "act" else -1)
            if any(abs(x or 0) > 1000 for x in v):
                ctas.append([lado, cuentas.get((cid, aid), ("", ""))[0], nombres.get((cid, aid), ""), v])
        ctas.sort(key=lambda x: -abs(next((y for y in reversed(x[3]) if y is not None), 0)))
        ctas = [c for c in ctas if c[0] == "act"][:60] + [c for c in ctas if c[0] == "pas"][:60]
        filas_at = []
        for (cod, t), arr in at[cid].items():
            v = cum13(arr, 1)
            m = max(abs(y or 0) for y in v)
            if m > 1000:
                filas_at.append([cod, t, [None if y is None else round(y) for y in v], m])
        filas_at.sort(key=lambda x: -x[3])
        top_at, resto_at = filas_at[:600], filas_at[600:]
        agr = defaultdict(lambda: [0.0] * 12)
        for cod, t, v, m in resto_at:
            for k in range(12):
                agr[cod][k] += v[k] or 0
        atl = [[c, t, v] for c, t, v, m in top_at]
        atl += [[cod, f"(otros terceros de {cod})", [round(a[k]) for k in range(12)]] for cod, a in agr.items()]
        terc = {}
        for g in ("fin", "prov", "otp", "cxc"):
            sg = 1 if g == "cxc" else -1
            filas = [(t, cum13(arr, sg)) for (gg, t), arr in ter[cid].items() if gg == g]
            filas = [(t, v) for t, v in filas if any(abs(x or 0) > 1000 for x in v)]
            filas.sort(key=lambda x: -max(abs(y or 0) for y in x[1]))
            top, resto = filas[:30], filas[30:]
            lista = [[t, v] for t, v in top]
            if resto:
                lista.append([f"(otros {len(resto)} terceros)",
                              [None if top[0][1][m] is None else round(sum((v[m] or 0) for _, v in resto)) for m in range(12)]])
            terc[g] = lista
        datos_emp[nombre] = {
            "act": acum(cid, "act"), "pas": acum(cid, "pas", -1),
            "pat": [None if a is None else a + b for a, b in zip(eq, res)],
            "fin": acum(cid, "fin", -1), "cxc": acum(cid, "cxc"), "cxp": acum(cid, "cxp", -1),
            "inv": acum(cid, "inv"),
            "vc": flujo(vc[cid]), "comp": flujo(comp[cid]), "cogs": flujo(cogs[cid]),
            "pa": flujo(pa[cid]), "pd": flujo(pd[cid]),
            "grp": grp, "cuentas": ctas, "terc": terc, "at": atl, "nmcod": {c: nmcod.get(c, "") for c in {r[0] for r in atl}},
        }

    # dias transcurridos del ano hasta el cierre de cada mes (o hasta hoy en el mes en curso)
    dias = []
    for m in range(1, 13):
        sig = date(anio + (m == 12), m % 12 + 1, 1)
        d = (sig - date(anio, 1, 1)).days
        if anio == ahora.year and m == ahora.month:
            d = (ahora.date() - date(anio, 1, 1)).days + 1
        dias.append(d)

    manual = cfg.get("manual", {}).get("bienestar", {}).get(str(anio), [None] * 12)
    return {
        "ok": True, "ultimo": ultimo, "empresas": list(datos_emp), "m": datos_emp, "dias": dias,
        "hoy": ahora.date().isoformat(), "vinc": list(datos_emp),
        "meta": cfg["metas"], "tipo": cfg["tipo"],
        "amarillo_max": cfg.get("amarillo_max", 1.25), "amarillo_min": cfg.get("amarillo_min", 0.7),
        "manual_bienestar": (manual + [None] * 12)[:12],
        "hoja": {str(m): cfg.get("hoja", {}).get(f"{anio}-{m:02d}") for m in range(1, 13)},
        "antig": antig, "abiertos": abiertos,
    }


# --------------------------------------------------------------------------
def valores(adm, nombres, k):
    """Misma logica que el dashboard (JS), para verificar desde la consola. k = mes 0..11."""
    def mes(campo, j):
        return sum((adm["m"][n][campo][j] or 0) for n in nombres)

    def ytd(campo):
        return sum(mes(campo, j) for j in range(k + 1))
    act, pas, pat, fin = mes("act", k), mes("pas", k), mes("pat", k), mes("fin", k)
    cxc_prom = sum(mes("cxc", j) for j in range(k + 1)) / (k + 1)
    vc, cg, dias = ytd("vc"), ytd("cogs"), adm["dias"][k]
    cobro = dias * cxc_prom / vc if vc else None
    pa, pdv = mes("pa", k), mes("pd", k)
    inv_d = dias * mes("inv", k) / cg if cg > 0 else None
    return {
        "razon": pas / act if act else None, "fin": fin / act if act else None,
        "apal": pas / pat if pat > 0 else None,
        "rot": 365 / cobro if cobro else None, "cobro": cobro,
        "pago": pdv / pa if pa else None,
        "ciclo": cobro + inv_d if cobro is not None and inv_d is not None else None,
    }


def main():
    iv.cargar_env()
    ap = argparse.ArgumentParser(description="Indicadores del area administrativa (Odoo 19)")
    ap.add_argument("--anio", type=int, default=datetime.now().year)
    args = ap.parse_args()
    odoo = iv.Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])
    avisos = []
    adm = calcular(odoo, args.anio, cargar_config(), avisos)
    for a in avisos:
        print("AVISO:", a)
    if not adm.get("ok"):
        sys.exit("Sin datos contables.")
    for nombres, tit in [([n], n) for n in adm["empresas"]] + [(adm["empresas"], "TODAS")]:
        print(f"\n{tit}\nMes  " + " ".join(f"{i:>8}" for i in IND[:-1]))
        for k in range(adm["ultimo"]):
            v = valores(adm, nombres, k)
            print(f"{k + 1:>3}  " + " ".join(f"{(v[i] if v[i] is not None else float('nan')):8.2f}" for i in IND[:-1]))


if __name__ == "__main__":
    main()
