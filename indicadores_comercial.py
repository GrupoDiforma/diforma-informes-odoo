#!/usr/bin/env python3
"""
Indicadores mensuales del area comercial - Grupo Diforma SAS (Odoo 19)

Calcula desde Odoo los 4 indicadores de la hoja "Indicadores Area (Comercial)":
  - Nuevas Oportunidades  (CRM, cantidad)           meta mensual en metas_comercial.json
  - Pipeline              (cotizaciones abiertas, USD, al cierre de cada mes)
  - Closed PO             (ordenes de venta confirmadas en el mes, USD)
  - Billing               (facturacion del mes, USD; viene de las facturas contabilizadas)
y los compara contra las metas (% de cumplimiento).

Uso directo (calibracion):  python indicadores_comercial.py --calibrar
Solo lee datos.
"""
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import informe_ventas as iv  # noqa: E402

CONFIG = Path(__file__).resolve().parent / "metas_comercial.json"
IND = ["opp", "pipe", "po", "bill"]
TZ_H = -5  # Colombia: las fechas de Odoo vienen en UTC
UMBRAL_COP = 500000   # ingreso esperado mayor a esto se asume digitado en COP en un campo de USD
DIA_MASIVO = 500      # dia con mas de N proyectos creados = carga/migracion (no cuenta como "nuevos")


def cargar_config():
    with open(CONFIG, encoding="utf-8-sig") as f:
        return json.load(f)


def meta_mes(cfg, ind, anio, mes):
    return cfg["metas"].get(f"{anio}-{mes:02d}", {}).get(ind, cfg["defecto"][ind])


def leer_todo(odoo, modelo, dominio, campos, lote=2000):
    """search_read con active_test desactivado (incluye archivados/perdidos), de a lotes."""
    filas, offset = [], 0
    while True:
        b = odoo.call(modelo, "search_read", domain=dominio, fields=campos, offset=offset,
                      limit=lote, context={"active_test": False})
        filas.extend(b)
        if len(b) < lote:
            return filas
        offset += lote


def _local(serie):
    return (pd.to_datetime(serie, errors="coerce") + pd.Timedelta(hours=TZ_H)).astype("datetime64[ns]")


def _ym(serie):
    return serie.dt.year * 100 + serie.dt.month


# --------------------------------------------------------------------------
def traer_oportunidades(odoo, desde, hasta, avisos):
    campos = iv.campos_existentes(odoo, "crm.lead",
                                  ["create_date", "date_conversion", "type", "active", "probability",
                                   "stage_id", "expected_revenue", "user_id", "company_id", "date_closed", "date_open",
                                   "company_currency", "lost_reason_id"])
    if "create_date" not in campos:
        campos.append("create_date")
    dom = [["create_date", ">=", f"{desde:%Y-%m-%d}"], ["create_date", "<", f"{hasta:%Y-%m-%d}"]]
    if "type" in campos:
        dom.append(["type", "=", "opportunity"])
    try:
        filas = leer_todo(odoo, "crm.lead", dom, campos)
    except RuntimeError as e:
        avisos.append("No se pudo leer CRM (crm.lead): " + str(e)[:200])
        return pd.DataFrame()
    if not filas:
        return pd.DataFrame()
    df = pd.DataFrame(filas)
    df["creada"] = _local(df["create_date"])
    df["conv"] = _local(df["date_conversion"]) if "date_conversion" in df else pd.NaT
    df["vigente"] = df["active"].astype(bool) if "active" in df else True
    if "probability" in df:
        df["vigente"] = df["vigente"] | (df["probability"] >= 100)
    return df


def contar_oportunidades(df, criterio):
    if df.empty:
        return {}
    if criterio == "creadas":
        s = _ym(df["creada"])
    elif criterio == "convertidas":
        x = df.dropna(subset=["conv"])
        s = _ym(x["conv"])
    else:
        s = _ym(df.loc[df["vigente"], "creada"])
    return {int(k): int(v) for k, v in s.value_counts().items()}


