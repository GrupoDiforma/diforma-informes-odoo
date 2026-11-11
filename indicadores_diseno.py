#!/usr/bin/env python3
"""
Indicadores mensuales del area de DISENO - Grupo Diforma SAS (Odoo 19)

Se calculan sobre el modulo personalizado "Diforma Brief" (modelo crm.lead). Definiciones:

  Presentado  = brief con "Fecha entrega real Diseno" (fecha_diseno_real) en el mes.
  Vendido     = brief que pasa a la etapa Ganado (Won) en el mes (historial de etapas del chatter).
  Devuelto    = brief que vuelve a la etapa Diseno (Design) desde una etapa posterior.
  Destacado   = presentado del mes con el campo "Diseno destacado" (dis_destacado).
  Novedad     = brief del mismo numero de proyecto con otra letra (17573-A -> 17573-B ...).

  efectividad = vendidos / presentados
  eficiencia  = proyectos presentados con al menos una novedad (letra distinta de A) / proyectos presentados
  devueltos   = devoluciones / presentados
  destacados  = destacados / presentados
  ia          = manual (no sale de Odoo)

Aqui solo se calculan los conteos por empresa y mes; los cocientes se calculan en el dashboard
para poder filtrar por empresa o ver todas juntas, y acumular el ano. Solo lee datos.

Uso directo (verificacion):  python indicadores_diseno.py [--anio 2026]
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import informe_ventas as iv  # noqa: E402
import indicadores_comercial as ic  # noqa: E402

CONFIG = Path(__file__).resolve().parent / "metas_diseno.json"
MODELO = "crm.lead"
IND = ["efectividad", "eficiencia", "devueltos", "destacados", "ia"]
MAXFILAS = 60


def cargar_config():
    with open(CONFIG, encoding="utf-8-sig") as f:
        return json.load(f)


def _ym(s):
    return pd.to_datetime(s, errors="coerce")


def _fecha_campo(serie):
    """Campos date de Odoo: False si esta vacio."""
    return pd.to_datetime(serie.where(serie.astype(bool)), errors="coerce")


def _idx_letra(letra):
    """A=0 (sin novedades), B=1, C=2 ..."""
    letra = str(letra or "").strip().upper()
    if not letra:
        return 0
    return sum(ord(c) - 65 + (26 if i else 0) for i, c in enumerate(letra[:2]))


def _leer_ids(odoo, modelo, ids, campos, lote=1000):
    out = []
    for i in range(0, len(ids), lote):
        out.extend(odoo.call(modelo, "read", ids=ids[i:i + lote], fields=campos))
    return out


def _cargar_briefs(odoo):
    info = odoo.call(MODELO, "fields_get", attributes=["string", "type"])
    quiero = ["name", "create_date", "stage_id", "active", "letra", "dis_destacado", "id_disenador",
              "fecha_diseno_real", "partner_id", "company_id"]
    campos = [c for c in quiero if c in info or c in ("name", "create_date")]
    df = pd.DataFrame(ic.leer_todo(odoo, MODELO, [], campos))
    if df.empty:
        return df
    df["etapa_id"] = df["stage_id"].map(iv.m2o_id) if "stage_id" in df else 0
    df["etapa"] = df["stage_id"].map(iv.m2o) if "stage_id" in df else ""
    nombre = df["name"].astype(str)
    df["num"] = pd.to_numeric(nombre.str.extract(r"^\s*(\d{3,6})")[0], errors="coerce").fillna(0).astype(int)
    letra_n = nombre.str.extract(r"^\s*\d{3,6}\s*-?\s*([A-Za-z]{1,2})\b")[0]
    if "letra" in df:
        letra_f = df["letra"].astype(str).where(df["letra"].astype(bool), None)
        letra = letra_f.fillna(letra_n)
    else:
        letra = letra_n
    df["letra_"] = letra.fillna("").str.upper().str.strip()
    df["lidx"] = df["letra_"].map(_idx_letra)
    df["dest"] = df["dis_destacado"].astype(bool) if "dis_destacado" in df else False
    df["f_real"] = _fecha_campo(df["fecha_diseno_real"]) if "fecha_diseno_real" in df else pd.NaT
    df["disenador"] = df["id_disenador"].map(lambda v: iv.m2o(v) or "(sin disenador)") if "id_disenador" in df else "(sin disenador)"
    df["cliente"] = df["partner_id"].map(lambda v: iv.m2o(v) or "") if "partner_id" in df else ""
    df["empresa"] = df["company_id"].map(lambda v: iv.m2o(v) or "(sin empresa)") if "company_id" in df else "Grupo Diforma S.A.S"
    return df


def _transiciones(odoo):
    """Cambios de etapa desde el chatter: DataFrame id, fecha, old, new."""
    tv = ic.leer_todo(odoo, "mail.tracking.value",
                      [["field_id.name", "=", "stage_id"], ["mail_message_id.model", "=", MODELO]],
                      ["mail_message_id", "old_value_integer", "new_value_integer", "create_date"])
    if not tv:
        return pd.DataFrame(columns=["id", "fecha", "old", "new"])
    t = pd.DataFrame(tv)
    mids = sorted({iv.m2o_id(x) for x in t["mail_message_id"]})
    mm = {m["id"]: m for m in _leer_ids(odoo, "mail.message", mids, ["res_id", "date"])}
    t["id"] = t["mail_message_id"].map(lambda x: mm.get(iv.m2o_id(x), {}).get("res_id"))
    t["fecha"] = ic._local(t["mail_message_id"].map(lambda x: mm.get(iv.m2o_id(x), {}).get("date")))
    t["old"] = t["old_value_integer"].fillna(0).astype(int)
    t["new"] = t["new_value_integer"].fillna(0).astype(int)
    t = t.dropna(subset=["id", "fecha"])
    t["id"] = t["id"].astype(int)
    return t[["id", "fecha", "old", "new"]].sort_values("fecha").reset_index(drop=True)


def calcular(odoo, anio, cfg, avisos):
    """Devuelve dict listo para el dashboard (conteos y detalle por empresa y mes)."""
    ahora = datetime.now()
    df = _cargar_briefs(odoo)
    if df.empty:
        return {"ok": False}
    etapas = {e["id"]: e["name"] for e in odoo.search_read("crm.stage", [], ["name", "sequence"], order="sequence asc")}
    busca = lambda *ts: {i for i, n in etapas.items() if n.lower().startswith(ts)}  # noqa: E731
    DIS, GAN = busca("design", "dise"), busca("won", "ganad")
    POST = set(etapas) - DIS - busca("new", "nuevo")

    try:
        t = _transiciones(odoo)
    except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
        avisos.append("Diseno: no se pudo leer el historial de etapas (vendidos y devueltos): " + str(e)[:200])
        t = pd.DataFrame(columns=["id", "fecha", "old", "new"])

    ultimo = ahora.month if anio == ahora.year else 12
    desde = int((cfg.get("desde") or {}).get(str(anio), 1))
    nuevo = lambda: {"pres": [0] * 12, "vend": [0] * 12, "dev": [0] * 12, "dest": [0] * 12, "py": [0] * 12, "pyn": [0] * 12}  # noqa: E731
    M, DET, DIS_D = {}, {}, {}

    def det(emp, mes):
        return DET.setdefault(emp, {}).setdefault(str(mes), {"pres": [], "vend": [], "dev": [], "nov": []})

    def por_dis(emp, nombre):
        return DIS_D.setdefault(emp, {}).setdefault(nombre, {"pres": [0] * 12, "vend": [0] * 12, "dev": [0] * 12})

    # --- presentados, destacados, proyectos con novedades ---
    pf = df[df["f_real"].dt.year == anio].copy()
    pf["mes"] = pf["f_real"].dt.month - 1
    for r in pf.sort_values("f_real").itertuples():
        m = M.setdefault(r.empresa, nuevo())
        m["pres"][r.mes] += 1
        if r.dest:
            m["dest"][r.mes] += 1
        por_dis(r.empresa, r.disenador)["pres"][r.mes] += 1
        det(r.empresa, r.mes)["pres"].append([str(r.name), r.cliente, r.disenador, r.etapa, 1 if r.dest else 0])
    for (emp, mes, num), x in pf[pf["num"] > 0].groupby(["empresa", "mes", "num"]):
        m = M.setdefault(emp, nuevo())
        m["py"][int(mes)] += 1
        if x["lidx"].max() > 0:
            m["pyn"][int(mes)] += 1
            det(emp, int(mes))["nov"].append([int(num), ", ".join(sorted(set(x["letra_"]))), x.iloc[0]["cliente"]])

    # --- vendidos (entran a Ganado) y devueltos (vuelven a Diseno desde una etapa posterior) ---
    reg = df.set_index("id")
    vistos = set()
    for r in t.itertuples():
        if r.fecha.year != anio or r.id not in reg.index:
            continue
        mes = r.fecha.month - 1
        emp, nom, dis = reg.at[r.id, "empresa"], str(reg.at[r.id, "name"]), reg.at[r.id, "disenador"]
        if r.new in GAN:
            if (r.id, mes) in vistos:
                continue
            vistos.add((r.id, mes))
            M.setdefault(emp, nuevo())["vend"][mes] += 1
            por_dis(emp, dis)["vend"][mes] += 1
            det(emp, mes)["vend"].append([nom, reg.at[r.id, "cliente"], dis, f"{r.fecha:%d/%m}"])
        elif r.new in DIS and r.old in POST and r.old > 0:
            M.setdefault(emp, nuevo())["dev"][mes] += 1
            por_dis(emp, dis)["dev"][mes] += 1
            det(emp, mes)["dev"].append([nom, etapas.get(r.old, "?"), etapas.get(r.new, "?"), f"{r.fecha:%d/%m}"])

    if not any(sum(v["pres"]) + sum(v["vend"]) + sum(v["dev"]) for v in M.values()):
        return {"ok": False}

    def recorta(arr):
        return [(None if (j + 1 > ultimo or j + 1 < desde) else v) for j, v in enumerate(arr)]

    for emp, m in M.items():
        for k in m:
            m[k] = recorta(m[k])
    for emp in DET:
        for mes in DET[emp]:
            for k in DET[emp][mes]:
                DET[emp][mes][k] = DET[emp][mes][k][:MAXFILAS]
    for emp in DIS_D:
        for nom in DIS_D[emp]:
            for k in DIS_D[emp][nom]:
                DIS_D[emp][nom][k] = recorta(DIS_D[emp][nom][k])

    manual = (cfg.get("manual", {}).get("ia", {}) or {}).get(str(anio), [None] * 12)
    return {
        "ok": True, "ultimo": ultimo, "desde": desde, "empresas": sorted(M), "m": M, "det": DET, "disen": DIS_D,
        "hoy": ahora.date().isoformat(), "meta": cfg["metas"], "tipo": cfg["tipo"],
        "amarillo_max": cfg.get("amarillo_max", 1.25), "amarillo_min": cfg.get("amarillo_min", 0.7),
        "manual_ia": (manual + [None] * 12)[:12],
        "hoja": {str(m): cfg.get("hoja", {}).get(f"{anio}-{m:02d}") for m in range(1, 13)},
    }


def valores(dis, nombres, k):
    """Misma logica que el dashboard (JS), para verificar desde la consola. k = mes 0..11."""
    def s(c):
        return sum((dis["m"][n][c][k] or 0) for n in nombres)
    r = lambda a, b: (a / b * 100) if b else None  # noqa: E731
    return {"efectividad": r(s("vend"), s("pres")), "eficiencia": r(s("pyn"), s("py")),
            "devueltos": r(s("dev"), s("pres")), "destacados": r(s("dest"), s("pres")), "ia": dis["manual_ia"][k]}


def main():
    iv.cargar_env()
    ap = argparse.ArgumentParser(description="Indicadores del area de Diseno (Odoo 19)")
    ap.add_argument("--anio", type=int, default=datetime.now().year)
    args = ap.parse_args()
    odoo = iv.Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])
    avisos = []
    dis = calcular(odoo, args.anio, cargar_config(), avisos)
    for a in avisos:
        print("AVISO:", a)
    if not dis.get("ok"):
        sys.exit("Sin datos de Diseno para ese ano.")
    for nombres, tit in [([n], n) for n in dis["empresas"]] + [(dis["empresas"], "TODAS")]:
        print(f"\n{tit}\nMes  " + " ".join(f"{i:>12}" for i in IND))
        for k in range(dis["desde"] - 1, dis["ultimo"]):
            v = valores(dis, nombres, k)
            print(f"{k + 1:>3}  " + " ".join(f"{(v[i] if v[i] is not None else float('nan')):12.1f}" for i in IND))


if __name__ == "__main__":
    main()
