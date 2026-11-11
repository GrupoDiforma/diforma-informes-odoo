#!/usr/bin/env python3
"""
Modulo personalizado "Diforma Brief" (proyectos): descubrimiento y calibracion de indicadores.

Definiciones (segun Diforma):
  - Nuevas Oportunidades = proyectos (briefs) creados en el mes (inician en cualquier etapa menos Ganado/Aprobado).
  - Pipeline = proyectos en etapa Cotizacion, aun no aprobados; valor = expected_revenue (USD).
  - Closed PO = ordenes de venta confirmadas (sale.order) / proyectos que pasan a Ganado.
  - Billing = facturacion del mes (asientos contables).

Uso:  python brief_diforma.py        (imprime el diagnostico; es de solo lectura)
"""
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import informe_ventas as iv  # noqa: E402
import indicadores_comercial as ic  # noqa: E402

UMBRAL_COP = 500000  # ingreso esperado mayor a esto se asume digitado en COP
ACCION = 949  # accion del menu "Diforma Brief" (de la URL .../odoo/action-949)


def seguro(f, *a, **k):
    try:
        return f(*a, **k)
    except Exception as e:  # noqa: BLE001 - diagnostico: mostrar y seguir
        print("   (error:", str(e)[:300], ")")
        return None


def leer_ids(odoo, modelo, ids, campos, lote=1000):
    out = []
    for i in range(0, len(ids), lote):
        out.extend(odoo.call(modelo, "read", ids=ids[i:i + lote], fields=campos))
    return out


def descubrir_modelo(odoo):
    print("1) Modelo detras del menu Diforma Brief (accion %d)" % ACCION)
    rm = None
    a = seguro(odoo.call, "ir.actions.act_window", "read", ids=[ACCION], fields=["name", "res_model", "domain", "context"])
    if a:
        print("   ", a[0])
        rm = a[0].get("res_model")
    print("\n2) Modelos con 'brief' en el nombre tecnico:")
    ms = seguro(odoo.search_read, "ir.model", [["model", "ilike", "brief"]], ["model", "name"]) or []
    for m in ms:
        print("   ", m["model"], "-", m["name"])
    if not rm:
        rm = "crm.brief" if any(m["model"] == "crm.brief" for m in ms) else (ms[0]["model"] if ms else None)
    print("\n   => modelo de proyectos usado:", rm)
    return rm, [m["model"] for m in ms]


def mostrar_campos(odoo, modelo):
    info = odoo.call(modelo, "fields_get", attributes=["string", "type", "relation", "selection"])
    print(f"\n3) Campos de {modelo}:")
    for n, d in sorted(info.items()):
        if n.startswith(("message_", "activity_", "website_", "__", "access_", "has_message", "my_activity")):
            continue
        extra = d.get("relation") or (str(d.get("selection"))[:100] if d.get("selection") else "")
        print(f"   {n:32s} {d.get('type',''):10s} {d.get('string','')}  {extra}")
    return info


def elegir(info, preferidos, textos):
    for p in preferidos:
        if p in info:
            return p
    for n, d in info.items():
        s = (d.get("string") or "").lower()
        if any(t in s for t in textos):
            return n
    return None