def traer_ordenes(odoo, desde, hasta, avisos):
    """Ordenes de venta con importe sin IVA en USD (COP se convierte con la TRM de Odoo)."""
    campos = iv.campos_existentes(odoo, "sale.order",
                                  ["name", "state", "date_order", "create_date", "write_date",
                                   "amount_untaxed", "currency_id", "company_id", "user_id"])
    ini = f"{date(desde.year - 1, 1, 1):%Y-%m-%d}"
    dom = [["create_date", ">=", ini], ["create_date", "<", f"{hasta:%Y-%m-%d}"]]
    try:
        filas = leer_todo(odoo, "sale.order", dom, campos)
    except RuntimeError as e:
        avisos.append("No se pudo leer ventas (sale.order): " + str(e)[:200])
        return pd.DataFrame()
    if not filas:
        return pd.DataFrame()
    df = pd.DataFrame(filas)
    df["creada"] = _local(df["create_date"])
    df["conf"] = _local(df["date_order"])
    df["mod"] = _local(df["write_date"])
    df["moneda"] = df["currency_id"].map(iv.m2o)
    df["vend"] = df["user_id"].map(iv.m2o).astype(str).str.strip() if "user_id" in df else "Sin asignar"
    df.loc[df["vend"].isin(["", "False", "(sin dato)", "None"]), "vend"] = "Sin asignar"
    cop_usd, _ = iv.tabla_trm(odoo, desde)
    fecha = df["conf"].fillna(df["creada"])
    trm = iv.trm_cercana(fecha, cop_usd)
    es_usd = df["moneda"] == "USD"
    bad = (~es_usd) & ~((trm >= iv.TRM_MIN) & (trm <= iv.TRM_MAX))
    if bad.any():
        avisos.append(f"{int(bad.sum())} ordenes de venta sin TRM valida; no se incluyen en Pipeline/Closed PO.")
    df["usd"] = np.where(es_usd, df["amount_untaxed"], np.where(bad, 0.0, df["amount_untaxed"] / np.where(bad, 1, trm)))
    otras = set(df.loc[~es_usd, "moneda"]) - {"COP"}
    if otras:
        avisos.append("Ordenes en monedas distintas a USD/COP (se tratan como COP): " + ", ".join(sorted(otras)))
    return df


def pipeline_al_cierre(ord_df, anio, mes, estados, ahora):
    """Valor USD de cotizaciones abiertas (en los estados dados) al cierre del mes."""
    if ord_df.empty:
        return 0.0
    cierre = min(pd.Timestamp(date(anio + (mes == 12), mes % 12 + 1, 1)), ahora)
    o = ord_df[ord_df["creada"] < cierre]
    abierta_hoy = o["state"].isin(estados)
    resuelta_despues = ((o["state"].isin(["sale", "done"])) & (o["conf"] >= cierre)) | \
                       ((o["state"] == "cancel") & (o["mod"] >= cierre))
    return float(o.loc[abierta_hoy | resuelta_despues, "usd"].sum())


def closed_po(ord_df, anio, mes):
    if ord_df.empty:
        return 0.0
    c = ord_df[ord_df["state"].isin(["sale", "done"])]
    return float(c.loc[(c["conf"].dt.year == anio) & (c["conf"].dt.month == mes), "usd"].sum())


# --------------------------------------------------------------------------
# Proyectos del modulo Diforma Brief (crm.lead personalizado)
# --------------------------------------------------------------------------
def _leer_ids(odoo, modelo, ids, campos, lote=1000):
    out = []
    for i in range(0, len(ids), lote):
        out.extend(odoo.call(modelo, "read", ids=ids[i:i + lote], fields=campos))
    return out


