#!/usr/bin/env python3
"""
Dashboard de Indicadores (areas comercial, administrativa, de diseno y de produccion) - Grupo Diforma SAS (Odoo 19)

Uso:
    python dashboard_anual.py               # año actual
    python dashboard_anual.py --anio 2025   # otro año

Incluye los indicadores del area comercial (ver indicadores_comercial.py y metas_comercial.json)
los del area administrativa (indicadores_admin.py y metas_admin.json) y los del area de diseno
(indicadores_diseno.py y metas_diseno.json).
Genera un unico archivo HTML (informes/dashboard_indicadores_AAAA.html) que se abre en el
navegador, sin internet y sin instalar nada. Permite elegir mes a mes, ver el acumulado
del año, filtrar por empresa y cambiar entre USD y COP.

Fuente: asientos contables de facturas de cliente publicadas (notas credito restan).
El valor contable en COP es el oficial; el USD sale de la factura (o de la TRM de Odoo
si la factura fue emitida en COP). Ver informe_ventas.py. Solo lee datos.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import informe_ventas as iv  # noqa: E402
import indicadores_comercial as ic  # noqa: E402
import indicadores_admin as ia  # noqa: E402
import indicadores_diseno as idg  # noqa: E402
import indicadores_produccion as ipr  # noqa: E402


def codificar(serie, nombres=None):
    if nombres is None:
        codigos, nombres = pd.factorize(serie.astype(str))
        return [int(c) for c in codigos], [str(n) for n in nombres]
    idx = {n: i for i, n in enumerate(nombres)}
    return [idx[str(s)] for s in serie], nombres


def construir_datos(odoo, anio):
    avisos = []
    hist = iv.traer_facturacion(odoo, date(anio - 1, 1, 1), date(anio + 1, 1, 1), avisos)
    if hist.empty:
        return None, avisos
    hist["anio"] = hist["fecha"].dt.year
    hist["mes"] = hist["fecha"].dt.month
    act = hist[hist["anio"] == anio].copy()
    prev = hist[hist["anio"] == anio - 1].copy()
    if act.empty:
        return None, avisos

    _, emp_n = codificar(hist["empresa"])
    emp, _ = codificar(act["empresa"], emp_n)
    cli, cli_n = codificar(act["cliente"])
    _, ven_n = codificar(hist["vendedor"])
    ven, _ = codificar(act["vendedor"], ven_n)
    equ, equ_n = codificar(act["equipo"])
    pro, pro_n = codificar(act["producto"])
    cat, cat_n = codificar(act["categoria"])
    fac, fac_n = codificar(act["factura"])
    tipo_por_fac = act.drop_duplicates("factura").set_index("factura")["tipo"]
    ftipo = [0 if tipo_por_fac[n] == "out_invoice" else 1 for n in fac_n]

    filas = [[int(m), e, c, v, q, p, k, f, round(float(u), 2), round(float(w))]
             for m, e, c, v, q, p, k, f, u, w in
             zip(act["mes"], emp, cli, ven, equ, pro, cat, fac, act["usd"], act["cop"])]

    pe, _ = codificar(prev["empresa"], emp_n) if len(prev) else ([], emp_n)
    pv, _ = codificar(prev["vendedor"], ven_n) if len(prev) else ([], ven_n)
    pr = prev.assign(emp=pe, ven=pv).groupby(["mes", "emp", "ven"])[["usd", "cop"]].sum().reset_index()
    prev_filas = [[int(r.mes), int(r.emp), round(float(r.usd), 2), round(float(r.cop)), int(r.ven)] for r in pr.itertuples()]

    datos = dict(
        anio=anio, generado=datetime.now().strftime("%d/%m/%Y %H:%M"),
        ultimo_mes=int(act["mes"].max()), mes_inicial=int(act["mes"].max()),
        meses_cortos=[m[:3].capitalize() for m in iv.MESES], meses_largos=[m.capitalize() for m in iv.MESES],
        empresas=emp_n, clientes=cli_n, vendedores=ven_n, equipos=equ_n, productos=pro_n, categorias=cat_n,
        ftipo=ftipo, r=filas, pr=prev_filas, avisos=avisos, tiene_previo=bool(len(prev)),
    )
    try:
        billing = act.groupby("mes")["usd"].sum().to_dict()
        datos["ind"] = ic.calcular(odoo, anio, ic.cargar_config(), billing, avisos)
    except (RuntimeError, requests.RequestException, OSError, ValueError, KeyError) as e:
        avisos.append("No se pudieron calcular los indicadores del area comercial: " + str(e)[:200])
        datos["ind"] = None
    try:
        datos["adm"] = ia.calcular(odoo, anio, ia.cargar_config(), avisos)
    except (RuntimeError, requests.RequestException, OSError, ValueError, KeyError) as e:
        avisos.append("No se pudieron calcular los indicadores del area administrativa: " + str(e)[:200])
        datos["adm"] = None
    try:
        datos["dis"] = idg.calcular(odoo, anio, idg.cargar_config(), avisos)
    except (RuntimeError, requests.RequestException, OSError, ValueError, KeyError) as e:
        avisos.append("No se pudieron calcular los indicadores del area de diseno: " + str(e)[:200])
        datos["dis"] = None
    try:
        datos["pro"] = ipr.calcular(odoo, anio, ipr.cargar_config(), avisos)
    except (RuntimeError, requests.RequestException, OSError, ValueError, KeyError) as e:
        avisos.append("No se pudieron calcular los indicadores del area de produccion: " + str(e)[:200])
        datos["pro"] = None
    return datos, avisos


def construir_multi(odoo, anio):
    """Datos del año pedido y, si hay facturas del año anterior, tambien de ese año (selector de año)."""
    datos, avisos = construir_datos(odoo, anio)
    if datos is None:
        return None, avisos
    por = {anio: datos}
    if datos.get("tiene_previo"):
        previo, _ = construir_datos(odoo, anio - 1)
        if previo:
            por[anio - 1] = previo
    return {"anio_defecto": anio, "anios": sorted(por), "por_anio": {str(k): v for k, v in por.items()}}, avisos


PLANTILLA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard de Indicadores - Grupo Diforma</title>
<link rel="icon" type="image/png" href="data:image/png;base64,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">
<style>
*{box-sizing:border-box}
body{font-family:Segoe UI,Arial,sans-serif;margin:0;color:#222;background:#fff}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px 40px}
h1{margin:22px 0 0}.sub{color:#666;margin:4px 0 12px}
h2{margin:30px 0 8px;font-size:18px;border-bottom:2px solid #e5e5e5;padding-bottom:4px}
.ctrl{position:sticky;top:0;background:#fffffff2;z-index:5;padding:10px 0;border-bottom:1px solid #e5e5e5;
  display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.chip{border:1px solid #c9d3da;background:#f4f7f9;border-radius:16px;padding:6px 12px;cursor:pointer;font-size:14px;color:#234}
.chip:hover{background:#e6eef3}.chip.on{background:#1f6f8b;border-color:#1f6f8b;color:#fff}
.chip.todo{font-weight:600}
.sep{flex:1}
select{border:1px solid #c9d3da;border-radius:16px;padding:6px 10px;font-size:14px;background:#fff;color:#234}
.seg{display:inline-flex;border:1px solid #1f6f8b;border-radius:16px;overflow:hidden}
.seg button{border:0;background:#fff;padding:6px 14px;cursor:pointer;font-size:14px;color:#1f6f8b}
.seg button.on{background:#1f6f8b;color:#fff}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:14px 0}
.kpi{background:#f4f7f9;border-radius:8px;padding:12px 14px}
.kpi.ytd{background:#fdf3e7}
.kpi .l{font-size:12px;color:#666}.kpi .v{font-size:22px;font-weight:700}
.cmp{font-size:12px;margin-top:3px}
.up{color:#1a7f37}.down{color:#c62828}.muted{color:#888}.small{font-size:12px}
.aviso{background:#fff4e5;border-left:4px solid #e08e45;padding:8px 12px;margin:12px 0;font-size:13px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:22px}
.rk{display:grid;grid-template-columns:minmax(90px,1.1fr) 1.4fr auto 44px;gap:8px;align-items:center;font-size:13px;padding:3px 0}
.rk-n{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rk-b{background:#eef2f5;border-radius:3px;height:12px}.rk-b i{display:block;height:100%;background:#1f6f8b;border-radius:3px}
.rk-v{text-align:right;white-space:nowrap}.rk-p{text-align:right;color:#777}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid #e5e5e5;text-align:right}
th:first-child,td:first-child{text-align:left}th{background:#f4f7f9}
tbody tr{cursor:pointer}tbody tr:hover{background:#f4f7f9}tbody tr.sel{background:#fdf3e7}
tfoot td{font-weight:700;border-top:2px solid #ccc}
.nota{font-size:12px;color:#777;margin-top:26px}
svg text{font-family:Segoe UI,Arial,sans-serif}
.hit{cursor:pointer}.hit:hover{fill:#1f6f8b14}
.ind{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:12px 0}
.ic{border-radius:8px;padding:12px 14px;background:#f4f7f9;border-top:5px solid #bbb}
.ic.g{border-top-color:#2e9e5b;background:#eef8f1}.ic.y{border-top-color:#e6b422;background:#fff9e5}.ic.r{border-top-color:#d64545;background:#fdeeee}
.ic .l{font-size:13px;font-weight:600}.ic .p{font-size:30px;font-weight:700;margin:2px 0}
.ic.g .p{color:#1a7f37}.ic.y .p{color:#a67c00}.ic.r .p{color:#c62828}
.pb{background:#00000012;border-radius:3px;height:6px;margin:4px 0}.pb i{display:block;height:100%;border-radius:3px;background:#888}
.ic.g .pb i{background:#2e9e5b}.ic.y .pb i{background:#e6b422}.ic.r .pb i{background:#d64545}
.pill{display:inline-block;min-width:46px;text-align:center;border-radius:10px;padding:2px 8px;font-weight:600}
.pill.g{background:#d9f0e1;color:#1a7f37}.pill.y{background:#fff0b8;color:#8a6a00}.pill.r{background:#fadada;color:#b42323}.pill.n{background:#eee;color:#888}
.ed input{width:92px;border:1px solid #c9d3da;border-radius:6px;padding:4px 6px;font-size:13px;text-align:right}
.ed input.mod{background:#fff4d6;border-color:#e0b43c}.ed{background:#f4f7f9;border-radius:8px;padding:10px 12px;margin:8px 0;overflow-x:auto}
.ed td,.ed th{text-align:center}.ed td:first-child,.ed th:first-child{text-align:left}
.ind.adm{grid-template-columns:repeat(4,1fr)}.ind.dis{grid-template-columns:repeat(5,1fr)}.ic .l small{font-weight:400;color:#666}
.ant{font-size:13px;margin:8px 0}.ant td,.ant th{padding:4px 8px}
@media(max-width:900px){.ind.dis{grid-template-columns:repeat(3,1fr)}}
@media(max-width:760px){.ind,.ind.adm,.ind.dis{grid-template-columns:1fr 1fr}}
@media(max-width:760px){.kpis{grid-template-columns:1fr}.grid2{grid-template-columns:1fr}}
.tabs{display:flex;gap:4px;margin:14px 0 0;border-bottom:2px solid #e5e5e5;flex-wrap:wrap}
.tab{border:0;background:none;padding:10px 18px;font-size:15px;cursor:pointer;color:#456;border-bottom:3px solid transparent;margin-bottom:-2px}
.tab:hover{background:#f4f7f9}.tab.on{color:#1f6f8b;font-weight:700;border-bottom-color:#1f6f8b}
.monbar{margin:12px 0 0;text-align:right}
.ic.clk{cursor:pointer}.ic.clk:hover{filter:brightness(.97)}.ic.selc{outline:3px solid #1f6f8b66;outline-offset:1px}
.dp{border:1px solid #dfe6eb;border-radius:10px;padding:6px 16px 16px;margin:10px 0 6px;background:#fff}
.h2s{font-size:17px;margin:16px 0 4px;border:0}.dp h3{font-size:14px;margin:14px 0 4px}
.dsec{margin-top:14px}
table.sub{font-size:12px;margin:0}table.sub th,table.sub td{background:#fafbfc}
.ctrl select{font-weight:600}
</style></head><body><div class="wrap">
<h1>Dashboard de Indicadores</h1>
<div class="sub" id="sub"></div>
<div id="avisos"></div>
<div class="ctrl" id="ctrl"></div>
<div class="tabs" id="tabs"></div>
<section id="tab_com">
<div class="monbar"><span class="seg" id="monseg"><button data-c="usd">USD</button><button data-c="cop">COP</button></span></div>
<div class="kpis" id="kpis"></div>
<div class="kpis" id="ytd"></div>
<div id="bloque_ind" style="display:none"><h2 id="t_ind">Indicadores del área comercial</h2>
<div class="small" style="margin:4px 0"><button class="chip" id="btn_metas">✎ Editar metas</button> <span class="muted" id="metas_est"></span> <span class="muted" id="com_est"></span></div>
<div id="ind_edit" style="display:none"></div>
<div class="ind" id="ind_cards"></div><div id="ind_tabla"></div>
<div class="small muted" id="ind_nota"></div></div>
<h2>Ventas por mes y acumulado del año</h2>
<div class="small muted">Haga clic en un mes de la gráfica (o en los botones de arriba) para ver su detalle.</div>
<div id="chart"></div>
<div class="grid2">
  <div><h2 id="t_cli">Clientes</h2><div id="r_cli"></div></div>
  <div><h2 id="t_ven">Vendedores</h2><div id="r_ven"></div></div>
  <div><h2 id="t_pro">Productos</h2><div id="r_pro"></div></div>
  <div><h2 id="t_cat">Categorías de producto</h2><div id="r_cat"></div></div>
</div>
<h2>Equipos de venta</h2><div id="r_equ"></div>
<div id="bloque_emp"><h2>Empresas</h2><div id="r_emp"></div></div>
<h2>Resumen mensual</h2><div id="tabla"></div>
<div class="nota">Cifras sin IVA de facturas de cliente publicadas; las notas crédito restan. Fecha contable del asiento. El valor
contable en COP es el oficial (moneda de la compañía). El USD es el valor de la factura cuando fue emitida en USD; en facturas
emitidas en COP se convierte con la TRM de la tabla de tasas de Odoo a la fecha de la factura. Las variaciones en USD y en COP
pueden diferir por el efecto de la TRM. Fuente: Odoo 19, solo lectura.</div>
</section>
<section id="tab_adm" style="display:none">
<h2 id="t_adm">Indicadores del área administrativa</h2>
<div class="small" style="margin:4px 0"><button class="chip" id="btn_metas_adm">✎ Editar metas</button> <span class="muted" id="adm_est"></span></div>
<div id="adm_edit" style="display:none"></div>
<div id="adm_root"></div>
</section>
<section id="tab_dis" style="display:none">
<h2 id="t_dis">Indicadores del área de Diseño</h2>
<div class="small" style="margin:4px 0"><button class="chip" id="btn_metas_dis">✎ Editar metas</button> <span class="muted" id="dis_est"></span></div>
<div id="dis_edit" style="display:none"></div>
<div id="dis_root"></div>
</section>
<section id="tab_pro" style="display:none">
<h2 id="t_pro">Indicadores del área de Producción</h2>
<div class="small" style="margin:4px 0"><button class="chip" id="btn_metas_pro">✎ Editar metas</button> <span class="muted" id="pro_est"></span></div>
<div id="pro_edit" style="display:none"></div>
<div id="pro_root"></div>
</section>
</div>
<script type="application/json" id="datos">__DATOS__</script>
<script>
(function(){
const RAW = JSON.parse(document.getElementById('datos').textContent);
const MULTI = RAW.por_anio ? RAW : {anios:[RAW.anio], por_anio:{[RAW.anio]:RAW}, anio_defecto:RAW.anio};
let D, MC, ML, U;
const nf = new Intl.NumberFormat('es-CO',{maximumFractionDigits:0});
const nf1 = new Intl.NumberFormat('es-CO',{maximumFractionDigits:1});
const pf = new Intl.NumberFormat('es-CO',{minimumFractionDigits:1,maximumFractionDigits:1});
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const st = {anio:null, mes:0, mon:'usd', emp:-1, empName:null, tab:'com', ind:'razon', com:''};
const OTRA = {usd:'cop', cop:'usd'};
// fila r: [mes, emp, cli, ven, equ, pro, cat, fac, usd, cop]
let M, P, RW;

function compute(){
  const vi = st.com ? D.vendedores.findIndex(n => nrm(n)===nrm(st.com)) : -2;
  RW = D.r.filter(r => (st.emp<0 || r[1]===st.emp) && (vi===-2 || r[3]===vi));
  M = {usd:Array(12).fill(0), cop:Array(12).fill(0), n:Array(12).fill(0)};
  const sets = Array.from({length:12}, () => new Set());
  RW.forEach(r => {const m=r[0]-1; M.usd[m]+=r[8]; M.cop[m]+=r[9]; if(D.ftipo[r[7]]===0) sets[m].add(r[7]);});
  M.n = sets.map(s => s.size);
  P = {usd:Array(12).fill(0), cop:Array(12).fill(0)};
  D.pr.forEach(r => {if((st.emp<0 || r[1]===st.emp) && (vi===-2 || r[4]===vi)){P.usd[r[0]-1]+=r[2]; P.cop[r[0]-1]+=r[3];}});
}
const sum = (a,from,to) => {let s=0; for(let i=from;i<to;i++) s+=a[i]; return s;};
const money = (v,mon) => (mon==='usd'?'US$ ':'COP ') + nf.format(Math.round(v));
function abbr(v,mon,pre){
  const a=Math.abs(v), p = pre ? (mon==='usd'?'US$ ':'$ ') : '';
  if(a>=1e9) return p+nf1.format(v/1e9)+' mil M';
  if(a>=1e6) return p+nf1.format(v/1e6)+' M';
  if(a>=1e3) return p+nf1.format(v/1e3)+' k';
  return p+nf.format(v);
}
function varHtml(a,b,label){
  if(!b) return '<div class="cmp">'+label+': <span class="muted">sin base de comparación</span></div>';
  const p=(a-b)/b*100, up=p>=0;
  return '<div class="cmp">'+label+': <span class="'+(up?'up':'down')+'">'+(up?'▲':'▼')+' '+pf.format(Math.abs(p))+'%</span></div>';
}
function cmpFor(mon,val){
  if(st.mes===0) return varHtml(val, sum(P[mon],0,U), 'vs mismo periodo '+(D.anio-1));
  const m=st.mes-1, prevM = m>0 ? M[mon][m-1] : P[mon][11];
  return varHtml(val, prevM, 'vs mes anterior') + varHtml(val, P[mon][m], 'vs '+ML[m]+' '+(D.anio-1));
}
const periodoTxt = () => st.mes===0 ? ('Año '+D.anio+' ('+MC[0]+'–'+MC[U-1]+')') : (ML[st.mes-1]+' '+D.anio);
const filas = () => RW.filter(r => st.mes===0 || r[0]===st.mes);
const kpi = (l,v,c,cls) => '<div class="kpi '+(cls||'')+'"><div class="l">'+l+'</div><div class="v">'+v+'</div>'+(c||'')+'</div>';

function renderCtrl(){
  let h = '<select id="anio" title="Año">'+MULTI.anios.map(a => '<option value="'+a+'"'+(a===st.anio?' selected':'')+'>Año '+a+'</option>').join('')+'</select>';
  h += '<button class="chip todo" data-m="0">Año completo</button>';
  for(let i=1;i<=12;i++) h += '<button class="chip" data-m="'+i+'">'+MC[i-1]+'</button>';
  h += '<span class="sep"></span>';
  if(D.empresas.length>1){
    h += '<select id="emp"><option value="-1">Todas las empresas</option>' +
         D.empresas.map((e,i) => '<option value="'+i+'"'+(i===st.emp?' selected':'')+'>'+esc(e)+'</option>').join('') + '</select>';
  }
  const lc = comLista();
  h += '<select id="com" title="Filtrar por comercial"><option value="">Todos los comerciales</option>' +
       lc.map(k => '<option value="'+esc(k)+'"'+(nrm(k)===nrm(st.com)?' selected':'')+'>'+esc(k)+'</option>').join('') + '</select>';
  const el = document.getElementById('ctrl'); el.innerHTML = h;
  el.querySelectorAll('.chip').forEach(b => b.onclick = () => {st.mes=+b.dataset.m; render();});
  document.getElementById('anio').onchange = e => setAnio(+e.target.value);
  const sv = document.getElementById('com'); sv.onchange = () => {st.com=sv.value; render();};
  const se = document.getElementById('emp'); if(se) se.onchange = () => {st.emp=+se.value; st.empName = st.emp<0 ? '' : D.empresas[st.emp]; render();};
}
function updateCtrl(){
  document.querySelectorAll('#ctrl .chip').forEach(b => b.classList.toggle('on', +b.dataset.m===st.mes));
  document.querySelectorAll('#monseg button').forEach(b => b.classList.toggle('on', b.dataset.c===st.mon));
}
const TABS=[['com','Indicadores Área Comercial'],['adm','Indicadores Área Administrativa'],['dis','Indicadores Área Diseño'],['pro','Indicadores Área Producción']];
function renderTabs(){
  const el=document.getElementById('tabs');
  el.innerHTML=TABS.map(t => '<button class="tab" data-t="'+t[0]+'">'+t[1]+'</button>').join('');
  el.querySelectorAll('.tab').forEach(b => b.onclick = () => {st.tab=b.dataset.t; render();});
}
function updateTabs(){
  document.querySelectorAll('#tabs .tab').forEach(b => b.classList.toggle('on', b.dataset.t===st.tab));
  document.getElementById('tab_com').style.display = st.tab==='com' ? '' : 'none';
  const sc0=document.getElementById('com'); if(sc0) sc0.style.display = st.tab==='com' ? '' : 'none';
  document.getElementById('tab_adm').style.display = st.tab==='adm' ? '' : 'none';
  document.getElementById('tab_dis').style.display = st.tab==='dis' ? '' : 'none';
  document.getElementById('tab_pro').style.display = st.tab==='pro' ? '' : 'none';
}

function renderKpis(){
  const mon=st.mon, o=OTRA[mon], rows=filas();
  let u=0,c=0; const porCli=new Map(), fac=new Set();
  rows.forEach(r => {u+=r[8]; c+=r[9]; porCli.set(r[2],(porCli.get(r[2])||0)+r[8]); if(D.ftipo[r[7]]===0) fac.add(r[7]);});
  const n=fac.size, val=mon==='usd'?u:c, valO=mon==='usd'?c:u;
  const top5=[...porCli.values()].sort((a,b)=>b-a).slice(0,5).reduce((a,b)=>a+b,0);
  document.getElementById('kpis').innerHTML =
    kpi('Ventas facturadas sin IVA ('+mon.toUpperCase()+') · '+periodoTxt(), money(val,mon), cmpFor(mon,val)) +
    kpi(o==='cop'?'Valor contable (COP)':'Equivalente en USD', money(valO,o), cmpFor(o,valO)) +
    kpi('TRM promedio ponderada', u? 'COP '+nf.format(Math.round(c/u)) : '—', '') +
    kpi('Facturas emitidas', nf.format(n), '') +
    kpi('Ticket promedio por factura ('+mon.toUpperCase()+')', n? money(val/n,mon):'—', '') +
    kpi('Clientes facturados', nf.format(porCli.size), '<div class="cmp">Top 5 = '+(u? Math.round(top5/u*100):0)+'% de las ventas</div>');
  const h = st.mes===0 ? U : st.mes;
  const yu=sum(M.usd,0,h), yc=sum(M.cop,0,h), yn=sum(M.n,0,h), yv = mon==='usd'?yu:yc, yvo = mon==='usd'?yc:yu;
  const lab = 'Acumulado del año ('+MC[0]+'–'+MC[h-1]+')';
  const pv = k => varHtml(sum(M[k],0,h), sum(P[k],0,h), 'vs mismo periodo '+(D.anio-1));
  document.getElementById('ytd').innerHTML =
    kpi(lab+' · '+mon.toUpperCase(), money(yv,mon), pv(mon), 'ytd') +
    kpi(lab+' · '+o.toUpperCase(), money(yvo,o), pv(o), 'ytd') +
    kpi(lab+' · facturas', nf.format(yn), '<div class="cmp">TRM promedio: COP '+(yu?nf.format(Math.round(yc/yu)):'—')+'</div>', 'ytd');
}

function renderChart(){
  const mon=st.mon, W=960,H=350,ml=78,mr=78,mt=34,mb=34, iw=W-ml-mr, ih=H-mt-mb;
  const cur=M[mon], prv=P[mon], cum=[]; let s=0;
  cur.forEach((v,i) => {if(i<U){s+=v; cum.push(s);} else cum.push(null);});
  const maxB=Math.max(1,...cur,...prv), maxC=Math.max(1,...cum.filter(x=>x!==null));
  const x=i=>ml+iw/12*(i+.5), bw=iw/12*.5, yb=v=>mt+ih-v/maxB*ih, yc=v=>mt+ih-v/maxC*ih;
  let g='<svg viewBox="0 0 '+W+' '+H+'" width="100%">';
  for(let t=0;t<=4;t++){
    const y=mt+ih-ih*t/4;
    g+='<line x1="'+ml+'" x2="'+(W-mr)+'" y1="'+y+'" y2="'+y+'" stroke="#e5e5e5"/>'+
       '<text x="'+(ml-8)+'" y="'+(y+4)+'" text-anchor="end" font-size="11" fill="#666">'+abbr(maxB*t/4,mon,false)+'</text>'+
       '<text x="'+(W-mr+8)+'" y="'+(y+4)+'" font-size="11" fill="#14213d">'+abbr(maxC*t/4,mon,false)+'</text>';
  }
  cur.forEach((v,i) => {
    const sel = st.mes===i+1, h=Math.max(0,v/maxB*ih);
    g+='<rect x="'+(x(i)-bw/2)+'" y="'+yb(Math.max(v,0))+'" width="'+bw+'" height="'+h+'" fill="'+(sel?'#e08e45':'#1f6f8b')+'" rx="2"/>';
    if(v>0) g+='<text x="'+x(i)+'" y="'+(yb(v)-5)+'" text-anchor="middle" font-size="10" fill="#333">'+abbr(v,mon,false)+'</text>';
    g+='<text x="'+x(i)+'" y="'+(H-mb+18)+'" text-anchor="middle" font-size="12" fill="'+(sel?'#b8651b':'#444')+'" font-weight="'+(sel?700:400)+'">'+MC[i]+'</text>';
  });
  const pp=[]; prv.forEach((v,i) => {if(v>0) pp.push([x(i),yb(v)]);});
  if(pp.length) g+='<polyline points="'+pp.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="#9aa5ad" stroke-width="2" stroke-dasharray="5 4"/>'+
                   pp.map(p=>'<circle cx="'+p[0]+'" cy="'+p[1]+'" r="3" fill="#9aa5ad"/>').join('');
  const cp=[]; cum.forEach((v,i) => {if(v!==null) cp.push([x(i),yc(v)]);});
  if(cp.length) g+='<polyline points="'+cp.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="#14213d" stroke-width="2.5"/>'+
                   cp.map(p=>'<circle cx="'+p[0]+'" cy="'+p[1]+'" r="3.5" fill="#14213d"/>').join('');
  cur.forEach((v,i) => {
    g+='<rect class="hit" data-m="'+(i+1)+'" x="'+(x(i)-iw/24)+'" y="'+mt+'" width="'+(iw/12)+'" height="'+(ih+mb)+'" fill="transparent"><title>'+
       ML[i]+' '+D.anio+': '+money(v,mon)+' · '+M.n[i]+' facturas'+(cum[i]!==null?' · acumulado '+money(cum[i],mon):'')+
       ' · '+(D.anio-1)+': '+money(prv[i],mon)+'</title></rect>';
  });
  g+='<g font-size="12"><rect x="'+ml+'" y="8" width="12" height="12" fill="#1f6f8b" rx="2"/><text x="'+(ml+18)+'" y="18">Ventas del mes</text>'+
     '<rect x="'+(ml+130)+'" y="8" width="12" height="12" fill="#e08e45" rx="2"/><text x="'+(ml+148)+'" y="18">Mes seleccionado</text>'+
     '<line x1="'+(ml+270)+'" x2="'+(ml+290)+'" y1="14" y2="14" stroke="#14213d" stroke-width="2.5"/><text x="'+(ml+296)+'" y="18">Acumulado del año (eje derecho)</text>'+
     '<line x1="'+(ml+500)+'" x2="'+(ml+520)+'" y1="14" y2="14" stroke="#9aa5ad" stroke-width="2" stroke-dasharray="5 4"/><text x="'+(ml+526)+'" y="18">'+(D.anio-1)+'</text></g></svg>';
  const el=document.getElementById('chart'); el.innerHTML=g;
  el.querySelectorAll('.hit').forEach(r => r.onclick = () => {st.mes=+r.dataset.m; render();});
}

function agg(rows,key,vi){const m=new Map(); rows.forEach(r => m.set(r[key],(m.get(r[key])||0)+r[vi])); return m;}
function ranking(id,map,names,n){
  const arr=[...map.entries()].map(([k,v]) => [names[k],v]).sort((a,b)=>b[1]-a[1]);
  const tot=arr.reduce((a,x)=>a+x[1],0), top=arr.slice(0,n), mx=top.length?Math.max(top[0][1],1):1;
  document.getElementById(id).innerHTML = top.length ?
    top.map(([nm,v]) => '<div class="rk"><div class="rk-n" title="'+esc(nm)+'">'+esc(nm)+'</div><div class="rk-b"><i style="width:'+Math.max(v/mx*100,.5)+'%"></i></div><div class="rk-v">'+abbr(v,st.mon,true)+'</div><div class="rk-p">'+(tot?pf.format(v/tot*100):'0,0')+'%</div></div>').join('') +
      (arr.length>n ? '<div class="muted small">+ '+(arr.length-n)+' más</div>' : '')
    : '<div class="muted">Sin datos en este periodo.</div>';
}
function renderRankings(){
  const vi = st.mon==='usd'?8:9, rows=filas(), pt=periodoTxt();
  document.getElementById('t_cli').textContent='Top clientes · '+pt;
  document.getElementById('t_ven').textContent='Vendedores · '+pt;
  document.getElementById('t_pro').textContent='Top productos · '+pt;
  document.getElementById('t_cat').textContent='Categorías de producto · '+pt;
  ranking('r_cli',agg(rows,2,vi),D.clientes,10);
  ranking('r_ven',agg(rows,3,vi),D.vendedores,10);
  ranking('r_pro',agg(rows,5,vi),D.productos,10);
  ranking('r_cat',agg(rows,6,vi),D.categorias,10);
  ranking('r_equ',agg(rows,4,vi),D.equipos,10);
  ranking('r_emp',agg(rows,1,vi),D.empresas,10);
  document.getElementById('bloque_emp').style.display = D.empresas.length>1 ? '' : 'none';
}

function renderTabla(){
  const mon=st.mon; let h='<table><thead><tr><th>Mes</th><th>Ventas ('+mon.toUpperCase()+')</th><th>'+(OTRA[mon]==='cop'?'Valor contable (COP)':'Equivalente (USD)')+'</th><th>TRM prom.</th><th>Facturas</th><th>vs '+(D.anio-1)+'</th><th>Acumulado ('+mon.toUpperCase()+')</th></tr></thead><tbody>';
  let acc=0;
  for(let i=0;i<12;i++){
    const v=M[mon][i], o=M[OTRA[mon]][i], trm=M.usd[i]?M.cop[i]/M.usd[i]:0, pv=P[mon][i];
    if(i<U) acc+=v;
    const vs = (i<U && pv) ? '<span class="'+(v>=pv?'up':'down')+'">'+(v>=pv?'▲':'▼')+' '+pf.format(Math.abs((v-pv)/pv*100))+'%</span>' : '<span class="muted">—</span>';
    h+='<tr data-m="'+(i+1)+'" class="'+(st.mes===i+1?'sel':'')+'"><td>'+ML[i]+'</td><td>'+(i<U?money(v,mon):'—')+'</td><td>'+(i<U?money(o,OTRA[mon]):'—')+'</td><td>'+(trm?nf.format(Math.round(trm)):'—')+'</td><td>'+(i<U?M.n[i]:'—')+'</td><td>'+vs+'</td><td>'+(i<U?money(acc,mon):'—')+'</td></tr>';
  }
  const tu=sum(M.usd,0,U), tc=sum(M.cop,0,U), tv=mon==='usd'?tu:tc, to=mon==='usd'?tc:tu, pv=sum(P[mon],0,U);
  h+='</tbody><tfoot><tr><td>Total '+MC[0]+'–'+MC[U-1]+'</td><td>'+money(tv,mon)+'</td><td>'+money(to,OTRA[mon])+'</td><td>'+(tu?nf.format(Math.round(tc/tu)):'—')+'</td><td>'+nf.format(sum(M.n,0,U))+'</td><td>'+(pv?('<span class="'+(tv>=pv?'up':'down')+'">'+(tv>=pv?'▲':'▼')+' '+pf.format(Math.abs((tv-pv)/pv*100))+'%</span>'):'—')+'</td><td></td></tr></tfoot></table>';
  const el=document.getElementById('tabla'); el.innerHTML=h;
  el.querySelectorAll('tbody tr').forEach(r => r.onclick = () => {st.mes=+r.dataset.m; render(); window.scrollTo({top:0,behavior:'smooth'});});
}

const INAMES = {opp:'Nuevas oportunidades', pipe:'Pipeline', po:'Closed PO', bill:'Billing'};
const IDESC = {opp:'Proyectos nuevos del mes', pipe:'Cotizaciones del mes sin aprobar', po:'Órdenes de compra recibidas', bill:'Facturación del mes'};
const fmtI = (i,v) => i==='opp' ? nf.format(v) : 'US$ '+abbr(v,'usd',false);
function colorI(i,p){
  if(p===null) return 'n';
  const u=D.ind.umbral[i], a=D.ind.amarillo;
  return p>=u ? 'g' : (p>=u*a ? 'y' : 'r');
}
const nrm = t => String(t||'').trim().toLowerCase();
function comVend(){return (D.ind && D.ind.vend) || {};}
function comLista(){
  const n={}; Object.keys(comVend()).forEach(k => n[nrm(k)]=k);
  D.vendedores.forEach(k => {if(!(nrm(k) in n)) n[nrm(k)]=k;});
  return Object.values(n).filter(k => nrm(k)!=='' && nrm(k)!=='false').sort((a,b) => a.localeCompare(b));
}
function valI(x,i){
  if(!st.com){
    if(i!=='bill') return x.val[i];
    return D.r.reduce((a,r) => (r[0]===x.m && (st.emp<0 || r[1]===st.emp)) ? a+r[8] : a, 0);
  }
  const c=nrm(st.com);
  if(i==='bill') return D.r.reduce((a,r) => (r[0]===x.m && (st.emp<0 || r[1]===st.emp) && nrm(D.vendedores[r[3]])===c) ? a+r[8] : a, 0);
  const k=Object.keys(comVend()).find(n => nrm(n)===c), v=k?comVend()[k][i]:null;
  return v ? (v[x.m-1]||0) : 0;
}
function indTodos(fn){const c=st.com; st.com=''; try{return fn();}finally{st.com=c;}}
function indPeriodo(i){
  // devuelve {val, meta} del periodo seleccionado
  const ms=D.ind.meses.filter(x => x.val);
  if(st.mes>0){const x=D.ind.meses[st.mes-1]; return x.val ? {val:valI(x,i), meta:x.meta[i]} : null;}
  if(!ms.length) return null;
  if(i==='pipe'){const x=ms[ms.length-1]; return {val:valI(x,'pipe'), meta:x.meta.pipe};}
  return {val:ms.reduce((a,x)=>a+valI(x,i),0), meta:ms.reduce((a,x)=>a+x.meta[i],0)};
}
function renderInd(){
  const blk=document.getElementById('bloque_ind');
  if(!D.ind || !D.ind.ok){blk.style.display='none'; return;}
  blk.style.display='';
  document.getElementById('com_est').textContent = st.com ? 'Vista de '+st.com+': no hay metas por comercial; cada indicador muestra su valor y su peso en el total del equipo.' : '';
  document.getElementById('btn_metas').style.display = st.com ? 'none' : '';
  if(st.com) document.getElementById('ind_edit').style.display='none';
  document.getElementById('t_ind').textContent='Indicadores del área comercial'+(st.com?' · '+st.com:'')+' · '+periodoTxt();
  let h='';
  ['opp','pipe','po','bill'].forEach(i => {
    const r=indPeriodo(i);
    if(!r){h+='<div class="ic"><div class="l">'+INAMES[i]+'</div><div class="p">—</div></div>'; return;}
    let p=r.meta? r.val/r.meta*100 : null, c=colorI(i,p), tot=null;
    if(st.com){const t=indTodos(() => indPeriodo(i)); tot=(t && t.val)? r.val/t.val*100 : null; p=tot; c='';}
    let ref='';
    if(st.mes>0){const hj=D.ind.meses[st.mes-1].hoja; if(hj && hj[i]!==undefined) ref='<div class="small muted">Según la hoja: '+hj[i]+'%</div>';}
    const sub = (st.mes===0 && i==='pipe') ? 'cierre de '+MC[D.ind.meses.filter(x=>x.val).length-1] : IDESC[i];
    h+='<div class="ic '+c+'"><div class="l">'+INAMES[i]+'</div><div class="p">'+(p===null?'—':Math.round(p)+'%')+'</div>'+
       '<div class="pb"><i style="width:'+Math.min(100,p||0)+'%"></i></div>'+
       (st.com ? '<div class="small"><b>'+fmtI(i,r.val)+'</b> · '+(tot===null?'—':Math.round(tot)+'% del equipo')+'</div>' : '<div class="small"><b>'+fmtI(i,r.val)+'</b> de '+fmtI(i,r.meta)+'</div>')+'<div class="small muted">'+sub+(st.com?'':' · umbral '+D.ind.umbral[i]+'%')+'</div>'+(st.com?'':ref)+'</div>';
  });
  document.getElementById('ind_cards').innerHTML=h;
  let t='<table><thead><tr><th>Mes</th>'+['opp','pipe','po','bill'].map(i=>'<th>'+INAMES[i]+'</th>').join('')+'</tr></thead><tbody>';
  D.ind.meses.forEach((x,k) => {
    t+='<tr data-m="'+(k+1)+'" class="'+(st.mes===k+1?'sel':'')+'"><td>'+ML[k]+'</td>';
    ['opp','pipe','po','bill'].forEach(i => {
      if(!x.val){t+='<td><span class="pill n">—</span></td>'; return;}
      const vv=valI(x,i), p=x.meta[i]? vv/x.meta[i]*100 : null, hj = x.hoja && x.hoja[i]!==undefined ? ' <span class="muted small">(hoja '+x.hoja[i]+'%)</span>' : '';
      if(st.com){t+='<td><span class="pill n">'+fmtI(i,vv)+'</span></td>'; return;}
      t+='<td title="'+fmtI(i,vv)+' de '+fmtI(i,x.meta[i])+'"><span class="pill '+colorI(i,p)+'">'+(p===null?'—':Math.round(p)+'%')+'</span>'+hj+'</td>';
    });
    t+='</tr>';
  });
  t+='</tbody></table>';
  const el=document.getElementById('ind_tabla'); el.innerHTML=t;
  el.querySelectorAll('tbody tr').forEach(r => r.onclick = () => {st.mes=+r.dataset.m; render();});
  document.getElementById('ind_nota').innerHTML='Verde: cumple el umbral del indicador; amarillo: al menos '+Math.round(D.ind.amarillo*100)+'% del umbral; rojo: por debajo. '+
    'Indicadores siempre en USD, sin importar el selector de moneda. Billing sigue el filtro de empresa; los demás indicadores son de toda la operación. Nuevas oportunidades = proyectos nuevos del mes (variantes A, B, C del mismo número cuentan una vez). Pipeline = proyectos que entraron a Cotización en el mes y siguen sin aprobar (ingreso esperado en USD). '+
    'Closed PO = órdenes de venta confirmadas en el mes. Billing = facturación del mes (sección de ventas). En “Año completo”: oportunidades, Closed PO y Billing se acumulan contra la suma de metas; Pipeline muestra el último cierre. Las metas se pueden editar con el botón “Editar metas”; los valores iniciales vienen de metas_comercial.json (hoja de indicadores).';
}

// ---- metas compartidas (Google Sheets por Apps Script) ----
const NUBE_URL='__NUBE__';
let NUBE_OK=false;
function nubeCargar(){
  if(!NUBE_URL) return Promise.resolve();
  const ctl=new AbortController(), t=setTimeout(()=>ctl.abort(), 6000);
  return fetch(NUBE_URL+'?accion=leer',{signal:ctl.signal}).then(r=>r.json()).then(j=>{
    clearTimeout(t);
    if(j && j.ok && j.datos){NUBE_OK=true; Object.keys(j.datos).forEach(k=>{try{localStorage.setItem(k, j.datos[k]);}catch(e){}});}
  }).catch(()=>{clearTimeout(t);});
}
function nubeGuardar(k,v){
  if(!NUBE_URL) return;
  let pin=''; try{pin=sessionStorage.getItem('diforma_pin')||'';}catch(e){}
  if(!pin){pin=prompt('Clave de edición para guardar las metas para todos (cancele para guardar solo en este navegador):')||''; if(!pin) return;}
  fetch(NUBE_URL,{method:'POST',body:JSON.stringify({pin:pin,clave:k,valor:v})}).then(r=>r.json()).then(j=>{
    if(j && j.ok){try{sessionStorage.setItem('diforma_pin',pin);}catch(e){} avisoNube('Metas guardadas para todos.');}
    else{try{sessionStorage.removeItem('diforma_pin');}catch(e){} avisoNube('No se guardó para todos: '+((j&&j.error)||'error')+'. Quedó solo en este navegador.');}
  }).catch(()=>avisoNube('No se pudo contactar la hoja compartida. Quedó solo en este navegador.'));
}
function avisoNube(m){
  let e=document.getElementById('nube_aviso');
  if(!e){e=document.createElement('div'); e.id='nube_aviso'; e.style.cssText='position:fixed;right:12px;bottom:12px;background:#222;color:#fff;padding:8px 12px;border-radius:8px;font-size:13px;z-index:99'; document.body.appendChild(e);}
  e.textContent=m; e.style.display=''; clearTimeout(e._t); e._t=setTimeout(()=>{e.style.display='none';}, 5000);
}

// ---- metas editables (se guardan en este navegador) ----
let MKEY=''; const IK=['opp','pipe','po','bill'];
let DEFM=null;
function initMetas(){
  MKEY='diforma_metas_'+D.anio; document.getElementById('ind_edit').style.display='none';
  if(!D.ind) return;
  DEFM=D.ind.meses.map(x => Object.assign({}, x.meta));
  try{const sv=JSON.parse(localStorage.getItem(MKEY)||'null');
    if(sv) sv.forEach((m,k) => {if(m && D.ind.meses[k]) IK.forEach(i => {if(typeof m[i]==='number' && m[i]>=0) D.ind.meses[k].meta[i]=m[i];});});}catch(e){}
}
function saveMetas(){try{const v=JSON.stringify(D.ind.meses.map(x=>x.meta)); localStorage.setItem(MKEY, v); nubeGuardar(MKEY,v);}catch(e){}}
const hayCambios = () => D.ind.meses.some((x,k) => IK.some(i => x.meta[i]!==DEFM[k][i]));
function estMetas(){document.getElementById('metas_est').textContent = !D.ind ? '' : hayCambios() ? 'Metas modificadas a mano (guardadas en este navegador).' : 'Metas de la hoja de indicadores.';}
function renderEdit(){
  const el=document.getElementById('ind_edit');
  let h='<div class="ed"><div class="small muted" style="margin-bottom:6px">Escriba la meta y el dashboard se actualiza al instante. Oportunidades en cantidad; las demás en US$. Los cambios se guardan en este navegador. Las celdas amarillas difieren de la hoja.</div><table><thead><tr><th>Mes</th>'+IK.map(i=>'<th>'+INAMES[i]+'</th>').join('')+'</tr></thead><tbody>';
  h+='<tr><td><b>Todos los meses</b></td>'+IK.map(i=>'<td><input type="number" min="0" data-all="'+i+'" placeholder="aplicar a todos"></td>').join('')+'</tr>';
  D.ind.meses.forEach((x,k) => {
    h+='<tr><td>'+ML[k]+'</td>'+IK.map(i=>'<td><input type="number" min="0" data-k="'+k+'" data-i="'+i+'" value="'+x.meta[i]+'" class="'+(x.meta[i]!==DEFM[k][i]?'mod':'')+'"></td>').join('')+'</tr>';
  });
  h+='</tbody></table><div style="margin-top:8px"><button class="chip" id="btn_reset">Restablecer metas de la hoja</button></div></div>';
  el.innerHTML=h;
  const refresh=()=>{saveMetas(); estMetas(); renderInd();};
  el.querySelectorAll('input[data-k]').forEach(inp => inp.oninput = () => {
    const v=parseFloat(inp.value); if(isNaN(v)||v<0) return;
    const k=+inp.dataset.k, i=inp.dataset.i; D.ind.meses[k].meta[i]=v;
    inp.classList.toggle('mod', v!==DEFM[k][i]); refresh();
  });
  el.querySelectorAll('input[data-all]').forEach(inp => inp.onchange = () => {
    const v=parseFloat(inp.value); if(isNaN(v)||v<0) return;
    D.ind.meses.forEach(x => x.meta[inp.dataset.all]=v); refresh(); renderEdit();
  });
  document.getElementById('btn_reset').onclick = () => {D.ind.meses.forEach((x,k) => x.meta=Object.assign({},DEFM[k])); refresh(); renderEdit();};
}

// ================= ÁREA ADMINISTRATIVA =================
const AK=['razon','fin','apal','rot','cobro','pago','ciclo','bienestar'];
const ANAMES={razon:'Razón de endeudamiento',fin:'Endeudamiento financiero',apal:'Apalancamiento total',rot:'Rotación de cuentas por cobrar',
  cobro:'Días promedio de cobro',pago:'Días de mora al pagar a proveedores',ciclo:'Ciclo de efectivo',bienestar:'Bienestar (semestral)'};
const ADESC={razon:'Pasivo total / Activo total',fin:'Obligaciones financieras / Activo total',apal:'Pasivo total / Patrimonio',
  rot:'Ventas a crédito acumuladas / CxC promedio (anualizada)',cobro:'Acumulado del año, base saldos de cartera',
  pago:'Pagos del mes vs. fecha de vencimiento',ciclo:'Días de cobro + días de inventario',bienestar:'Dato manual (no sale de Odoo)'};
const AFORM={razon:'Pasivo total ÷ Activo total, a fin de mes.',
  fin:'Obligaciones financieras (cuentas 21*: bancos, tarjetas, leasing y deudas con particulares) ÷ Activo total.',
  apal:'Pasivo total ÷ Patrimonio (capital + utilidades acumuladas + resultado del ejercicio en curso). Si el patrimonio es negativo no es calculable.',
  rot:'Ventas a crédito acumuladas del año ÷ saldo promedio de cuentas por cobrar × (365 ÷ días transcurridos).',
  cobro:'Días transcurridos × saldo promedio de cuentas por cobrar ÷ ventas a crédito acumuladas (facturas con vencimiento posterior a la fecha de factura).',
  pago:'Promedio ponderado por valor de (fecha de pago − fecha de vencimiento) de las facturas de proveedor pagadas en el mes. Negativo = pagó antes de vencer. No incluye lo que sigue sin pagar: para eso vea la antigüedad de saldos.',
  ciclo:'Días de cobro + días de inventario (saldo de cuentas 14* ÷ costo de ventas acumulado × días transcurridos). Depende de que el inventario esté valorado en contabilidad.',
  bienestar:'Actividades de bienestar realizadas ÷ propuestas en el semestre. No sale de Odoo: se digita en “Editar metas”.'};
const PUC={
  act:{'11':'Efectivo y equivalentes','12':'Inversiones','13':'Deudores (cartera y anticipos)','14':'Inventarios','15':'Propiedades, planta y equipo','16':'Intangibles','17':'Diferidos','18':'Otros activos','19':'Valorizaciones'},
  pas:{'21':'Obligaciones financieras','22':'Proveedores','23':'Cuentas por pagar y retenciones','24':'Impuestos','25':'Obligaciones laborales','26':'Pasivos estimados y provisiones','27':'Diferidos','28':'Otros pasivos (anticipos recibidos)','29':'Otros pasivos'},
  pat:{'31':'Capital social','32':'Superávit de capital','33':'Reservas','34':'Revalorización','35':'Dividendos','36':'Resultado del ejercicio cerrado','37':'Utilidades de ejercicios anteriores','38':'Superávit por valorizaciones','res':'Resultado del ejercicio en curso'}};
const AUNI={razon:'x',fin:'x',apal:'x',rot:'x',cobro:'d',pago:'d',ciclo:'d',bienestar:'%'};
const COL={g:'#2e9e5b',y:'#e6b422',r:'#d64545',n:'#bbbbbb'};
const nf2 = new Intl.NumberFormat('es-CO',{minimumFractionDigits:2,maximumFractionDigits:2});
let AMETA=null, ADEF=null, ABIEN=null, ABDEF=null, AKEY='';
const EXP=new Set();

const admOk = () => !!(D.adm && D.adm.ok);
function admNombres(){return st.emp<0 ? D.adm.empresas : [D.empresas[st.emp]].filter(n => D.adm.m[n]);}
const admK = () => Math.min(st.mes>0 ? st.mes : D.adm.ultimo, D.adm.ultimo)-1;
function admComp(k){
  const ns=admNombres(), A=D.adm;
  const mes=(c,j)=>ns.reduce((a,n)=>a+(A.m[n][c][j]||0),0);
  let cx=0; for(let j=0;j<=k;j++) cx+=mes('cxc',j); cx/=(k+1);
  const ytd=c=>{let s=0; for(let j=0;j<=k;j++) s+=mes(c,j); return s;};
  return {ns:ns, act:mes('act',k), pas:mes('pas',k), pat:mes('pat',k), fin:mes('fin',k), cxc:mes('cxc',k), cxp:mes('cxp',k), cxcProm:cx,
    vcY:ytd('vc'), compY:ytd('comp'), cgY:ytd('cogs'), dias:A.dias[k], pa:mes('pa',k), pd:mes('pd',k), inv:mes('inv',k)};
}
function admVal(k){
  const c=admComp(k); if(!c.ns.length) return {};
  const cobro=c.vcY>0 ? c.dias*c.cxcProm/c.vcY : null, invd=c.cgY>0 ? c.dias*c.inv/c.cgY : null;
  return {razon:c.act? c.pas/c.act:null, fin:c.act? c.fin/c.act:null, apal:c.pat>0? c.pas/c.pat:null, patneg:c.pat<=0,
    rot:cobro? 365/cobro:null, cobro:cobro, pago:c.pa>0? c.pd/c.pa:null, ciclo:(cobro!==null&&invd!==null)? cobro+invd:null,
    bienestar:ABIEN[k], invd:invd, comp:c};
}
function admColor(i,v,patneg){
  if(i==='apal' && patneg) return 'r';
  if(v===null||v===undefined||isNaN(v)) return 'n';
  const m=AMETA[i], A=D.adm;
  if(A.tipo[i]==='min') return v>=m ? 'g' : (v>=m*A.amarillo_min ? 'y' : 'r');
  return v<=m ? 'g' : (v<=m*A.amarillo_max ? 'y' : 'r');
}
function admFmt(i,v,patneg){
  if(i==='apal' && patneg) return 'Pat. neg.';
  if(v===null||v===undefined||isNaN(v)) return '—';
  if(i==='razon'||i==='fin'||i==='apal') return nf2.format(v);
  if(i==='rot') return nf1.format(v);
  if(i==='bienestar') return nf.format(v)+'%';
  return nf.format(Math.round(v))+' d';
}
const cop = v => abbr(v,'cop',true);
const diasEntre = (hoy,f) => Math.round((Date.parse(hoy)-Date.parse(f))/864e5);

// ---- bloques de detalle ----
function barList(rows,total){
  const mx=Math.max(1,...rows.map(r=>Math.abs(r[1])));
  return rows.map(r => '<div class="rk"><div class="rk-n" title="'+esc(r[0])+'">'+esc(r[0])+'</div><div class="rk-b"><i style="width:'+Math.max(Math.abs(r[1])/mx*100,.5)+'%'+(r[1]<0?';background:#c62828':'')+'"></i></div><div class="rk-v">'+cop(r[1])+'</div><div class="rk-p">'+(total? pf.format(r[1]/total*100):'0,0')+'%</div></div>').join('');
}
function sumGrp(lado,k){
  const m=new Map();
  admNombres().forEach(n => {const g=D.adm.m[n].grp[lado]||{}; Object.keys(g).forEach(p => {const v=g[p][k]; if(v) m.set(p,(m.get(p)||0)+v);});});
  return m;
}
function modComposicion(k,conPat){
  const blk=(lado,tit) => {
    const rows=[...sumGrp(lado,k).entries()].map(e => [(PUC[lado][e[0]]||('Cuentas '+e[0]+'*'))+(e[0]==='res'?'':' ('+e[0]+')'),e[1]]).sort((a,b)=>b[1]-a[1]);
    const tot=rows.reduce((a,x)=>a+x[1],0);
    return '<div><h3>'+tit+' · '+cop(tot)+'</h3>'+(rows.length? barList(rows,tot) : '<div class="muted small">Sin datos.</div>')+'</div>';
  };
  return '<div class="grid2">'+blk('pas','Composición del pasivo')+blk('act','Composición del activo')+(conPat? blk('pat','Composición del patrimonio'):'')+'</div>';
}
function modCuentas(lado,pref,tit,n){
  const k=admK(), m=new Map();
  admNombres().forEach(nm => D.adm.m[nm].cuentas.forEach(c => {
    if(c[0]!==lado || (pref && !c[1].startsWith(pref))) return;
    const v=c[3][k]; if(!v) return; const key=c[1]+'\u0001'+c[2]; m.set(key,(m.get(key)||0)+v);}));
  const rows=[...m.entries()].sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])), tot=rows.reduce((a,x)=>a+x[1],0);
  let h='<h3>'+tit+' · '+cop(tot)+'</h3>';
  if(!rows.length) return h+'<div class="muted small">Sin saldos.</div>';
  h+='<div class="ed"><table><thead><tr><th>Código</th><th>Cuenta</th><th>Saldo</th><th>%</th></tr></thead><tbody>'+
    rows.slice(0,n||20).map(r => {const p=r[0].split('\u0001'); return '<tr><td>'+esc(p[0])+'</td><td style="text-align:left">'+esc(p[1])+'</td><td>'+cop(r[1])+'</td><td>'+(tot? pf.format(r[1]/tot*100):'—')+'%</td></tr>';}).join('')+
    '</tbody></table></div>'+(rows.length>(n||20)?'<div class="muted small">+ '+(rows.length-(n||20))+' cuentas más</div>':'');
  return h;
}
function abMap(lado){
  const m=new Map(), F=['ab','fav','apl','ant','antsob','nv','d30','d60','d90','d91','pond','base','n'];
  admNombres().forEach(nm => {
    const L=((D.adm.abiertos||{})[nm]||{})[lado]||[];
    L.forEach(r => {let a=m.get(r.t); if(!a){a={t:r.t,docs:[]}; F.forEach(f=>a[f]=0); m.set(r.t,a);} F.forEach(f=>a[f]+=r[f]); a.docs=a.docs.concat(r.docs);});
  });
  return m;
}
function modTerceros(g,tit,lado,n){
  const k=admK(), m=new Map();
  admNombres().forEach(nm => ((D.adm.m[nm].terc||{})[g]||[]).forEach(r => {const x=r[1][k]; if(x) m.set(r[0],(m.get(r[0])||0)+x);}));
  const rows=[...m.entries()].sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])), tot=rows.reduce((a,x)=>a+x[1],0);
  const ab = lado ? abMap(lado) : null;
  let h='<h3>'+tit+' · '+cop(tot)+' <span class="muted small">(saldo a fin de '+ML[k]+')</span></h3>';
  if(!rows.length) return h+'<div class="muted small">Sin saldos.</div>';
  h+='<div class="ed"><table><thead><tr><th>Tercero</th><th>Saldo</th><th>% del total</th>'+(ab?'<th>Vencido hoy</th><th>Mora prom. hoy</th>':'')+'</tr></thead><tbody>';
  rows.slice(0,n||15).forEach(r => {
    const a = ab ? ab.get(r[0]) : null, venc = a ? a.d30+a.d60+a.d90+a.d91 : 0;
    h+='<tr><td title="'+esc(r[0])+'">'+esc(r[0])+'</td><td>'+cop(r[1])+'</td><td>'+(tot? pf.format(r[1]/tot*100):'—')+'%</td>'+
       (ab ? '<td>'+(a? cop(venc):'—')+'</td><td>'+(a && a.base? nf.format(Math.round(a.pond/a.base))+' d':'—')+'</td>' : '')+'</tr>';
  });
  h+='</tbody></table></div>'+(rows.length>(n||15)?'<div class="muted small">+ '+(rows.length-(n||15))+' terceros más</div>':'');
  return h;
}
const EXPC=new Set(), DEBC=new Set(['prov','fin','antc','lab','ret','prv','otp']);
const CONC={
  cli:'Clientes (cartera, neta de deterioro)', ant:'Anticipos y avances entregados (proveedores y otros)', trab:'Trabajadores y socios',
  otr:'Otros deudores', vinc:'Empresas vinculadas (entre las empresas del grupo)', imp:'Impuestos: posición neta con la DIAN (IVA, renta, retenciones a favor)',
  prov:'Proveedores y cuentas por pagar', fin:'Obligaciones financieras (bancos, tarjetas, particulares)', antc:'Anticipos recibidos de clientes y otros pasivos',
  lab:'Obligaciones laborales y seguridad social', ret:'Retenciones por pagar (DIAN y municipios)', prv:'Provisiones y estimados', otp:'Otros pasivos'};
function concDe(cod,t,vs){
  const c2=cod.slice(0,2), c4=cod.slice(0,4);
  if(c2==='24' || c4==='1355') return 'imp';
  if(vs.has(t)) return 'vinc';
  if(c2==='13'){
    if(c4==='1305' || c4==='1399') return 'cli';
    if(cod.slice(0,6)==='133015' || c4==='1365') return 'trab';
    if(c4==='1330') return 'ant';
    return 'otr';
  }
  if(c2==='21') return 'fin';
  if(c2==='22' || c4==='2335') return 'prov';
  if(c2==='25' || c4==='2370') return 'lab';
  if(c4==='2365' || c4==='2367' || c4==='2368') return 'ret';
  if(c2==='26') return 'prv';
  if(c2==='28') return 'antc';
  return 'otp';
}
function posiciones(){
  const k=admK(), nm=admNombres(), names={}, C={}, T=new Map();
  const vs=new Set((D.adm.vinc||[]).filter(n => nm.length>1 || nm[0]!==n));
  nm.forEach(n => { const M=D.adm.m[n]; Object.assign(names,M.nmcod||{});
    (M.at||[]).forEach(r => {
      const v=r[2][k]||0; if(!v) return;
      const c=concDe(r[0],r[1],vs), o=C[c]||(C[c]={tot:0,hij:new Map()}), pc=(c==='imp'||c==='vinc'), key=pc? r[0] : r[1];
      o.tot+=v;
      const h=o.hij.get(key)||{l: pc ? r[0]+' '+(names[r[0]]||'') : r[1], v:0}; h.v+=v; o.hij.set(key,h);
      if(c!=='imp') T.set(r[1],(T.get(r[1])||0)+v);
    }); });
  return {C,T,k};
}
function tablaPos(P,lado){
  const sg = lado==='cob' ? 1 : -1;
  const filas=Object.keys(P.C).map(c => ({c, v:P.C[c].tot*sg, o:P.C[c]})).filter(x => x.v>1000).sort((a,b)=>b.v-a.v);
  const tot=filas.reduce((a,x)=>a+x.v,0);
  let h='<div class="ed"><table><thead><tr><th>Concepto</th><th>Saldo</th><th>%</th></tr></thead><tbody>';
  if(!filas.length) h+='<tr><td colspan="3" class="muted">Sin saldos.</td></tr>';
  filas.forEach(x => {
    const key=lado+'|'+x.c, ab=EXPC.has(key);
    h+='<tr class="pc'+(ab?' sel':'')+'" data-k="'+key+'" style="cursor:pointer"><td style="text-align:left">'+(ab?'▾ ':'▸ ')+esc(CONC[x.c])+((lado==='cob' && DEBC.has(x.c)) ? ' <span class="down small">— saldo débito en cuentas del pasivo (revisar)</span>' : (lado==='deb' && !DEBC.has(x.c) && x.c!=='imp' && x.c!=='vinc') ? ' <span class="down small">— saldo crédito en cuentas del activo (pagos sin aplicar)</span>' : '')+'</td><td>'+cop(x.v)+'</td><td>'+(tot? pf.format(x.v/tot*100):'—')+'%</td></tr>';
    if(ab){
      const hs=[...x.o.hij.values()].sort((a,b)=>Math.abs(b.v)-Math.abs(a.v)), n=12;
      h+='<tr><td colspan="3" style="padding:0"><table class="sub"><tbody>'+hs.slice(0,n).map(y => '<tr><td style="text-align:left" title="'+esc(y.l)+'">'+esc(y.l)+'</td><td class="'+((y.v*sg)<0?'down':'')+'">'+cop(y.v*sg)+'</td></tr>').join('')+
         (hs.length>n?'<tr><td colspan="2" class="muted small">+ '+(hs.length-n)+' más</td></tr>':'')+'</tbody></table></td></tr>';
    }
  });
  h+='</tbody><tfoot><tr><td>Total</td><td>'+cop(tot)+'</td><td></td></tr></tfoot></table></div>';
  return {h,tot};
}
function renderPos(){
  const el=document.getElementById('pos_root'); if(!el) return;
  const P=posiciones(), A1=tablaPos(P,'cob'), A2=tablaPos(P,'deb');
  const cv=new Map(); D.adm.vinc && admNombres().forEach(n => (D.adm.m[n].at||[]).forEach(r => {
    const v=r[2][P.k]||0; if(!v) return; const c4=r[0].slice(0,4), c2=r[0].slice(0,2);
    if(c4!=='1305' && c2!=='28') return;
    const o=cv.get(r[1])||{c:0,a:0}; if(c4==='1305') o.c+=v; else o.a-=v; cv.set(r[1],o); }));
  const cva=[...cv.entries()].filter(r => r[1].a>1e5 && Math.abs(r[1].c)>1e5).sort((a,b)=>b[1].c-a[1].c);
  const cvH = !cva.length ? '' :
    '<h3>Clientes con anticipos recibidos: cartera frente a anticipo</h3><div class="small muted">Los anticipos se contabilizan en cuentas 28* (pasivo) y no se cruzan solos con las facturas de la cuenta 1305. Si el anticipo es mayor que la cartera, el saldo es a favor del cliente.</div>'+
    '<div class="ed"><table><thead><tr><th>Cliente</th><th>Cartera (1305)</th><th>Anticipos recibidos (28*)</th><th>Saldo neto</th><th>Resultado</th></tr></thead><tbody>'+
    cva.map(r => {const n=r[1].c-r[1].a; return '<tr><td title="'+esc(r[0])+'">'+esc(r[0])+'</td><td>'+cop(r[1].c)+'</td><td>'+cop(r[1].a)+'</td><td class="'+(n<0?'down':'')+'">'+cop(Math.abs(n))+'</td><td>'+(n<0?'Saldo a favor del cliente':'Nos debe')+'</td></tr>';}).join('')+
    '</tbody></table></div>';
  const net=[...P.T.entries()].filter(r => Math.abs(r[1])>1000);
  const pos=net.filter(r => r[1]>0).sort((a,b)=>b[1]-a[1]).slice(0,10), neg=net.filter(r => r[1]<0).sort((a,b)=>a[1]-b[1]).slice(0,10);
  const mini=(L,sg) => '<div class="ed"><table><thead><tr><th>Tercero</th><th>Saldo neto</th></tr></thead><tbody>'+(L.length? L.map(r => '<tr><td title="'+esc(r[0])+'">'+esc(r[0])+'</td><td>'+cop(r[1]*sg)+'</td></tr>').join('') : '<tr><td colspan="2" class="muted">—</td></tr>')+'</tbody></table></div>';
  let h='<h2 class="h2s">¿Quién nos debe?</h2><div class="small muted">Saldo a fin de '+ML[P.k]+', agrupado por concepto contable. Cada concepto suma los saldos netos de sus cuentas; haga clic en un concepto para ver el detalle por tercero (o por cuenta en impuestos y empresas vinculadas). Total · <b>'+cop(A1.tot)+'</b></div>'+A1.h+
    '<div class="dsec" id="ab_cxc_deb"></div>'+
    '<h2 class="h2s">¿A quién le debemos?</h2><div class="small muted">Mismo criterio. Los valores en rojo son saldos contrarios (por ejemplo, pagos o anticipos sin aplicar). Total · <b>'+cop(A2.tot)+'</b></div>'+A2.h+
    '<div class="dsec" id="ab_cxp_deb"></div>'+
    cvH+'<h2 class="h2s">Posición neta por tercero</h2><div class="small muted">Suma de todo lo que el tercero nos debe menos lo que le debemos (sin impuestos). Un mismo tercero puede ser a la vez cliente y proveedor o tener anticipos.</div>'+
    '<div class="grid2"><div><h3>Nos deben (neto)</h3>'+mini(pos,1)+'</div><div><h3>Les debemos (neto)</h3>'+mini(neg,-1)+'</div></div>';
  el.innerHTML=h;
  el.querySelectorAll('tr.pc').forEach(tr => tr.onclick = () => {const k=tr.dataset.k; if(EXPC.has(k)) EXPC.delete(k); else EXPC.add(k); renderPos();});
  renderAb('cxc','ab_cxc_deb'); renderAb('cxp','ab_cxp_deb');
}
function renderAb(lado,id){
  const el=document.getElementById(id||('ab_'+lado)); if(!el) return;
  const hoy=D.adm.hoy, m=abMap(lado), rows=[...m.values()].sort((a,b)=>b.ab-a.ab);
  const T=f=>rows.reduce((a,r)=>a+r[f],0), ab=T('ab'), venc=T('d30')+T('d60')+T('d90')+T('d91');
  const tit = lado==='cxc' ? 'Cartera de clientes: quién nos debe y cuántos días de mora' : 'Cuentas por pagar: a quién se le debe y cuántos días de mora';
  let h='<h3>'+tit+'</h3><div class="small muted">Saldos abiertos a hoy ('+hoy+'). Abierto '+cop(ab)+' · vencido '+cop(venc)+' ('+(ab? Math.round(venc/ab*100):0)+'%) · mora promedio ponderada '+(T('base')? nf.format(Math.round(T('pond')/T('base')))+' d':'—')+
    ' · pagos/anticipos sin conciliar aplicados a las facturas más antiguas: '+cop(T('apl'))+(T('ant')? ' · anticipos recibidos de clientes (cuentas 28*) aplicados a sus facturas: '+cop(T('ant')) : '')+(T('antsob')? ' · anticipo que sobra a favor del cliente: '+cop(T('antsob')) : '')+(T('fav')? ' · saldo a favor del tercero que queda: '+cop(T('fav')) : '')+'. Haga clic en un tercero para ver sus facturas.</div>';
  if(!rows.length){el.innerHTML=h+'<div class="muted small">Sin saldos abiertos.</div>'; return;}
  h+='<div class="ed"><table><thead><tr><th>Tercero</th><th>Abierto</th><th>Vencido</th><th>Mora prom.</th><th>No vencido</th><th>1–30 d</th><th>31–60 d</th><th>61–90 d</th><th>+90 d</th><th>Fact.</th></tr></thead><tbody>';
  rows.slice(0,40).forEach(r => {
    const key=lado+'|'+r.t, open=EXP.has(key), v=r.d30+r.d60+r.d90+r.d91;
    h+='<tr class="ab-r'+(open?' sel':'')+'" data-k="'+esc(key)+'" style="cursor:'+(r.docs.length?'pointer':'default')+'"><td title="'+esc(r.t)+'">'+(r.docs.length?(open?'▾ ':'▸ '):'')+esc(r.t)+'</td><td>'+cop(r.ab)+'</td><td>'+(v? cop(v):'—')+'</td><td>'+(r.base? nf.format(Math.round(r.pond/r.base))+' d':'—')+'</td>'+
       ['nv','d30','d60','d90','d91'].map(c => '<td>'+(r[c]? cop(r[c]):'—')+'</td>').join('')+'<td>'+r.n+'</td></tr>';
    if(open){
      const docs=r.docs.slice().sort((a,b)=>a[2]<b[2]?-1:1);
      h+='<tr><td colspan="10" style="padding:0"><table class="sub"><thead><tr><th>Documento</th><th>Fecha</th><th>Vence</th><th>Saldo</th><th>Días de mora</th></tr></thead><tbody>'+
         docs.map(d => {const dm=diasEntre(hoy,d[2]); return '<tr><td>'+esc(d[0])+'</td><td>'+d[1]+'</td><td>'+d[2]+'</td><td>'+cop(d[3])+'</td><td class="'+(dm>0?'down':'muted')+'">'+(dm>0? dm+' d':'al día')+'</td></tr>';}).join('')+
         '</tbody></table>'+(r.n>docs.length?'<div class="muted small" style="padding:4px 8px">Se muestran las '+docs.length+' facturas que vencen primero de '+r.n+'.</div>':'')+'</td></tr>';
    }
  });
  h+='</tbody><tfoot><tr><td>Total</td><td>'+cop(ab)+'</td><td>'+cop(venc)+'</td><td>'+(T('base')? nf.format(Math.round(T('pond')/T('base')))+' d':'—')+'</td>'+
     ['nv','d30','d60','d90','d91'].map(c => '<td>'+cop(T(c))+'</td>').join('')+'<td>'+T('n')+'</td></tr></tfoot></table></div>'+(rows.length>40?'<div class="muted small">+ '+(rows.length-40)+' terceros más (ver archivo de diagnóstico)</div>':'');
  el.innerHTML=h;
  el.querySelectorAll('.ab-r').forEach(tr => tr.onclick = () => {const k=tr.dataset.k; if(EXP.has(k)) EXP.delete(k); else EXP.add(k); renderAb(lado,id);});
}

// ---- gráfico de la serie mensual del indicador ----
function serieChart(i){
  const A=D.adm, n=A.ultimo, W=960, H=250, ml=62, mr=24, mt=26, mb=32, iw=W-ml-mr, ih=H-mt-mb;
  const vals=[], cols=[];
  for(let j=0;j<n;j++){const w=admVal(j); vals.push(w[i]); cols.push(admColor(i,w[i],w.patneg));}
  const meta=AMETA[i], fin=vals.filter(v => v!==null && v!==undefined && isFinite(v));
  let mx=Math.max(meta*1.3, ...fin, 1e-9), mn=Math.min(0, ...fin), clip=false;
  if(meta>0 && mx>meta*6){mx=meta*6; clip=true;}
  const x=j => ml+iw/12*(j+.5), y=v => mt+ih-(Math.min(v,mx)-mn)/(mx-mn)*ih;
  let g='<svg viewBox="0 0 '+W+' '+H+'" width="100%">';
  for(let t=0;t<=4;t++){const v=mn+(mx-mn)*t/4, yy=mt+ih-ih*t/4;
    g+='<line x1="'+ml+'" x2="'+(W-mr)+'" y1="'+yy+'" y2="'+yy+'" stroke="#e5e5e5"/><text x="'+(ml-8)+'" y="'+(yy+4)+'" text-anchor="end" font-size="11" fill="#666">'+admFmt(i,v,false).replace(' d','')+'</text>';}
  const ym=y(meta);
  g+='<line x1="'+ml+'" x2="'+(W-mr)+'" y1="'+ym+'" y2="'+ym+'" stroke="#e08e45" stroke-width="2" stroke-dasharray="6 4"/><text x="'+(W-mr)+'" y="'+(ym-5)+'" text-anchor="end" font-size="11" fill="#b8651b">Meta '+admFmt(i,meta,false)+'</text>';
  const pts=[]; vals.forEach((v,j) => {if(v!==null && v!==undefined && isFinite(v)) pts.push([x(j),y(v)]);});
  if(pts.length>1) g+='<polyline points="'+pts.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="#9aa5ad" stroke-width="2"/>';
  const ks=admK();
  vals.forEach((v,j) => {
    g+='<text x="'+x(j)+'" y="'+(H-mb+18)+'" text-anchor="middle" font-size="12" fill="'+(j===ks?'#b8651b':'#444')+'" font-weight="'+(j===ks?700:400)+'">'+MC[j]+'</text>';
    if(v===null||v===undefined||!isFinite(v)) return;
    const cl = v>mx;
    g+='<circle cx="'+x(j)+'" cy="'+y(v)+'" r="'+(j===ks?7:5)+'" fill="'+COL[cols[j]]+'" stroke="'+(j===ks?'#14213d':'#fff')+'" stroke-width="'+(j===ks?2.5:1.5)+'"/>'+
       '<text x="'+x(j)+'" y="'+(y(v)-10)+'" text-anchor="middle" font-size="11" fill="#333">'+(cl?'▲ ':'')+admFmt(i,v,false)+'</text>';
    g+='<rect class="ahit" data-m="'+(j+1)+'" x="'+(x(j)-iw/24)+'" y="'+mt+'" width="'+(iw/12)+'" height="'+(ih+mb)+'" fill="transparent"><title>'+ML[j]+': '+admFmt(i,v,false)+'</title></rect>';
  });
  return g+'</svg>'+(clip?'<div class="muted small">La escala se recortó en 6 veces la meta; ▲ indica valores por encima.</div>':'');
}
function compTable(i){
  const defs={
    razon:[['Pasivo total',c=>cop(c.pas)],['Activo total',c=>cop(c.act)]],
    fin:[['Obligaciones financieras',c=>cop(c.fin)],['Activo total',c=>cop(c.act)]],
    apal:[['Pasivo total',c=>cop(c.pas)],['Patrimonio',c=>cop(c.pat)]],
    rot:[['Ventas a crédito acum.',c=>cop(c.vcY)],['CxC promedio',c=>cop(c.cxcProm)],['Días del año',c=>c.dias]],
    cobro:[['Ventas a crédito acum.',c=>cop(c.vcY)],['CxC promedio',c=>cop(c.cxcProm)],['Días del año',c=>c.dias]],
    pago:[['Pagado a proveedores en el mes',c=>cop(c.pa)],['Compras acumuladas',c=>cop(c.compY)],['Saldo CxP fin de mes',c=>cop(c.cxp)]],
    ciclo:[['Días de cobro',(c,v)=>admFmt('cobro',v.cobro)],['Saldo inventario (14*)',c=>cop(c.inv)],['Costo de ventas acum.',c=>cop(c.cgY)],['Días de inventario',(c,v)=>admFmt('ciclo',v.invd)]],
    bienestar:[]};
  const cols=defs[i]; if(!cols.length) return '';
  let h='<h3>Cómo se calcula mes a mes</h3><div class="ed"><table><thead><tr><th>Mes</th>'+cols.map(c=>'<th>'+c[0]+'</th>').join('')+'<th>'+ANAMES[i].replace(' (semestral)','')+'</th></tr></thead><tbody>';
  for(let j=0;j<D.adm.ultimo;j++){const v=admVal(j); if(!v.comp) continue;
    h+='<tr data-m="'+(j+1)+'" class="'+(j===admK()?'sel':'')+'"><td>'+ML[j]+'</td>'+cols.map(c=>'<td>'+c[1](v.comp,v)+'</td>').join('')+'<td><span class="pill '+admColor(i,v[i],v.patneg)+'">'+admFmt(i,v[i],v.patneg)+'</span></td></tr>';}
  return h+'</tbody></table></div>';
}
function detalleAdm(i,k){
  const sec = (id,html) => '<div class="dsec">'+html+'</div>';
  const ph = l => '<div class="dsec" id="ab_'+l+'"></div>';
  switch(i){
    case 'razon': return sec('c',modComposicion(k,false))+sec('a',modCuentas('pas','','Pasivo por cuenta contable',25))+
      '<div class="dsec muted small">El detalle de a quién se le debe (proveedores, bancos, particulares, anticipos) está en la sección «¿A quién le debemos?» más abajo.</div>';
    case 'apal': return sec('c',modComposicion(k,true))+sec('a',modCuentas('pas','','Pasivo por cuenta contable',20))+
      '<div class="dsec muted small">El detalle por tercero está en las secciones «¿Quién nos debe?» y «¿A quién le debemos?» más abajo.</div>';
    case 'fin': return sec('c','<div class="grid2"><div>'+modCuentas('pas','21','Obligaciones financieras por cuenta',30)+'</div><div>'+modTerceros('fin','¿A quién se le debe? (por tercero)',null,20)+'</div></div>');
    case 'rot': case 'cobro': return sec('t',modTerceros('cxc','Cartera por cliente','cxc',15))+'<div class="dsec muted small">La mora por factura de cada cliente está en «¿Quién nos debe?» más abajo.</div>';
    case 'pago': return sec('t',modTerceros('prov','Cuentas por pagar por proveedor',null,15))+'<div class="dsec muted small">La mora por factura de cada proveedor está en «¿A quién le debemos?» más abajo.</div>';
    case 'ciclo': return sec('i',modCuentas('act','14','Inventarios por cuenta contable (14*)',15));
    default: return '<div class="dsec muted small">Este indicador se digita a mano: use “Editar metas” → fila de Bienestar para registrar el % cumplido de cada mes.</div>';
  }
}
function lados(i){return [];}

function renderAdm(){
  const root=document.getElementById('adm_root');
  if(!admOk()){document.getElementById('t_adm').textContent='Indicadores del área administrativa'; root.innerHTML='<div class="aviso">No hay datos contables suficientes para este año.</div>'; return;}
  const A=D.adm, ns=admNombres(), k=admK(), ent = st.emp<0 ? 'todas las empresas' : (D.empresas[st.emp]||'');
  document.getElementById('t_adm').textContent='Indicadores del área administrativa · '+ML[k]+' '+D.anio+' · '+ent;
  if(!ns.length){root.innerHTML='<div class="muted">Sin datos contables para esta empresa.</div>'; return;}
  const v=admVal(k), hj=A.hoja[String(k+1)], i=st.ind;
  let h='<div class="ind adm">';
  AK.forEach(x => {
    const val=v[x], c=admColor(x,val,v.patneg);
    const comp=(x==='ciclo' && v.invd!==null && v.cobro!==null) ? '<div class="small muted">cobro '+nf.format(Math.round(v.cobro))+' d + inventario '+nf.format(Math.round(v.invd))+' d</div>' : '';
    h+='<div class="ic clk '+c+(x===i?' selc':'')+'" data-i="'+x+'"><div class="l">'+ANAMES[x]+'</div><div class="p">'+admFmt(x,val,v.patneg)+'</div>'+
       '<div class="small"><b>Meta</b> '+(A.tipo[x]==='min'?'≥ ':'≤ ')+admFmt(x,AMETA[x],false)+'</div><div class="small muted">'+ADESC[x]+'</div>'+comp+
       (hj && hj[x]!==undefined && st.emp>=0 && /diforma/i.test(D.empresas[st.emp]||'') ? '<div class="small muted">Según la hoja: '+admFmt(x,hj[x],false)+'</div>' : '')+'</div>';
  });
  h+='</div><div class="small muted" style="margin:-4px 0 8px">Haga clic en un indicador para ver su detalle.</div>';
  const hjI = hj && hj[i]!==undefined && st.emp>=0 && /diforma/i.test(D.empresas[st.emp]||'') ? ' · según la hoja: '+admFmt(i,hj[i],false) : '';
  h+='<div class="dp"><h2 class="h2s">'+ANAMES[i]+' · detalle de '+ML[k]+' '+D.anio+'</h2><div class="small muted">'+AFORM[i]+'<br>Meta '+(A.tipo[i]==='min'?'≥ ':'≤ ')+admFmt(i,AMETA[i],false)+' · valor '+admFmt(i,v[i],v.patneg)+hjI+'</div>'+
     (i==='bienestar' ? '' : '<div id="adm_chart" style="margin-top:8px">'+serieChart(i)+'</div>')+compTable(i)+detalleAdm(i,k)+'</div>';
  h+='<div class="dp" id="pos_root"></div>';
  h+='<h2>Resumen de los 8 indicadores por mes</h2>';
  let t='<table><thead><tr><th>Mes</th>'+AK.map(x=>'<th title="'+ADESC[x]+'">'+ANAMES[x].replace(' (semestral)','')+'</th>').join('')+'</tr></thead><tbody>';
  for(let j=0;j<12;j++){
    if(j>=A.ultimo){t+='<tr><td>'+ML[j]+'</td>'+AK.map(()=>'<td><span class="pill n">—</span></td>').join('')+'</tr>'; continue;}
    const w=admVal(j);
    t+='<tr data-m="'+(j+1)+'" class="'+(j===k?'sel':'')+'"><td>'+ML[j]+'</td>'+AK.map(x => '<td><span class="pill '+admColor(x,w[x],w.patneg)+'">'+admFmt(x,w[x],w.patneg)+'</span></td>').join('')+'</tr>';
  }
  h+=t+'</tbody></table>';
  h+='<div class="small muted" style="margin-top:14px">Verde: cumple la meta; amarillo: hasta '+Math.round((A.amarillo_max-1)*100)+'% por encima de la meta (o al menos '+Math.round(A.amarillo_min*100)+'% en indicadores “mayor es mejor”); rojo: peor. '+
    'Cifras en COP de la contabilidad (asientos contabilizados; saldos acumulados a fin de mes), por empresa o consolidadas según el selector de arriba. '+
    'Los saldos abiertos por tercero y su mora corresponden a la fecha de generación ('+A.hoy+'), no al mes elegido. Las metas se pueden editar con “Editar metas”; los valores iniciales vienen de metas_admin.json (hoja de indicadores).</div>';
  root.innerHTML=h;
  root.querySelectorAll('.ic[data-i]').forEach(c => c.onclick = () => {st.ind=c.dataset.i; renderAdm();});
  root.querySelectorAll('tr[data-m]').forEach(r => r.onclick = () => {st.mes=+r.dataset.m; render();});
  root.querySelectorAll('.ahit').forEach(r => r.onclick = () => {st.mes=+r.dataset.m; render();});
  lados(i).forEach(l => renderAb(l));
  renderPos();
}
function initAdm(){
  AKEY='diforma_metas_admin_'+D.anio;
  if(!admOk()) return;
  AMETA=Object.assign({},D.adm.meta); ADEF=Object.assign({},D.adm.meta);
  ABIEN=D.adm.manual_bienestar.slice(); ABDEF=ABIEN.slice();
  try{const sv=JSON.parse(localStorage.getItem(AKEY)||'null');
    if(sv){AK.forEach(i => {if(sv.meta && typeof sv.meta[i]==='number' && sv.meta[i]>=0) AMETA[i]=sv.meta[i];});
      if(Array.isArray(sv.bien)) sv.bien.forEach((x,j) => {if(j<12 && (x===null||typeof x==='number')) ABIEN[j]=x;});}}catch(e){}
  document.getElementById('adm_edit').style.display='none';
}
const admCambios = () => AK.some(i => AMETA[i]!==ADEF[i]) || ABIEN.some((x,j) => x!==ABDEF[j]);
function admEst(){document.getElementById('adm_est').textContent = !admOk()? '' : (admCambios() ? 'Metas/datos modificados a mano (guardados en este navegador).' : 'Metas de la hoja de indicadores.');}
function admSave(){try{const v=JSON.stringify({meta:AMETA, bien:ABIEN}); localStorage.setItem(AKEY, v); nubeGuardar(AKEY,v);}catch(e){}}
function renderAdmEdit(){
  const el=document.getElementById('adm_edit');
  let h='<div class="ed"><div class="small muted" style="margin-bottom:6px">Escriba la meta y el dashboard se actualiza al instante. Los cambios se guardan en este navegador; para dejarlos permanentes edite metas_admin.json. Las celdas amarillas difieren de la hoja.</div><table><thead><tr><th>Indicador</th><th>Meta</th><th>Criterio</th></tr></thead><tbody>';
  AK.forEach(i => {h+='<tr><td>'+ANAMES[i]+'</td><td><input type="number" step="any" min="0" data-m="'+i+'" value="'+AMETA[i]+'" class="'+(AMETA[i]!==ADEF[i]?'mod':'')+'"></td><td class="small muted">'+(D.adm.tipo[i]==='min'?'cumple si es mayor o igual':'cumple si es menor o igual')+'</td></tr>';});
  h+='</tbody></table><div class="small muted" style="margin:10px 0 4px">Bienestar (% de actividades cumplidas en el semestre, dato manual por mes)</div><table><thead><tr>'+MC.map(x=>'<th>'+x+'</th>').join('')+'</tr></thead><tbody><tr>'+
     ABIEN.map((x,j) => '<td><input type="number" step="any" min="0" data-b="'+j+'" value="'+(x===null?'':x)+'" class="'+(x!==ABDEF[j]?'mod':'')+'" style="width:56px"></td>').join('')+'</tr></tbody></table>'+
     '<div style="margin-top:8px"><button class="chip" id="adm_reset">Restablecer valores de la hoja</button></div></div>';
  el.innerHTML=h;
  const refresh=()=>{admSave(); admEst(); renderAdm();};
  el.querySelectorAll('input[data-m]').forEach(inp => inp.oninput = () => {
    const v=parseFloat(inp.value); if(isNaN(v)||v<0) return; AMETA[inp.dataset.m]=v; inp.classList.toggle('mod', v!==ADEF[inp.dataset.m]); refresh();});
  el.querySelectorAll('input[data-b]').forEach(inp => inp.oninput = () => {
    const j=+inp.dataset.b, v=inp.value===''?null:parseFloat(inp.value); if(v!==null&&(isNaN(v)||v<0)) return; ABIEN[j]=v; inp.classList.toggle('mod', v!==ABDEF[j]); refresh();});
  document.getElementById('adm_reset').onclick = () => {AMETA=Object.assign({},ADEF); ABIEN=ABDEF.slice(); refresh(); renderAdmEdit();};
}

// ================= ÁREA DISEÑO =================
const DK=['efectividad','eficiencia','devueltos','destacados','ia'];
const DNAMES={efectividad:'Efectividad',eficiencia:'Eficiencia',devueltos:'Proyectos devueltos',destacados:'Proyectos destacados',ia:'Proyectos IA'};
const DDESC={efectividad:'Diseños vendidos / presentados',eficiencia:'Proyectos con novedades / presentados',devueltos:'Diseños devueltos / presentados',
  destacados:'Diseños destacados / presentados',ia:'Dato manual (no sale de Odoo)'};
const DFORM={
  efectividad:'Diseños vendidos ÷ diseños presentados × 100. Presentado = brief con “Fecha entrega real Diseño” en el mes; vendido = brief que pasa a la etapa Ganado en el mes. Mayor es mejor.',
  eficiencia:'Proyectos presentados en el mes que tienen al menos una novedad (letra distinta de A: 17573-B, -C…) ÷ proyectos presentados en el mes × 100. Un proyecto sin novedades se logró al primer intento; con varias novedades, el diseño no se aproximó rápido a la expectativa del cliente. Menor es mejor. Nota: la hoja la define como promedio de novedades por proyecto y sus valores no se pudieron reproducir desde Odoo.',
  devueltos:'Veces que un brief vuelve a la etapa Diseño desde una etapa posterior (Presentación, Presupuesto, Cotización o Ganado) ÷ diseños presentados × 100. Menor es mejor.',
  destacados:'Diseños presentados en el mes marcados como “Diseño destacado” ÷ diseños presentados × 100. Mayor es mejor.',
  ia:'Diseños desarrollados con IA ÷ diseños presentados. No sale de Odoo (no hay etiqueta ni campo): se digita en “Editar metas”. Mayor es mejor.'};
let DMETA=null, DDEF=null, DIA=null, DIADEF=null, DKEY='';
st.dind='efectividad';

const disOk = () => !!(D.dis && D.dis.ok);
function disNombres(){return st.emp<0 ? D.dis.empresas : [D.empresas[st.emp]].filter(n => D.dis.m[n]);}
const disK = () => Math.min(st.mes>0 ? st.mes : D.dis.ultimo, D.dis.ultimo)-1;
function disRango(){const k=disK(); return st.mes>0 ? [k,k] : [Math.max(D.dis.desde-1,0), D.dis.ultimo-1];}
function disSum(a,b,c){
  let s=0; disNombres().forEach(n => {const arr=D.dis.m[n][c]; for(let j=a;j<=b;j++) s+=(arr[j]||0);}); return s;
}
function disVal(a,b){
  const c={pres:disSum(a,b,'pres'),vend:disSum(a,b,'vend'),dev:disSum(a,b,'dev'),dest:disSum(a,b,'dest'),py:disSum(a,b,'py'),pyn:disSum(a,b,'pyn')};
  const r=(x,y)=> y>0 ? x/y*100 : null;
  let ia=null;
  if(a===b) ia=(DIA[a]===undefined ? null : DIA[a]);
  else {const w=DIA.slice(a,b+1).filter(x => x!==null && x!==undefined); ia=w.length ? w.reduce((p,q)=>p+q,0)/w.length : null;}
  return {efectividad:r(c.vend,c.pres), eficiencia:r(c.pyn,c.py), devueltos:r(c.dev,c.pres), destacados:r(c.dest,c.pres), ia:ia, comp:c};
}
function disColor(i,v){
  if(v===null||v===undefined||isNaN(v)) return 'n';
  const m=DMETA[i], A=D.dis;
  if(A.tipo[i]==='min') return v>=m ? 'g' : (v>=m*A.amarillo_min ? 'y' : 'r');
  return v<=m ? 'g' : (v<=m*A.amarillo_max ? 'y' : 'r');
}
const disFmt = (i,v) => (v===null||v===undefined||isNaN(v)) ? '—' : nf.format(Math.round(v))+'%';
const disEsDiforma = () => st.emp>=0 && /diforma/i.test(D.empresas[st.emp]||'');

function disChart(i){
  const A=D.dis, n=A.ultimo, W=960, H=250, ml=62, mr=24, mt=26, mb=32, iw=W-ml-mr, ih=H-mt-mb;
  const vals=[], cols=[];
  for(let j=0;j<n;j++){const w=disVal(j,j)[i]; vals.push(w); cols.push(disColor(i,w));}
  const meta=DMETA[i], fin=vals.filter(v => v!==null && v!==undefined && isFinite(v));
  const mx=Math.max(meta*1.3, ...fin, 10), mn=0;
  const x=j => ml+iw/12*(j+.5), y=v => mt+ih-(v-mn)/(mx-mn)*ih;
  let g='<svg viewBox="0 0 '+W+' '+H+'" width="100%">';
  for(let t=0;t<=4;t++){const v=mn+(mx-mn)*t/4, yy=mt+ih-ih*t/4;
    g+='<line x1="'+ml+'" x2="'+(W-mr)+'" y1="'+yy+'" y2="'+yy+'" stroke="#e5e5e5"/><text x="'+(ml-8)+'" y="'+(yy+4)+'" text-anchor="end" font-size="11" fill="#666">'+nf.format(Math.round(v))+'%</text>';}
  const ym=y(meta);
  g+='<line x1="'+ml+'" x2="'+(W-mr)+'" y1="'+ym+'" y2="'+ym+'" stroke="#e08e45" stroke-width="2" stroke-dasharray="6 4"/><text x="'+(W-mr)+'" y="'+(ym-5)+'" text-anchor="end" font-size="11" fill="#b8651b">Meta '+(A.tipo[i]==='min'?'≥ ':'≤ ')+disFmt(i,meta)+'</text>';
  const pts=[]; vals.forEach((v,j) => {if(v!==null && v!==undefined && isFinite(v)) pts.push([x(j),y(v)]);});
  if(pts.length>1) g+='<polyline points="'+pts.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="#9aa5ad" stroke-width="2"/>';
  const ks=disK();
  for(let j=0;j<12;j++){
    g+='<text x="'+x(j)+'" y="'+(H-mb+18)+'" text-anchor="middle" font-size="12" fill="'+(j===ks&&st.mes>0?'#b8651b':'#444')+'" font-weight="'+(j===ks&&st.mes>0?700:400)+'">'+MC[j]+'</text>';
    const v=vals[j]; if(v===null||v===undefined||!isFinite(v)) continue;
    g+='<circle cx="'+x(j)+'" cy="'+y(v)+'" r="'+(j===ks&&st.mes>0?7:5)+'" fill="'+COL[cols[j]]+'" stroke="'+(j===ks&&st.mes>0?'#14213d':'#fff')+'" stroke-width="'+(j===ks&&st.mes>0?2.5:1.5)+'"/>'+
       '<text x="'+x(j)+'" y="'+(y(v)-10)+'" text-anchor="middle" font-size="11" fill="#333">'+disFmt(i,v)+'</text>'+
       '<rect class="dhit" data-m="'+(j+1)+'" x="'+(x(j)-iw/24)+'" y="'+mt+'" width="'+(iw/12)+'" height="'+(ih+mb)+'" fill="transparent"><title>'+ML[j]+': '+disFmt(i,v)+'</title></rect>';
  }
  return g+'</svg>';
}
function disCompTable(i){
  const defs={
    efectividad:[['Presentados',c=>nf.format(c.pres)],['Vendidos',c=>nf.format(c.vend)]],
    eficiencia:[['Proyectos presentados',c=>nf.format(c.py)],['Con novedades',c=>nf.format(c.pyn)]],
    devueltos:[['Presentados',c=>nf.format(c.pres)],['Devoluciones',c=>nf.format(c.dev)]],
    destacados:[['Presentados',c=>nf.format(c.pres)],['Destacados',c=>nf.format(c.dest)]],
    ia:[]};
  const cols=defs[i]; if(!cols.length) return '';
  let h='<h3>Cómo se calcula mes a mes</h3><div class="ed"><table><thead><tr><th>Mes</th>'+cols.map(c=>'<th>'+c[0]+'</th>').join('')+'<th>'+DNAMES[i]+'</th></tr></thead><tbody>';
  for(let j=Math.max(D.dis.desde-1,0); j<D.dis.ultimo; j++){const v=disVal(j,j);
    h+='<tr data-m="'+(j+1)+'" class="'+(st.mes>0&&j===disK()?'sel':'')+'"><td>'+ML[j]+'</td>'+cols.map(c=>'<td>'+c[1](v.comp)+'</td>').join('')+'<td><span class="pill '+disColor(i,v[i])+'">'+disFmt(i,v[i])+'</span></td></tr>';}
  return h+'</tbody></table></div>';
}
function dTbl(heads,rows){
  if(!rows.length) return '<div class="muted small">Sin registros.</div>';
  return '<div class="ed"><table><thead><tr>'+heads.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody>'+
    rows.map(r => '<tr>'+r.map((c,j)=>'<td'+(j===0?' style="text-align:left"':'')+'>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
}
function disDet(k){
  const o={pres:[],vend:[],dev:[],nov:[]};
  disNombres().forEach(n => {const d=((D.dis.det[n]||{})[k])||{}; ['pres','vend','dev','nov'].forEach(f => {o[f]=o[f].concat(d[f]||[]);});});
  return o;
}
function disDisenadores(a,b){
  const m=new Map();
  disNombres().forEach(n => {const d=D.dis.disen[n]||{};
    Object.keys(d).forEach(nm => {let r=m.get(nm); if(!r){r={pres:0,vend:0,dev:0}; m.set(nm,r);}
      ['pres','vend','dev'].forEach(f => {for(let j=a;j<=b;j++) r[f]+=(d[nm][f][j]||0);});});});
  return [...m.entries()].filter(e => e[1].pres+e[1].vend+e[1].dev>0).sort((p,q) => q[1].pres-p[1].pres);
}
function disDetalle(i,k,rg){
  const d=disDet(k), mesTxt=ML[k]+' '+D.anio, nota = st.mes>0 ? '' : ' <span class="muted small">(último mes con datos; elija un mes arriba para ver otro)</span>';
  const lim = '<div class="muted small">Se muestran hasta 60 registros por empresa y mes.</div>';
  let h='';
  if(i!=='ia'){
    const dz=disDisenadores(rg[0],rg[1]);
    h+='<div class="dsec"><h3>Por diseñador · '+(st.mes>0? mesTxt : 'acumulado del año')+'</h3>'+
      dTbl(['Diseñador','Presentados','Vendidos','Devoluciones'], dz.map(e => [esc(e[0]),nf.format(e[1].pres),nf.format(e[1].vend),nf.format(e[1].dev)]))+'</div>';
  }
  if(i==='efectividad'){
    h+='<div class="dsec"><h3>Vendidos en '+mesTxt+nota+'</h3>'+dTbl(['Brief','Cliente','Diseñador','Fecha'], d.vend.map(r => [esc(r[0]),esc(r[1]),esc(r[2]),esc(r[3])]))+'</div>'+
       '<div class="dsec"><h3>Presentados en '+mesTxt+'</h3>'+dTbl(['Brief','Cliente','Diseñador','Etapa actual'], d.pres.map(r => [esc(r[0]),esc(r[1]),esc(r[2]),esc(r[3])]))+lim+'</div>';
  } else if(i==='eficiencia'){
    h+='<div class="dsec"><h3>Proyectos presentados en '+mesTxt+' con novedades'+nota+'</h3>'+dTbl(['Proyecto (n.º)','Letras presentadas','Cliente'], d.nov.map(r => [esc(r[0]),esc(r[1]),esc(r[2])]))+lim+'</div>';
  } else if(i==='devueltos'){
    h+='<div class="dsec"><h3>Devoluciones en '+mesTxt+nota+'</h3>'+dTbl(['Brief','Venía de','Vuelve a','Fecha'], d.dev.map(r => [esc(r[0]),esc(r[1]),esc(r[2]),esc(r[3])]))+'</div>';
  } else if(i==='destacados'){
    h+='<div class="dsec"><h3>Diseños destacados presentados en '+mesTxt+nota+'</h3>'+dTbl(['Brief','Cliente','Diseñador'], d.pres.filter(r => r[4]===1).map(r => [esc(r[0]),esc(r[1]),esc(r[2])]))+lim+'</div>';
  } else {
    h+='<div class="dsec muted small">Este indicador se digita a mano: use “Editar metas” → fila de Proyectos IA para registrar el % de cada mes.</div>';
  }
  return h;
}

function renderDis(){
  const root=document.getElementById('dis_root');
  if(!disOk()){document.getElementById('t_dis').textContent='Indicadores del área de Diseño'; root.innerHTML='<div class="aviso">No hay datos del módulo Diforma Brief suficientes para este año.</div>'; return;}
  const A=D.dis, ns=disNombres(), k=disK(), rg=disRango(), ent = st.emp<0 ? 'todas las empresas' : (D.empresas[st.emp]||'');
  const per = st.mes>0 ? ML[k]+' '+D.anio : 'acumulado '+MC[rg[0]]+'–'+MC[rg[1]]+' '+D.anio;
  document.getElementById('t_dis').textContent='Indicadores del área de Diseño · '+per+' · '+ent;
  if(!ns.length){root.innerHTML='<div class="muted">Sin datos de Diseño para esta empresa.</div>'; return;}
  const v=disVal(rg[0],rg[1]), c=v.comp, hj=A.hoja[String(k+1)], i=st.dind;
  const base = {efectividad:[c.vend,c.pres,'vendidos de presentados'], eficiencia:[c.pyn,c.py,'proyectos con novedades de presentados'],
    devueltos:[c.dev,c.pres,'devoluciones de presentados'], destacados:[c.dest,c.pres,'destacados de presentados']};
  const refHoja = x => (st.mes>0 && hj && hj[x]!==undefined && disEsDiforma()) ? '<div class="small muted">Según la hoja: '+hj[x]+(hj[x]>100 ? '' : '%')+'</div>' : '';
  let h='<div class="ind dis">';
  DK.forEach(x => {
    const val=v[x], cl=disColor(x,val), b=base[x];
    h+='<div class="ic clk '+cl+(x===i?' selc':'')+'" data-i="'+x+'"><div class="l">'+DNAMES[x]+'</div><div class="p">'+disFmt(x,val)+'</div>'+
       '<div class="small"><b>Meta</b> '+(A.tipo[x]==='min'?'≥ ':'≤ ')+disFmt(x,DMETA[x])+'</div>'+
       '<div class="small muted">'+(b? nf.format(b[0])+' '+b[2] : DDESC[x])+'</div>'+refHoja(x)+'</div>';
  });
  h+='</div><div class="small muted" style="margin:-4px 0 8px">Haga clic en un indicador para ver su detalle.</div>';
  const hjI = (st.mes>0 && hj && hj[i]!==undefined && disEsDiforma()) ? ' · según la hoja: '+hj[i]+(hj[i]>100?'':'%') : '';
  h+='<div class="dp"><h2 class="h2s">'+DNAMES[i]+' · detalle de '+(st.mes>0? ML[k]+' '+D.anio : 'todo el año')+'</h2><div class="small muted">'+DFORM[i]+'<br>Meta '+(A.tipo[i]==='min'?'≥ ':'≤ ')+disFmt(i,DMETA[i])+' · valor '+disFmt(i,v[i])+hjI+'</div>'+
     '<div style="margin-top:8px">'+disChart(i)+'</div>'+disCompTable(i)+disDetalle(i,k,rg)+'</div>';
  h+='<h2>Resumen de los 5 indicadores por mes</h2>';
  let t='<table><thead><tr><th>Mes</th>'+DK.map(x=>'<th title="'+DDESC[x]+'">'+DNAMES[x]+'</th>').join('')+'</tr></thead><tbody>';
  for(let j=0;j<12;j++){
    if(j>=A.ultimo || j<A.desde-1){t+='<tr><td>'+ML[j]+'</td>'+DK.map(()=>'<td><span class="pill n">—</span></td>').join('')+'</tr>'; continue;}
    const w=disVal(j,j), hh=A.hoja[String(j+1)];
    t+='<tr data-m="'+(j+1)+'" class="'+(st.mes>0&&j===k?'sel':'')+'"><td>'+ML[j]+'</td>'+DK.map(x => '<td><span class="pill '+disColor(x,w[x])+'">'+disFmt(x,w[x])+'</span>'+
       ((hh && hh[x]!==undefined && disEsDiforma()) ? ' <span class="muted small">(hoja '+hh[x]+')</span>' : '')+'</td>').join('')+'</tr>';
  }
  h+=t+'</tbody></table>';
  h+='<div class="small muted" style="margin-top:14px">Verde: cumple la meta; amarillo: hasta '+Math.round((A.amarillo_max-1)*100)+'% por encima de la meta (o al menos '+Math.round(A.amarillo_min*100)+'% en indicadores “mayor es mejor”); rojo: peor. '+
    'Fuente: módulo Diforma Brief de Odoo (historial de etapas, fecha de entrega real de diseño, letra del proyecto y campo “Diseño destacado”). Presentado = brief con fecha de entrega real de diseño en el mes. '+
    'Enero de 2026 no se muestra: es la carga inicial de datos y no tiene historial de etapas. En “Año completo” se acumulan los conteos de los meses (un mismo proyecto puede contarse en más de un mes). '+
    'Las metas se pueden editar con “Editar metas”; los valores iniciales vienen de metas_diseno.json (hoja de indicadores).</div>';
  root.innerHTML=h;
  root.querySelectorAll('.ic[data-i]').forEach(e => e.onclick = () => {st.dind=e.dataset.i; renderDis();});
  root.querySelectorAll('tr[data-m]').forEach(r => r.onclick = () => {st.mes=+r.dataset.m; render();});
  root.querySelectorAll('.dhit').forEach(r => r.onclick = () => {st.mes=+r.dataset.m; render();});
}
function initDis(){
  DKEY='diforma_metas_dis_'+D.anio;
  document.getElementById('dis_edit').style.display='none';
  if(!disOk()) return;
  DMETA=Object.assign({},D.dis.meta); DDEF=Object.assign({},D.dis.meta);
  DIA=D.dis.manual_ia.slice(); DIADEF=DIA.slice();
  try{const sv=JSON.parse(localStorage.getItem(DKEY)||'null');
    if(sv){DK.forEach(i => {if(sv.meta && typeof sv.meta[i]==='number' && sv.meta[i]>=0) DMETA[i]=sv.meta[i];});
      if(Array.isArray(sv.ia)) sv.ia.forEach((x,j) => {if(j<12 && (x===null||typeof x==='number')) DIA[j]=x;});}}catch(e){}
}
const disCambios = () => DK.some(i => DMETA[i]!==DDEF[i]) || DIA.some((x,j) => x!==DIADEF[j]);
function disEst(){document.getElementById('dis_est').textContent = !disOk()? '' : (disCambios() ? 'Metas/datos modificados a mano (guardados en este navegador).' : 'Metas de la hoja de indicadores.');}
function disSave(){try{const v=JSON.stringify({meta:DMETA, ia:DIA}); localStorage.setItem(DKEY, v); nubeGuardar(DKEY,v);}catch(e){}}
function renderDisEdit(){
  const el=document.getElementById('dis_edit');
  let h='<div class="ed"><div class="small muted" style="margin-bottom:6px">Escriba la meta (en %) y el dashboard se actualiza al instante. Los cambios se guardan en este navegador; para dejarlos permanentes edite metas_diseno.json. Las celdas amarillas difieren de la hoja.</div><table><thead><tr><th>Indicador</th><th>Meta (%)</th><th>Criterio</th></tr></thead><tbody>';
  DK.forEach(i => {h+='<tr><td>'+DNAMES[i]+'</td><td><input type="number" step="any" min="0" data-m="'+i+'" value="'+DMETA[i]+'" class="'+(DMETA[i]!==DDEF[i]?'mod':'')+'"></td><td class="small muted">'+(D.dis.tipo[i]==='min'?'cumple si es mayor o igual':'cumple si es menor o igual')+'</td></tr>';});
  h+='</tbody></table><div class="small muted" style="margin:10px 0 4px">Proyectos IA (% de diseños desarrollados con IA, dato manual por mes)</div><table><thead><tr>'+MC.map(x=>'<th>'+x+'</th>').join('')+'</tr></thead><tbody><tr>'+
     DIA.map((x,j) => '<td><input type="number" step="any" min="0" data-b="'+j+'" value="'+(x===null||x===undefined?'':x)+'" class="'+(x!==DIADEF[j]?'mod':'')+'" style="width:56px"></td>').join('')+'</tr></tbody></table>'+
     '<div style="margin-top:8px"><button class="chip" id="dis_reset">Restablecer valores de la hoja</button></div></div>';
  el.innerHTML=h;
  const refresh=()=>{disSave(); disEst(); renderDis();};
  el.querySelectorAll('input[data-m]').forEach(inp => inp.oninput = () => {
    const v=parseFloat(inp.value); if(isNaN(v)||v<0) return; DMETA[inp.dataset.m]=v; inp.classList.toggle('mod', v!==DDEF[inp.dataset.m]); refresh();});
  el.querySelectorAll('input[data-b]').forEach(inp => inp.oninput = () => {
    const j=+inp.dataset.b, v=inp.value===''?null:parseFloat(inp.value); if(v!==null&&(isNaN(v)||v<0)) return; DIA[j]=v; inp.classList.toggle('mod', v!==DIADEF[j]); refresh();});
  document.getElementById('dis_reset').onclick = () => {DMETA=Object.assign({},DDEF); DIA=DIADEF.slice(); refresh(); renderDisEdit();};
}

// ================= ÁREA PRODUCCIÓN =================
const PK=['garantias','proveedores','eficiencia','costo_garantias'];
const PNAMES={garantias:'Cero garantías',proveedores:'Proveedor de servicios',eficiencia:'Eficiencia en el costo',costo_garantias:'Costo de garantías'};
const PDESC={garantias:'(Ejecutados − garantías) / ejecutados',proveedores:'Proveedores nuevos que facturan a proyectos',
  eficiencia:'(Costo proyectado − costo real) / proyectado',costo_garantias:'Costo real de los proyectos en garantía'};
const PFORM={
  garantias:'(Proyectos ejecutados − garantías) ÷ proyectos ejecutados × 100. Ejecutado = proyecto de tipo Producción o Prototipo Facturable, contado en el mes de su primera factura de cliente contabilizada. Garantía = brief aprobado de tipo Garantía, contado en el mes en que se crea. Mayor es mejor: 100% significa cero garantías.',
  proveedores:'Proveedores nuevos ÷ meta de proveedores del periodo × 100 (1 nuevo = 100%, 2 = 200%, como en la hoja). Odoo cuenta proveedores cuya primera factura de proveedor en Odoo lleva distribución analítica a proyectos, en el mes de esa factura. Si se digita el dato oficial en “Editar metas”, ese valor tiene prioridad. Mayor es mejor.',
  eficiencia:'(Costo proyectado − costo real) ÷ costo proyectado × 100 de los proyectos ejecutados en el mes. Proyectado = “Costo Proyectado” × cantidad de los briefs aprobados; real = suma neta del plan analítico del proyecto sin las categorías Venta y Servicio Exportación. Solo entran proyectos con proyectado mínimo y con costo real; los demás se listan aparte. Mayor ahorro es mejor.',
  costo_garantias:'Costo real (COP) acumulado en el plan analítico de los proyectos de tipo Garantía creados en el mes (sin Venta ni Servicio Exportación). Es informativo: no tiene meta ni semáforo.'};
let PMETA=null, PDEF=null, PMAN=null, PMANDEF=null, PKEY='';
st.pind='garantias';

const proOk = () => !!(D.pro && D.pro.ok);
const proK = () => Math.min(st.mes>0 ? st.mes : D.pro.ultimo, D.pro.ultimo)-1;
function proRango(){const k=proK(); return st.mes>0 ? [k,k] : [Math.max(D.pro.desde-1,0), D.pro.ultimo-1];}
function proSum(a,b,c){const arr=D.pro.m[c]; let s=0; for(let j=a;j<=b;j++) s+=(arr[j]||0); return s;}
function proVal(a,b){
  const c={ejec:proSum(a,b,'ejec'),gar:proSum(a,b,'gar'),nefi:proSum(a,b,'nefi'),proy:proSum(a,b,'proy'),real:proSum(a,b,'real'),gcosto:proSum(a,b,'gcosto')};
  let nuevos=0, meses=0, dig=0;
  for(let j=a;j<=b;j++){
    const man=PMAN[j];
    if(man!==null && man!==undefined){nuevos+=man; meses++; dig++;}
    else if(j+1>=D.pro.desde_prov && j+1<=D.pro.ultimo){nuevos+=(D.pro.m.prov[j]||0); meses++;}
  }
  c.prov=nuevos; c.mesesp=meses; c.dig=dig;
  const r=(x,y)=> y>0 ? x/y*100 : null;
  return {garantias:r(c.ejec-c.gar,c.ejec), proveedores: meses>0 ? nuevos/(meses*D.pro.prov_por_mes)*100 : null,
          eficiencia:r(c.proy-c.real,c.proy), costo_garantias:c.gcosto, comp:c};
}
function proColor(i,v){
  if(i==='costo_garantias' || v===null||v===undefined||isNaN(v)) return 'n';
  const m=PMETA[i], A=D.pro;
  if(A.tipo[i]==='min') return v>=m ? 'g' : (v>=m*A.amarillo_min ? 'y' : 'r');
  return v<=m ? 'g' : (v<=m*A.amarillo_max ? 'y' : 'r');
}
const proMon = v => '$ '+nf.format(Math.round(v));
const proFmt = (i,v) => (v===null||v===undefined||isNaN(v)) ? '—' : (i==='costo_garantias' ? proMon(v) : nf1.format(v)+'%');

function proChart(i){
  const A=D.pro, n=A.ultimo, W=960, H=250, ml=70, mr=24, mt=26, mb=32, iw=W-ml-mr, ih=H-mt-mb;
  const esC=(i==='costo_garantias'), vals=[], cols=[];
  const j0 = i==='proveedores' ? A.desde_prov-1 : A.desde-1;
  for(let j=0;j<n;j++){const w=(j<j0) ? null : proVal(j,j)[i]; vals.push(w); cols.push(proColor(i,w));}
  const meta=esC ? null : PMETA[i], fin=vals.filter(v => v!==null && v!==undefined && isFinite(v));
  const mx=Math.max(meta===null?0:meta*1.3, ...fin, esC?1:10), mn=Math.min(0, ...fin);
  const x=j => ml+iw/12*(j+.5), y=v => mt+ih-(v-mn)/(mx-mn)*ih;
  const tk = v => esC ? abbr(v,'cop',false) : nf.format(Math.round(v))+'%';
  let g='<svg viewBox="0 0 '+W+' '+H+'" width="100%">';
  for(let t=0;t<=4;t++){const v=mn+(mx-mn)*t/4, yy=mt+ih-ih*t/4;
    g+='<line x1="'+ml+'" x2="'+(W-mr)+'" y1="'+yy+'" y2="'+yy+'" stroke="#e5e5e5"/><text x="'+(ml-8)+'" y="'+(yy+4)+'" text-anchor="end" font-size="11" fill="#666">'+tk(v)+'</text>';}
  if(!esC){const ym=y(meta);
    g+='<line x1="'+ml+'" x2="'+(W-mr)+'" y1="'+ym+'" y2="'+ym+'" stroke="#e08e45" stroke-width="2" stroke-dasharray="6 4"/><text x="'+(W-mr)+'" y="'+(ym-5)+'" text-anchor="end" font-size="11" fill="#b8651b">Meta '+(A.tipo[i]==='min'?'≥ ':'≤ ')+proFmt(i,meta)+'</text>';}
  const pts=[]; vals.forEach((v,j) => {if(v!==null && v!==undefined && isFinite(v)) pts.push([x(j),y(v)]);});
  if(pts.length>1) g+='<polyline points="'+pts.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="#9aa5ad" stroke-width="2"/>';
  const ks=proK();
  for(let j=0;j<12;j++){
    g+='<text x="'+x(j)+'" y="'+(H-mb+18)+'" text-anchor="middle" font-size="12" fill="'+(j===ks&&st.mes>0?'#b8651b':'#444')+'" font-weight="'+(j===ks&&st.mes>0?700:400)+'">'+MC[j]+'</text>';
    const v=vals[j]; if(v===null||v===undefined||!isFinite(v)) continue;
    g+='<circle cx="'+x(j)+'" cy="'+y(v)+'" r="'+(j===ks&&st.mes>0?7:5)+'" fill="'+COL[cols[j]]+'" stroke="'+(j===ks&&st.mes>0?'#14213d':'#fff')+'" stroke-width="'+(j===ks&&st.mes>0?2.5:1.5)+'"/>'+
       '<text x="'+x(j)+'" y="'+(y(v)-10)+'" text-anchor="middle" font-size="11" fill="#333">'+(esC?abbr(v,'cop',false):nf1.format(v)+'%')+'</text>'+
       '<rect class="dhit" data-m="'+(j+1)+'" x="'+(x(j)-iw/24)+'" y="'+mt+'" width="'+(iw/12)+'" height="'+(ih+mb)+'" fill="transparent"><title>'+ML[j]+': '+proFmt(i,v)+'</title></rect>';
  }
  return g+'</svg>';
}
function proCompTable(i){
  const defs={
    garantias:[['Proyectos ejecutados',c=>nf.format(c.ejec)],['Garantías',c=>nf.format(c.gar)]],
    proveedores:[['Proveedores nuevos',c=>nf.format(c.prov)],['Fuente',c=>c.dig>0?'digitado':'Odoo']],
    eficiencia:[['Proyectos',c=>nf.format(c.nefi)],['Costo proyectado',c=>proMon(c.proy)],['Costo real',c=>proMon(c.real)]],
    costo_garantias:[['Garantías',c=>nf.format(c.gar)]]};
  const cols=defs[i];
  let h='<h3>Cómo se calcula mes a mes</h3><div class="ed"><table><thead><tr><th>Mes</th>'+cols.map(c=>'<th>'+c[0]+'</th>').join('')+'<th>'+PNAMES[i]+'</th></tr></thead><tbody>';
  const j0 = i==='proveedores' ? D.pro.desde_prov-1 : D.pro.desde-1;
  for(let j=Math.max(j0,0); j<D.pro.ultimo; j++){const v=proVal(j,j);
    h+='<tr data-m="'+(j+1)+'" class="'+(st.mes>0&&j===proK()?'sel':'')+'"><td>'+ML[j]+'</td>'+cols.map(c=>'<td>'+c[1](v.comp)+'</td>').join('')+'<td><span class="pill '+proColor(i,v[i])+'">'+proFmt(i,v[i])+'</span></td></tr>';}
  return h+'</tbody></table></div>';
}
function proDet(k){return Object.assign({ejec:[],gar:[],prov:[],excl:[]}, (D.pro.det||{})[String(k)]||{});}
function proDetalle(i,k){
  const d=proDet(k), mesTxt=ML[k]+' '+D.anio, nota = st.mes>0 ? '' : ' <span class="muted small">(último mes con datos; elija un mes arriba para ver otro)</span>';
  const lim='<div class="muted small">Se muestran hasta 80 registros por mes.</div>';
  const gTbl='<div class="dsec"><h3>Garantías creadas en '+mesTxt+nota+'</h3>'+dTbl(['Brief','Cliente','Creada','Costo real'], d.gar.map(r => [esc(r[0]),esc(r[1]),esc(r[2]),proMon(r[3])]))+'</div>';
  let h='';
  if(i==='garantias'){
    h+=gTbl+'<div class="dsec"><h3>Proyectos ejecutados (primera factura en '+mesTxt+')</h3>'+
       dTbl(['Proyecto','Cliente','Tipo','Primera factura'], d.ejec.map(r => [esc(r[0]),esc(r[1]),esc(r[2]),esc(r[3])]))+lim+'</div>';
  } else if(i==='proveedores'){
    const man=PMAN[k];
    h+='<div class="dsec"><h3>Proveedores cuya primera factura a proyectos fue en '+mesTxt+nota+'</h3>'+
       dTbl(['Proveedor','Primera factura'], d.prov.map(r => [esc(r[0]),esc(r[1])]))+
       '<div class="muted small">'+((man!==null&&man!==undefined)?'Este mes tiene el dato digitado a mano ('+nf.format(man)+'); prevalece sobre el conteo de Odoo.':'Se cuentan todos los proveedores de la lista; si solo algunos son de servicios, digite el dato oficial en “Editar metas”.')+'</div></div>';
  } else if(i==='eficiencia'){
    h+='<div class="dsec"><h3>Proyectos incluidos · primera factura en '+mesTxt+nota+'</h3>'+
       dTbl(['Proyecto','Cliente','Costo proyectado','Costo real','Ahorro'], d.ejec.filter(r => r[7]===1).map(r => [esc(r[0]),esc(r[1]),proMon(r[4]),proMon(r[5]),(r[6]===null?'—':nf1.format(r[6])+'%')]))+lim+'</div>'+
       '<div class="dsec"><h3>Proyectos ejecutados que no entran en la eficiencia</h3>'+
       dTbl(['Proyecto','Cliente','Costo proyectado','Costo real','Motivo'], d.excl.map(r => [esc(r[0]),esc(r[1]),proMon(r[2]),proMon(r[3]),esc(r[4])]))+
       '<div class="muted small">Se excluyen los de costo proyectado menor a '+proMon(D.pro.min_proy)+' (valores simbólicos) y los que aún no tienen costo real.</div></div>';
  } else {
    h+=gTbl;
  }
  return h;
}

function renderPro(){
  const root=document.getElementById('pro_root');
  if(!proOk()){document.getElementById('t_pro').textContent='Indicadores del área de Producción'; root.innerHTML='<div class="aviso">No hay datos suficientes de briefs aprobados y planes analíticos para este año.</div>'; return;}
  const A=D.pro, k=proK(), rg=proRango();
  const per = st.mes>0 ? ML[k]+' '+D.anio : 'acumulado '+MC[rg[0]]+'–'+MC[rg[1]]+' '+D.anio;
  document.getElementById('t_pro').textContent='Indicadores del área de Producción · '+per;
  const v=proVal(rg[0],rg[1]), c=v.comp, hj=A.hoja[String(k+1)], i=st.pind;
  const refHoja = x => (st.mes>0 && hj && hj[x]!==undefined) ? '<div class="small muted">Según la hoja: '+nf1.format(hj[x])+'%</div>' : '';
  const sub = {
    garantias: nf.format(c.gar)+' garantías de '+nf.format(c.ejec)+' proyectos ejecutados',
    proveedores: nf.format(c.prov)+' proveedores nuevos ('+(c.dig>0?'digitado':'Odoo')+')',
    eficiencia: nf.format(c.nefi)+' proyectos · real '+proMon(c.real)+' de '+proMon(c.proy),
    costo_garantias: nf.format(c.gar)+' garantías creadas'};
  let h='<div class="ind adm">';
  PK.forEach(x => {
    const val=v[x], cl=proColor(x,val);
    h+='<div class="ic clk '+cl+(x===i?' selc':'')+'" data-i="'+x+'"><div class="l">'+PNAMES[x]+'</div><div class="p">'+proFmt(x,val)+'</div>'+
       '<div class="small">'+(x==='costo_garantias' ? 'Informativo (sin meta)' : '<b>Meta</b> '+(A.tipo[x]==='min'?'≥ ':'≤ ')+proFmt(x,PMETA[x]))+'</div>'+
       '<div class="small muted">'+sub[x]+'</div>'+refHoja(x)+'</div>';
  });
  h+='</div><div class="small muted" style="margin:-4px 0 8px">Haga clic en un indicador para ver su detalle. Esta pestaña no depende del selector de empresa.</div>';
  const hjI = (st.mes>0 && hj && hj[i]!==undefined) ? ' · según la hoja: '+nf1.format(hj[i])+'%' : '';
  h+='<div class="dp"><h2 class="h2s">'+PNAMES[i]+' · detalle de '+(st.mes>0? ML[k]+' '+D.anio : 'todo el año')+'</h2><div class="small muted">'+PFORM[i]+'<br>'+
     (i==='costo_garantias' ? 'Valor '+proFmt(i,v[i]) : 'Meta '+(A.tipo[i]==='min'?'≥ ':'≤ ')+proFmt(i,PMETA[i])+' · valor '+proFmt(i,v[i]))+hjI+'</div>'+
     '<div style="margin-top:8px">'+proChart(i)+'</div>'+proCompTable(i)+proDetalle(i,k)+'</div>';
  h+='<h2>Resumen de los indicadores por mes</h2>';
  let t='<table><thead><tr><th>Mes</th>'+PK.map(x=>'<th title="'+PDESC[x]+'">'+PNAMES[x]+'</th>').join('')+'</tr></thead><tbody>';
  for(let j=0;j<12;j++){
    if(j>=A.ultimo || j<A.desde-1){t+='<tr><td>'+ML[j]+'</td>'+PK.map(()=>'<td><span class="pill n">—</span></td>').join('')+'</tr>'; continue;}
    const w=proVal(j,j), hh=A.hoja[String(j+1)];
    t+='<tr data-m="'+(j+1)+'" class="'+(st.mes>0&&j===k?'sel':'')+'"><td>'+ML[j]+'</td>'+PK.map(x => {
      if(x==='proveedores' && j<A.desde_prov-1 && (PMAN[j]===null||PMAN[j]===undefined)) return '<td><span class="pill n">—</span></td>';
      return '<td><span class="pill '+proColor(x,w[x])+'">'+proFmt(x,w[x])+'</span>'+((hh && hh[x]!==undefined) ? ' <span class="muted small">(hoja '+nf1.format(hh[x])+')</span>' : '')+'</td>';}).join('')+'</tr>';
  }
  h+=t+'</tbody></table>';
  h+='<div class="small muted" style="margin-top:14px">Verde: cumple la meta; amarillo: al menos '+Math.round(A.amarillo_min*100)+'% de la meta; rojo: peor. '+
    'Fuente: Brief Aprobados (tipo y costo proyectado), planes analíticos de proyecto (costo real, sin las categorías '+esc(A.excluidas.join(' y '))+'), facturas de cliente contabilizadas (mes de ejecución) y facturas de proveedor (proveedores nuevos). '+
    'Solo cuentan los tipos Producción y Prototipo Facturable; Prototipo, Mantenimiento y Servicios quedan fuera. '+
    'Odoo solo tiene datos desde enero de 2026: se muestra desde febrero (proveedores desde marzo). En “Año completo” se acumulan los conteos y las sumas de los meses. '+
    'La hoja la digita el responsable proyecto por proyecto, y por eso sus cifras pueden no coincidir exactamente con Odoo. '+
    'Las metas se pueden editar con “Editar metas”; los valores iniciales vienen de metas_produccion.json.</div>';
  root.innerHTML=h;
  root.querySelectorAll('.ic[data-i]').forEach(e => e.onclick = () => {st.pind=e.dataset.i; renderPro();});
  root.querySelectorAll('tr[data-m]').forEach(r => r.onclick = () => {st.mes=+r.dataset.m; render();});
  root.querySelectorAll('.dhit').forEach(r => r.onclick = () => {st.mes=+r.dataset.m; render();});
}
function initPro(){
  PKEY='diforma_metas_pro_'+D.anio;
  document.getElementById('pro_edit').style.display='none';
  if(!proOk()) return;
  PMETA=Object.assign({},D.pro.meta); PDEF=Object.assign({},D.pro.meta);
  PMAN=D.pro.manual_prov.slice(); PMANDEF=PMAN.slice();
  try{const sv=JSON.parse(localStorage.getItem(PKEY)||'null');
    if(sv){PK.forEach(i => {if(sv.meta && typeof sv.meta[i]==='number' && sv.meta[i]>=0) PMETA[i]=sv.meta[i];});
      if(Array.isArray(sv.prov)) sv.prov.forEach((x,j) => {if(j<12 && (x===null||typeof x==='number')) PMAN[j]=x;});}}catch(e){}
}
const proCambios = () => PK.some(i => PMETA[i]!==PDEF[i]) || PMAN.some((x,j) => x!==PMANDEF[j]);
function proEst(){document.getElementById('pro_est').textContent = !proOk()? '' : (proCambios() ? 'Metas/datos modificados a mano (guardados en este navegador).' : 'Metas de la hoja de indicadores.');}
function proSave(){try{const v=JSON.stringify({meta:PMETA, prov:PMAN}); localStorage.setItem(PKEY, v); nubeGuardar(PKEY,v);}catch(e){}}
function renderProEdit(){
  const el=document.getElementById('pro_edit');
  let h='<div class="ed"><div class="small muted" style="margin-bottom:6px">Escriba la meta y el dashboard se actualiza al instante. Los cambios se guardan en este navegador; para dejarlos permanentes edite metas_produccion.json. Las celdas amarillas difieren de la hoja.</div><table><thead><tr><th>Indicador</th><th>Meta</th><th>Criterio</th></tr></thead><tbody>';
  PK.filter(i => i!=='costo_garantias').forEach(i => {h+='<tr><td>'+PNAMES[i]+'</td><td><input type="number" step="any" min="0" data-m="'+i+'" value="'+PMETA[i]+'" class="'+(PMETA[i]!==PDEF[i]?'mod':'')+'"></td><td class="small muted">'+(D.pro.tipo[i]==='min'?'cumple si es mayor o igual':'cumple si es menor o igual')+' (en %)</td></tr>';});
  h+='</tbody></table><div class="small muted" style="margin:10px 0 4px">Proveedores nuevos de servicios (dato oficial, opcional: cuántos fueron en el mes; si queda vacío se usa el conteo de Odoo)</div><table><thead><tr>'+MC.map(x=>'<th>'+x+'</th>').join('')+'</tr></thead><tbody><tr>'+
     PMAN.map((x,j) => '<td><input type="number" step="any" min="0" data-b="'+j+'" value="'+(x===null||x===undefined?'':x)+'" class="'+(x!==PMANDEF[j]?'mod':'')+'" style="width:56px"></td>').join('')+'</tr></tbody></table>'+
     '<div style="margin-top:8px"><button class="chip" id="pro_reset">Restablecer valores de la hoja</button></div></div>';
  el.innerHTML=h;
  const refresh=()=>{proSave(); proEst(); renderPro();};
  el.querySelectorAll('input[data-m]').forEach(inp => inp.oninput = () => {
    const v=parseFloat(inp.value); if(isNaN(v)||v<0) return; PMETA[inp.dataset.m]=v; inp.classList.toggle('mod', v!==PDEF[inp.dataset.m]); refresh();});
  el.querySelectorAll('input[data-b]').forEach(inp => inp.oninput = () => {
    const j=+inp.dataset.b, v=inp.value===''?null:parseFloat(inp.value); if(v!==null&&(isNaN(v)||v<0)) return; PMAN[j]=v; inp.classList.toggle('mod', v!==PMANDEF[j]); refresh();});
  document.getElementById('pro_reset').onclick = () => {PMETA=Object.assign({},PDEF); PMAN=PMANDEF.slice(); refresh(); renderProEdit();};
}

function render(){
  compute(); updateCtrl(); updateTabs();
  if(st.tab==='com'){renderKpis(); renderInd(); renderChart(); renderRankings(); renderTabla();}
  else if(st.tab==='adm') renderAdm();
  else if(st.tab==='pro') renderPro();
  else renderDis();
}
function defaultEmp(){
  const i=D.empresas.findIndex(e => /diforma/i.test(e) && !/disenni/i.test(e));
  return i;   // -1 si no existe: todas
}
function setAnio(a){
  st.anio=a; D=MULTI.por_anio[a]; MC=D.meses_cortos; ML=D.meses_largos; U=D.ultimo_mes;
  if(st.empName===null) st.emp=defaultEmp(); else st.emp=D.empresas.indexOf(st.empName);
  if(st.emp>=0) st.empName=D.empresas[st.emp]; else if(st.empName===null) st.empName='';
  if(st.mes>12) st.mes=0;
  if(st.com && !comLista().some(k => nrm(k)===nrm(st.com))) st.com='';
  renderCtrl(); initMetas(); estMetas(); initAdm(); initDis(); disEst(); initPro(); proEst(); render();
}
document.querySelectorAll('#monseg button').forEach(b => b.onclick = () => {st.mon=b.dataset.c; render();});
document.getElementById('btn_metas').onclick = () => {
  if(!D.ind) return;
  const e=document.getElementById('ind_edit'), ab=e.style.display==='none';
  e.style.display=ab?'':'none'; if(ab) renderEdit();
};
document.getElementById('btn_metas_adm').onclick = () => {
  if(!admOk()) return;
  const e=document.getElementById('adm_edit'), ab=e.style.display==='none';
  e.style.display=ab?'':'none'; if(ab) renderAdmEdit();
};
document.getElementById('btn_metas_dis').onclick = () => {
  if(!disOk()) return;
  const e=document.getElementById('dis_edit'), ab=e.style.display==='none';
  e.style.display=ab?'':'none'; if(ab) renderDisEdit();
};
document.getElementById('btn_metas_pro').onclick = () => {
  if(!proOk()) return;
  const e=document.getElementById('pro_edit'), ab=e.style.display==='none';
  e.style.display=ab?'':'none'; if(ab) renderProEdit();
};
renderTabs();
nubeCargar().then(()=>setAnio(MULTI.anio_defecto));
})();
</script></body></html>
"""