def cargar_proyectos(odoo, modelo, info):
    f_stage = elegir(info, ["stage_id"], ["etapa", "stage"])
    f_rev = elegir(info, ["expected_revenue"], ["ingreso esperado"])
    f_act = "active" if "active" in info else None
    f_lost = elegir(info, ["lost_reason_id"], ["motivo de p"])
    f_user = elegir(info, ["user_id", "comercial_id"], ["comercial"])
    extra = ["proyecto", "letra", "brief_padre", "date_last_stage_update", "date_closed", "type", "won_status", "code"]
    campos = [c for c in ["name", "create_date", "write_date", f_stage, f_rev, f_act, f_lost, f_user, "partner_id"] + extra
              if c and (c in info or c in ("name", "create_date", "write_date"))]
    print(f"\n4) Campos detectados: etapa={f_stage}, ingreso={f_rev}, activo={f_act}, perdido={f_lost}, comercial={f_user}")
    filas = ic.leer_todo(odoo, modelo, [], campos)
    df = pd.DataFrame(filas)
    print(f"   Proyectos leidos (incluye archivados): {len(df)}")
    df["creada"] = ic._local(df["create_date"])
    df["etapa_id"] = df[f_stage].map(iv.m2o_id) if f_stage else 0
    df["etapa"] = df[f_stage].map(iv.m2o) if f_stage else "(sin etapa)"
    df["usd"] = pd.to_numeric(df[f_rev], errors="coerce").fillna(0.0) if f_rev else 0.0
    # El ingreso esperado se digita a mano: a veces se escribe en COP en un campo de USD.
    # Regla: valores > UMBRAL_COP se consideran COP y se convierten a USD con la TRM de la fecha de creacion.
    df["usd_bruto"] = df["usd"]
    try:
        cop_usd, _ = iv.tabla_trm(odoo, date(2025, 1, 1))
        trm = iv.trm_cercana(df["creada"], cop_usd)
        ok = (trm >= iv.TRM_MIN) & (trm <= iv.TRM_MAX)
        raro = (df["usd_bruto"] > UMBRAL_COP) & ok
        df["usd"] = df["usd_bruto"].where(~raro, df["usd_bruto"] / pd.Series(trm, index=df.index).where(ok, 1))
        df["corregido"] = raro
    except Exception as e:  # noqa: BLE001
        print("   (no se pudo corregir COP->USD:", str(e)[:200], ")")
        df["corregido"] = False
    df["activo"] = df[f_act].astype(bool) if f_act else True
    df["perdido"] = df[f_lost].notna() & (df[f_lost] != False) if f_lost else False  # noqa: E712
    df["proy"] = pd.to_numeric(df["proyecto"], errors="coerce").fillna(0).astype(int) if "proyecto" in df else 0
    df["num"] = pd.to_numeric(df["name"].astype(str).str.extract(r"^\s*(\d{3,6})")[0], errors="coerce").fillna(0).astype(int)
    df["padre"] = df["brief_padre"].map(lambda v: bool(v)) if "brief_padre" in df else False
    df["ult_etapa"] = ic._local(df["date_last_stage_update"]) if "date_last_stage_update" in df else df["creada"]
    df["cerrada"] = ic._local(df["date_closed"]) if "date_closed" in df else pd.NaT
    return df, f_stage


def historial_etapas(odoo, modelo, ids):
    """Cambios de etapa desde el chatter (mail.tracking.value): {id: [(fecha, etapa_nueva_id), ...]}, {id: etapa_inicial}."""
    tv = seguro(ic.leer_todo, odoo, "mail.tracking.value",
                [["field_id.name", "=", "stage_id"], ["mail_message_id.model", "=", modelo]],
                ["mail_message_id", "old_value_integer", "new_value_integer", "create_date"])
    if not tv:
        print("   Sin historial de cambios de etapa (tracking). Pipeline historico no se puede reconstruir.")
        return None, None
    t = pd.DataFrame(tv)
    mids = sorted({iv.m2o_id(x) for x in t["mail_message_id"]})
    msgs = leer_ids(odoo, "mail.message", mids, ["res_id", "date"])
    mm = {m["id"]: m for m in msgs}
    t["res_id"] = t["mail_message_id"].map(lambda x: mm.get(iv.m2o_id(x), {}).get("res_id"))
    t["fecha"] = ic._local(t["mail_message_id"].map(lambda x: mm.get(iv.m2o_id(x), {}).get("date")))
    t = t.dropna(subset=["res_id", "fecha"]).sort_values("fecha")
    cambios, inicial = {}, {}
    for r in t.itertuples():
        rid = int(r.res_id)
        cambios.setdefault(rid, []).append((r.fecha, int(r.new_value_integer or 0)))
        inicial.setdefault(rid, int(r.old_value_integer or 0))
    print(f"   Cambios de etapa leidos: {len(t)} en {len(cambios)} proyectos")
    return cambios, inicial


def etapa_en(rid, fecha, actual, creada, cambios, inicial):
    if rid not in cambios:
        return actual if creada < fecha else None
    if creada >= fecha:
        return None
    est = inicial.get(rid, actual)
    for f, e in cambios[rid]:
        if f < fecha:
            est = e
        else:
            break
    return est