def traer_proyectos(odoo, avisos):
    """Proyectos (crm.lead) con ingreso esperado en USD corregido y fechas de entrada a Cotizacion."""
    campos = iv.campos_existentes(odoo, "crm.lead", ["name", "create_date", "stage_id", "expected_revenue",
                                                      "active", "date_last_stage_update", "id_vendedor", "user_id"])
    for c in ("name", "create_date"):
        if c not in campos:
            campos.append(c)
    df = pd.DataFrame(leer_todo(odoo, "crm.lead", [], campos))
    if df.empty:
        return None
    df["creada"] = _local(df["create_date"])
    # comercial del proyecto: campo propio id_vendedor (Diforma Brief); si no existe o esta vacio, el responsable (user_id)
    vend = pd.Series("Sin asignar", index=df.index)
    for col in ("user_id", "id_vendedor"):
        if col in df:
            nom = df[col].map(iv.m2o).astype(str).str.strip()
            vend = vend.where(nom.isin(["", "False", "(sin dato)", "None"]), nom)
    df["vend"] = vend
    df["etapa_id"] = df["stage_id"].map(iv.m2o_id) if "stage_id" in df else 0
    df["activo"] = df["active"].astype(bool) if "active" in df else True
    df["ult"] = _local(df["date_last_stage_update"]) if "date_last_stage_update" in df else df["creada"]
    bruto = pd.to_numeric(df["expected_revenue"], errors="coerce").fillna(0.0) if "expected_revenue" in df else pd.Series(0.0, index=df.index)
    cop_usd, _ = iv.tabla_trm(odoo, date(2025, 1, 1))
    trm = iv.trm_cercana(df["creada"], cop_usd)
    ok = (trm >= iv.TRM_MIN) & (trm <= iv.TRM_MAX)
    raro = (bruto > UMBRAL_COP) & ok
    df["usd"] = bruto.where(~raro, bruto / pd.Series(trm, index=df.index).where(ok, 1))
    if raro.any():
        avisos.append(f"{int(raro.sum())} proyecto(s) con ingreso esperado > {UMBRAL_COP:,} se asumieron digitados en COP y se convirtieron a USD "
                      "(ver brief_diforma.py para la lista).")
    df["num"] = pd.to_numeric(df["name"].astype(str).str.extract(r"^\s*(\d{3,6})")[0], errors="coerce").fillna(0).astype(int)

    etapas = {e["id"]: e["name"] for e in odoo.search_read("crm.stage", [], ["name"])}
    cot = {i for i, n in etapas.items() if any(t in n.lower() for t in ("quotation", "cotiz")) and "aprob" not in n.lower()}

    # entradas a Cotizacion (historial del chatter); sin historial: fecha de ultima actualizacion de etapa
    entradas = {}
    tv = leer_todo(odoo, "mail.tracking.value", [["field_id.name", "=", "stage_id"], ["mail_message_id.model", "=", "crm.lead"]],
                   ["mail_message_id", "new_value_integer"])
    if tv:
        t = pd.DataFrame(tv)
        t = t[t["new_value_integer"].isin(cot)]
        mids = sorted({iv.m2o_id(x) for x in t["mail_message_id"]})
        mm = {m["id"]: m for m in _leer_ids(odoo, "mail.message", mids, ["res_id", "date"])} if mids else {}
        for r in t.itertuples():
            m = mm.get(iv.m2o_id(r.mail_message_id))
            if m and m.get("res_id"):
                entradas.setdefault(int(m["res_id"]), []).append(_local(pd.Series([m["date"]])).iloc[0])
    else:
        avisos.append("Sin historial de etapas en el chatter: el Pipeline usa la fecha de ultima actualizacion de etapa.")
    for r in df.itertuples():
        if int(r.id) not in entradas and r.etapa_id in cot and pd.notna(r.ult):
            entradas[int(r.id)] = [r.ult]
    return {"df": df, "cot": cot, "entradas": entradas}