def _url_nube():
    """URL del Apps Script que guarda las metas compartidas (archivo nube.json junto a este modulo)."""
    try:
        return str(json.loads((Path(__file__).resolve().parent / "nube.json").read_text(encoding="utf-8")).get("url", "")).strip()
    except (OSError, ValueError):
        return ""


PLANTILLA = PLANTILLA.replace("__NUBE__", _url_nube())


def main():
    iv.cargar_env()
    ap = argparse.ArgumentParser(description="Dashboard anual interactivo de ventas facturadas (Odoo 19)")
    ap.add_argument("--anio", type=int, default=datetime.now().year, help="Año a mostrar (por defecto: el actual)")
    ap.add_argument("--salida", default="informes", help="Carpeta de salida (por defecto: informes)")
    args = ap.parse_args()

    faltan = [v for v in ("ODOO_URL", "ODOO_DB", "ODOO_KEY") if not os.environ.get(v)]
    if faltan:
        sys.exit("Faltan datos de conexion: " + ", ".join(faltan) + ". Complete el archivo conexion.txt.")
    odoo = iv.Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"], os.environ["ODOO_KEY"])

    print(f"Consultando Odoo para el año {args.anio} (puede tardar unos minutos) ...")
    try:
        datos, avisos = construir_multi(odoo, args.anio)
    except (RuntimeError, requests.RequestException) as e:
        sys.exit(f"Error: {e}")
    if datos is None:
        sys.exit(f"No hay facturas publicadas en {args.anio}.")
    for a in avisos:
        print("AVISO:", a)

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    ruta = salida / f"dashboard_indicadores_{args.anio}.html"
    js = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    ruta.write_text(PLANTILLA.replace("__ANIO__", str(args.anio)).replace("__DATOS__", js), encoding="utf-8")
    print("Listo:", ruta.resolve())


if __name__ == "__main__":
    main()