def main():
    iv.cargar_env()
    faltan = [v for v in ("ODOO_URL", "ODOO_DB", "ODOO_KEY") if not os.environ.get(v)]
    if faltan:
        sys.exit("Faltan datos de conexion: " + ", ".join(faltan))
    odoo = iv.Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])
    print("DIAGNOSTICO DEL MODULO DIFORMA BRIEF  -", datetime.now().strftime("%d/%m/%Y %H:%M"), "\n")
    try:
        rm, _ = descubrir_modelo(odoo)
        if not rm:
            sys.exit("No se encontro el modelo de proyectos.")
        info = mostrar_campos(odoo, rm)
        df, f_stage = cargar_proyectos(odoo, rm, info)
        rel = info[f_stage].get("relation") if f_stage else None
        etapas = {}
        if rel:
            print(f"\n5) Etapas ({rel}):")
            for e in seguro(odoo.search_read, rel, [], ["name", "sequence"], order="sequence asc") or []:
                etapas[e["id"]] = e["name"]
                print("   ", e["id"], e["name"])
        print("\n6) Proyectos hoy por etapa (cantidad / activos / ingreso esperado USD):")
        g = df.groupby("etapa").agg(n=("usd", "size"), activos=("activo", "sum"), usd=("usd", "sum")).round(0)
        print(g.to_string())
        print("   Archivados/inactivos:", int((~df["activo"]).sum()), "| con motivo de perdida:", int(df["perdido"].sum()))
        print("\n7) Proyectos creados por mes (todos / activos / sin perdidos):")
        df["ym"] = ic._ym(df["creada"])
        m = df[df["ym"] >= 202509].groupby("ym").agg(todos=("usd", "size"), activos=("activo", "sum"), usd=("usd", "sum"))
        m["sin_perdidos"] = df[df["ym"] >= 202509].groupby("ym").apply(lambda x: int((x["activo"] & ~x["perdido"]).sum()))
        print(m.round(0).to_string())
        print("   Dias con mas proyectos creados:", df["creada"].dt.strftime("%Y-%m-%d").value_counts().head(3).to_dict())

        print("\n8) Historial de etapas (chatter):")
        cambios, inicial = historial_etapas(odoo, rm, [int(i) for i in df["id"]])
        cot = {i for i, n in etapas.items() if any(t in n.lower() for t in ("cotiz", "quotation")) and "aprob" not in n.lower()}
        gan = {i for i, n in etapas.items() if n.lower().startswith(("ganad", "aprob", "won"))}
        print("   Etapas tomadas como 'Cotizacion':", [etapas[i] for i in cot], "| 'Ganado/Aprobado':", [etapas[i] for i in gan])
        cfg = ic.cargar_config()
        ahora = pd.Timestamp(datetime.now())
        hoja = lambda k, y, m: (cfg["hoja"].get(f"{y}-{m:02d}") or {}).get(k, "-")  # noqa: E731

        print("\n9) VALORES ATIPICOS de ingreso esperado (los 12 mas altos) y proyectos > US$ 500.000:")
        top = df[df["corregido"]].sort_values("usd_bruto", ascending=False).head(15)
        for r in top.itertuples():
            print(f"   digitado {r.usd_bruto:14,.0f} -> corregido US$ {r.usd:10,.0f}  {r.etapa:12s} {str(r.name)[:45]}  creado {r.creada:%Y-%m-%d}")
        print("   Proyectos corregidos (COP digitado como USD):", int(df['corregido'].sum()))

        print("\n10) NUEVAS OPORTUNIDADES: candidatos (numero de proyecto = digitos iniciales del nombre, ej. 17573-A -> 17573)")
        print("    implicada = cantidad que sale de la hoja (meta x %). Columnas:")
        print("    T=todos creados | A=activos | DT=num. de proyecto distintos (todos) | DA=distintos (activos) | NT=proyectos nuevos 1a vez (todos) | NA=nuevos 1a vez (activos)")
        prim = df[df["num"] > 0].groupby("num")["creada"].min()
        prim_ym = ic._ym(prim)
        act_nums = set(df.loc[df["activo"], "num"])
        print("    mes     hoja%  implic |   T    A   DT   DA   NT   NA")
        y, mes = 2026, 2
        while date(y, mes, 1) <= ahora.date():
            k = y * 100 + mes
            t = df[df["ym"] == k]
            a = t[t["activo"]]
            h = hoja("opp", y, mes)
            imp = round(ic.meta_mes(cfg, "opp", y, mes) * h / 100) if isinstance(h, (int, float)) else "-"
            dt = t.loc[t["num"] > 0, "num"].nunique()
            da_ = a.loc[a["num"] > 0, "num"].nunique()
            nt = int((prim_ym == k).sum())
            na = len(set(prim.index[prim_ym == k]) & act_nums)
            print(f"    {k}   {str(h):>5s}  {str(imp):>5s}  | {len(t):3d}  {len(a):3d}  {dt:3d}  {da_:3d}  {nt:3d}  {na:3d}")
            y, mes = (y + 1, 1) if mes == 12 else (y, mes + 1)

        print("\n11) PIPELINE por mes de ENTRADA a Cotizacion (ingreso corregido COP->USD) vs hoja")
        print("    P1 = entraron en el mes y seguian en Cotizacion al cierre del mes | P2 = entraron en el mes y siguen en Cotizacion HOY | P3 = entraron en el mes (cualquier estado final)")
        entradas = {}  # id -> lista de fechas de entrada a Cotizacion
        for rid, lst in (cambios or {}).items():
            for f, e in lst:
                if e in cot:
                    entradas.setdefault(rid, []).append(f)
        for r in df.itertuples():
            if int(r.id) not in (cambios or {}) and r.etapa_id in cot and pd.notna(r.ult_etapa):
                entradas.setdefault(int(r.id), []).append(r.ult_etapa)
        reg = df.set_index("id")
        y, mes = 2026, 1
        while date(y, mes, 1) <= ahora.date():
            ini_m = pd.Timestamp(date(y, mes, 1)); fin_m = pd.Timestamp(date(y + (mes == 12), mes % 12 + 1, 1))
            fin_m_c = min(fin_m, ahora)
            p1 = p2 = p3 = 0.0
            n1 = n2 = n3 = 0
            for rid, fechas in entradas.items():
                if not any(ini_m <= f < fin_m for f in fechas):
                    continue
                r = reg.loc[rid]
                v = r["usd"]
                p3 += v; n3 += 1
                if int(r["etapa_id"]) in cot:
                    p2 += v; n2 += 1
                e = etapa_en(int(rid), fin_m_c, int(r["etapa_id"]), r["creada"], cambios or {}, inicial or {})
                if e in cot:
                    p1 += v; n1 += 1
            meta = ic.meta_mes(cfg, "pipe", y, mes)
            print(f"    {y}-{mes:02d}: P1 {n1:3d} US$ {p1:9,.0f} ({p1 / meta * 100:4.0f}%) | P2 {n2:3d} US$ {p2:9,.0f} ({p2 / meta * 100:4.0f}%) | "
                  f"P3 {n3:3d} US$ {p3:9,.0f} ({p3 / meta * 100:4.0f}%) | hoja {hoja('pipe', y, mes)}%")
            y, mes = (y + 1, 1) if mes == 12 else (y, mes + 1)

        print("\n12) CLOSED PO alternativo: proyectos Ganados por mes de cierre (date_closed) y cambio de etapa vs hoja")
        g = df[df["etapa_id"].isin(gan)].copy()
        g["cm"] = ic._ym(g["cerrada"])
        r1 = g.groupby("cm").agg(n=("usd", "size"), usd=("usd", "sum"))
        for k, v in r1.iterrows():
            if k >= 202601 and not pd.isna(k):
                y, mes = divmod(int(k), 100)
                meta = ic.meta_mes(cfg, "po", y, mes)
                print(f"    {int(k)}: {int(v['n']):4d} ganados  US$ {v['usd']:12,.0f} = {v['usd'] / meta * 100:5.0f}%   [hoja {hoja('po', y, mes)}%]")
        print("\nFin del diagnostico.")
    except (RuntimeError, requests.RequestException) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