def indicadores_proyectos(pr, anio, vend=None):
    """({mes: nuevos proyectos}, {mes: pipeline USD}) para el año, segun las definiciones de Diforma.
    Con vend: solo los proyectos de ese comercial."""
    df, cot, entradas = pr["df"], pr["cot"], pr["entradas"]
    # Nuevas oportunidades: numeros de proyecto vistos por primera vez en el mes (variantes A, B, C... no cuentan).
    # Se ignoran los dias de carga masiva (migracion), calculados sobre todos los proyectos.
    dias = df["creada"].dt.normalize().value_counts()
    masivos = set(dias[dias > DIA_MASIVO].index)
    if vend is not None:
        df = df[df["vend"] == vend]
        entradas = {k: v for k, v in entradas.items() if k in set(df["id"].astype(int))}
    con_num = df[df["num"] > 0].groupby("num")["creada"].min()
    sin_num = df.loc[df["num"] == 0, "creada"]
    primeras = pd.concat([con_num, sin_num])
    primeras = primeras[~primeras.dt.normalize().isin(masivos)]
    opp = {int(m): int(n) for m, n in primeras[primeras.dt.year == anio].dt.month.value_counts().items()}
    # Pipeline: proyectos que entraron a Cotizacion en el mes y siguen en Cotizacion (sin aprobar), valor USD
    pipe = {}
    base = df.set_index("id")
    for rid, fechas in entradas.items():
        r = base.loc[rid]
        if r["etapa_id"] not in cot or not r["activo"]:
            continue
        for f in {(x.year, x.month) for x in fechas}:
            if f[0] == anio:
                pipe[f[1]] = pipe.get(f[1], 0.0) + float(r["usd"])
    return opp, pipe


def calcular(odoo, anio, cfg, billing_usd, avisos):
    """Devuelve dict listo para el dashboard (lista de 12 meses)."""
    ahora = pd.Timestamp(datetime.now())
    opp_df = traer_oportunidades(odoo, date(anio, 1, 1), date(anio + 1, 1, 1), avisos)
    ord_df = traer_ordenes(odoo, date(anio, 1, 1), date(anio + 1, 1, 1), avisos)
    conteo = contar_oportunidades(opp_df, cfg.get("oportunidades_criterio", "creadas_vigentes"))
    estados = cfg.get("pipeline_estados", ["sent"])
    opp_p = pipe_p = pr = None
    if cfg.get("fuente_oportunidades_pipeline", "proyectos") == "proyectos":
        try:
            pr = traer_proyectos(odoo, avisos)
            if pr:
                opp_p, pipe_p = indicadores_proyectos(pr, anio)
        except (RuntimeError, requests.RequestException, KeyError, ValueError) as e:
            avisos.append("No se pudieron leer los proyectos (Diforma Brief): " + str(e)[:200])
    meses = []
    for m in range(1, 13):
        futuro = date(anio, m, 1) > ahora.date()
        reg = {"m": m, "meta": {i: meta_mes(cfg, i, anio, m) for i in IND}, "hoja": cfg["hoja"].get(f"{anio}-{m:02d}")}
        if futuro:
            reg["val"] = None
        else:
            reg["val"] = {
                "opp": opp_p.get(m, 0) if opp_p is not None else conteo.get(anio * 100 + m, 0),
                "pipe": round(pipe_p.get(m, 0.0)) if pipe_p is not None else round(pipeline_al_cierre(ord_df, anio, m, estados, ahora)),
                "po": round(closed_po(ord_df, anio, m)),
                "bill": round(float(billing_usd.get(m, 0.0))),
            }
        meses.append(reg)
    # --- por comercial (opp, pipe y po; el billing sale de las facturas en el navegador) ---
    vend = {}
    try:
        nombres = set()
        if not ord_df.empty:
            nombres |= set(ord_df.loc[ord_df["state"].isin(["sale", "done"]) & (ord_df["conf"].dt.year == anio), "vend"])
        pr_df = pr["df"] if (opp_p is not None and pr) else None
        if pr_df is not None:
            nombres |= set(pr_df.loc[pr_df["creada"].dt.year == anio, "vend"])
        for nom in sorted(nombres):
            vo = ord_df[ord_df["vend"] == nom] if not ord_df.empty else ord_df
            if pr_df is not None:
                o_v, p_v = indicadores_proyectos(pr, anio, nom)
            else:
                o_v, p_v = {}, {}
            vend[nom] = {"opp": [o_v.get(m, 0) for m in range(1, 13)],
                         "pipe": [round(p_v.get(m, 0.0)) for m in range(1, 13)],
                         "po": [round(closed_po(vo, anio, m)) for m in range(1, 13)]}
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        avisos.append("No se pudo calcular el detalle por comercial: " + str(e)[:200])
        vend = {}
    return {
        "meses": meses, "umbral": cfg["umbral_pct"], "amarillo": cfg.get("amarillo_desde", 0.7),
        "ok": opp_p is not None or not (opp_df.empty and ord_df.empty),
        "vend": vend,
    }


# --------------------------------------------------------------------------
def calibrar_crm(odoo, opp, desde):
    """Detalle del CRM para descubrir como la hoja define 'calificada' y el Pipeline."""
    print("\n" + "=" * 60)
    print("DETALLE CRM (oportunidades)")
    print("=" * 60)
    if opp.empty:
        print("Sin datos de CRM.")
        return
    cop_usd, _ = iv.tabla_trm(odoo, desde)
    trm = iv.trm_cercana(opp["creada"], cop_usd)
    ok = (trm >= iv.TRM_MIN) & (trm <= iv.TRM_MAX)
    mon = opp["company_currency"].map(iv.m2o) if "company_currency" in opp else pd.Series("COP", index=opp.index)
    ing = opp["expected_revenue"].fillna(0) if "expected_revenue" in opp else pd.Series(0.0, index=opp.index)
    opp = opp.copy()
    opp["usd"] = np.where(mon == "USD", ing, np.where(ok, ing / np.where(ok, trm, 1), 0.0))
    opp["etapa"] = opp["stage_id"].map(iv.m2o) if "stage_id" in opp else "(sin etapa)"
    opp["mes"] = _ym(opp["creada"])
    opp["estado"] = np.where(~opp["vigente"], "perdida", np.where(opp.get("probability", 0) >= 100, "ganada", "abierta"))
    print("Moneda de la compania en CRM:", ", ".join(sorted(set(mon.astype(str)))))
    print("\nDias con mas oportunidades creadas (posible carga masiva/migracion):")
    dias = opp["creada"].dt.strftime("%Y-%m-%d").value_counts().head(5)
    print(dias.to_string())
    print("\nOportunidades creadas por MES y ETAPA actual (cantidad):")
    print(pd.crosstab(opp["etapa"], opp["mes"]).to_string())
    print("\nOportunidades creadas por MES y ESTADO (abierta/ganada/perdida):")
    print(pd.crosstab(opp["estado"], opp["mes"]).to_string())
    print("\nPIPELINE HOY: oportunidades abiertas por etapa (cantidad / ingreso esperado USD):")
    ab = opp[opp["estado"] == "abierta"]
    print(ab.groupby("etapa")["usd"].agg(["count", "sum"]).round(0).to_string())
    print(f"TOTAL abiertas: {len(ab)} / US$ {ab['usd'].sum():,.0f}")
    print("\nIngreso esperado USD de las oportunidades CREADAS en cada mes, por estado:")
    print(opp.pivot_table(index="estado", columns="mes", values="usd", aggfunc="sum", fill_value=0).round(0).to_string())
    if "date_closed" in opp:
        ga = opp[opp["estado"] == "ganada"].copy()
        ga["cierre"] = _local(ga["date_closed"])
        print("\nOportunidades GANADAS por mes de cierre (cantidad / USD):")
        print(ga.groupby(_ym(ga["cierre"]))["usd"].agg(["count", "sum"]).round(0).to_string())


def calibrar(odoo):
    """Imprime, por mes, varias definiciones candidatas junto a lo que reporta la hoja."""
    cfg = cargar_config()
    avisos = []
    hoy = date.today()
    desde = date(2025, 9, 1)
    hasta = date(hoy.year + (hoy.month == 12), hoy.month % 12 + 1, 1)
    print("CALIBRACION DE INDICADORES COMERCIALES (Odoo vs hoja)")
    print(f"Generado {datetime.now():%d/%m/%Y %H:%M}\n")
    opp = traer_oportunidades(odoo, desde, hasta, avisos)
    ordd = traer_ordenes(odoo, desde, hasta, avisos)
    fact = iv.traer_facturacion(odoo, desde, hasta, avisos)
    bill = {}
    if not fact.empty:
        bill = fact.groupby(fact["fecha"].dt.year * 100 + fact["fecha"].dt.month)["usd"].sum().to_dict()
    for a in avisos:
        print("AVISO:", a)
    cn = {c: contar_oportunidades(opp, c) for c in ("creadas", "creadas_vigentes", "convertidas")}
    print("Leyenda: [H]=% que reporta la hoja; [Odoo]=% calculado con la meta de metas_comercial.json\n")
    ahora = pd.Timestamp(datetime.now())
    y, m = desde.year, desde.month
    while date(y, m, 1) < hasta:
        k = y * 100 + m
        h = cfg["hoja"].get(f"{y}-{m:02d}") or {}
        mt = {i: meta_mes(cfg, i, y, m) for i in IND}
        print(f"=== {y}-{m:02d} ===")

        def pct(v, i):
            return f"{v / mt[i] * 100:.0f}%" if mt[i] else "-"
        print(f"Nuevas oportunidades (meta {mt['opp']}) [H {h.get('opp', '-')}%]: creadas {cn['creadas'].get(k, 0)} ({pct(cn['creadas'].get(k, 0), 'opp')}), "
              f"creadas sin perdidas {cn['creadas_vigentes'].get(k, 0)} ({pct(cn['creadas_vigentes'].get(k, 0), 'opp')}), "
              f"convertidas {cn['convertidas'].get(k, 0)} ({pct(cn['convertidas'].get(k, 0), 'opp')})")
        for et in (["sent"], ["draft", "sent"]):
            v = pipeline_al_cierre(ordd, y, m, et, ahora)
            print(f"Pipeline (meta {mt['pipe']:,}) [H {h.get('pipe', '-')}%]: estados {'+'.join(et)} = US$ {v:,.0f} ({pct(v, 'pipe')})")
        v = closed_po(ordd, y, m)
        print(f"Closed PO (meta {mt['po']:,}) [H {h.get('po', '-')}%]: ordenes confirmadas US$ {v:,.0f} ({pct(v, 'po')})")
        v = bill.get(k, 0.0)
        print(f"Billing (meta {mt['bill']:,}) [H {h.get('bill', '-')}%]: facturado US$ {v:,.0f} ({pct(v, 'bill')})")
        print()
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    if not ordd.empty:
        print("Ordenes por estado (cantidad / USD):")
        print(ordd.groupby("state")["usd"].agg(["count", "sum"]).round(0).to_string())
    calibrar_crm(odoo, opp, desde)


def main():
    iv.cargar_env()
    faltan = [v for v in ("ODOO_URL", "ODOO_DB", "ODOO_KEY") if not os.environ.get(v)]
    if faltan:
        sys.exit("Faltan datos de conexion: " + ", ".join(faltan))
    odoo = iv.Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])
    try:
        calibrar(odoo)
    except (RuntimeError, requests.RequestException) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
