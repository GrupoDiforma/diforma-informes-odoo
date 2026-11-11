#!/usr/bin/env python3
"""
Dashboard de Indicadores (areas comercial y administrativa) - Grupo Diforma SAS (Odoo 19)

Uso:
    python dashboard_anual.py               # año actual
    python dashboard_anual.py --anio 2025   # otro año

Incluye los indicadores del area comercial (ver indicadores_comercial.py y metas_comercial.json)
y los del area administrativa (indicadores_admin.py y metas_admin.json).
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
    ven, ven_n = codificar(act["vendedor"])
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
    pr = prev.assign(emp=pe).groupby(["mes", "emp"])[["usd", "cop"]].sum().reset_index()
    prev_filas = [[int(r.mes), int(r.emp), round(float(r.usd), 2), round(float(r.cop))] for r in pr.itertuples()]

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
.ind.adm{grid-template-columns:repeat(4,1fr)}.ic .l small{font-weight:400;color:#666}
.ant{font-size:13px;margin:8px 0}.ant td,.ant th{padding:4px 8px}
@media(max-width:760px){.ind,.ind.adm{grid-template-columns:1fr 1fr}}
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
<div class="small" style="margin:4px 0"><button class="chip" id="btn_metas">✎ Editar metas</button> <span class="muted" id="metas_est"></span></div>
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
const st = {anio:null, mes:0, mon:'usd', emp:-1, empName:null, tab:'com', ind:'razon'};
const OTRA = {usd:'cop', cop:'usd'};
// fila r: [mes, emp, cli, ven, equ, pro, cat, fac, usd, cop]
let M, P, RW;

function compute(){
  RW = st.emp<0 ? D.r : D.r.filter(r => r[1]===st.emp);
  M = {usd:Array(12).fill(0), cop:Array(12).fill(0), n:Array(12).fill(0)};
  const sets = Array.from({length:12}, () => new Set());
  RW.forEach(r => {const m=r[0]-1; M.usd[m]+=r[8]; M.cop[m]+=r[9]; if(D.ftipo[r[7]]===0) sets[m].add(r[7]);});
  M.n = sets.map(s => s.size);
  P = {usd:Array(12).fill(0), cop:Array(12).fill(0)};
  D.pr.forEach(r => {if(st.emp<0 || r[1]===st.emp){P.usd[r[0]-1]+=r[2]; P.cop[r[0]-1]+=r[3];}});
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
  const el = document.getElementById('ctrl'); el.innerHTML = h;
  el.querySelectorAll('.chip').forEach(b => b.onclick = () => {st.mes=+b.dataset.m; render();});
  document.getElementById('anio').onchange = e => setAnio(+e.target.value);
  const se = document.getElementById('emp'); if(se) se.onchange = () => {st.emp=+se.value; st.empName = st.emp<0 ? '' : D.empresas[st.emp]; render();};
}
function updateCtrl(){
  document.querySelectorAll('#ctrl .chip').forEach(b => b.classList.toggle('on', +b.dataset.m===st.mes));
  document.querySelectorAll('#monseg button').forEach(b => b.classList.toggle('on', b.dataset.c===st.mon));
}
const TABS=[['com','Indicadores Área Comercial'],['adm','Indicadores Área Administrativa']];
function renderTabs(){
  const el=document.getElementById('tabs');
  el.innerHTML=TABS.map(t => '<button class="tab" data-t="'+t[0]+'">'+t[1]+'</button>').join('');
  el.querySelectorAll('.tab').forEach(b => b.onclick = () => {st.tab=b.dataset.t; render();});
}
function updateTabs(){
  document.querySelectorAll('#tabs .tab').forEach(b => b.classList.toggle('on', b.dataset.t===st.tab));
  document.getElementById('tab_com').style.display = st.tab==='com' ? '' : 'none';
  document.getElementById('tab_adm').style.display = st.tab==='adm' ? '' : 'none';
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
function valI(x,i){
  if(i!=='bill') return x.val[i];
  return D.r.reduce((a,r) => (r[0]===x.m && (st.emp<0 || r[1]===st.emp)) ? a+r[8] : a, 0);
}
function indPeriodo(i){
  // devuelve {val, meta} del periodo seleccionado
  const ms=D.ind.meses.filter(x => x.val);
  if(st.mes>0){const x=D.ind.meses[st.mes-1]; return x.val ? {val:valI(x,i), meta:x.meta[i]} : null;}
  if(!ms.length) return null;
  if(i==='pipe'){const x=ms[ms.length-1]; return {val:x.val.pipe, meta:x.meta.pipe};}
  return {val:ms.reduce((a,x)=>a+valI(x,i),0), meta:ms.reduce((a,x)=>a+x.meta[i],0)};
}
function renderInd(){
  const blk=document.getElementById('bloque_ind');
  if(!D.ind || !D.ind.ok){blk.style.display='none'; return;}
  blk.style.display='';
  document.getElementById('t_ind').textContent='Indicadores del área comercial · '+periodoTxt();
  let h='';
  ['opp','pipe','po','bill'].forEach(i => {
    const r=indPeriodo(i);
    if(!r){h+='<div class="ic"><div class="l">'+INAMES[i]+'</div><div class="p">—</div></div>'; return;}
    const p=r.meta? r.val/r.meta*100 : null, c=colorI(i,p);
    let ref='';
    if(st.mes>0){const hj=D.ind.meses[st.mes-1].hoja; if(hj && hj[i]!==undefined) ref='<div class="small muted">Según la hoja: '+hj[i]+'%</div>';}
    const sub = (st.mes===0 && i==='pipe') ? 'cierre de '+MC[D.ind.meses.filter(x=>x.val).length-1] : IDESC[i];
    h+='<div class="ic '+c+'"><div class="l">'+INAMES[i]+'</div><div class="p">'+(p===null?'—':Math.round(p)+'%')+'</div>'+
       '<div class="pb"><i style="width:'+Math.min(100,p||0)+'%"></i></div>'+
       '<div class="small"><b>'+fmtI(i,r.val)+'</b> de '+fmtI(i,r.meta)+'</div><div class="small muted">'+sub+' · umbral '+D.ind.umbral[i]+'%</div>'+ref+'</div>';
  });
  document.getElementById('ind_cards').innerHTML=h;
  let t='<table><thead><tr><th>Mes</th>'+['opp','pipe','po','bill'].map(i=>'<th>'+INAMES[i]+'</th>').join('')+'</tr></thead><tbody>';
  D.ind.meses.forEach((x,k) => {
    t+='<tr data-m="'+(k+1)+'" class="'+(st.mes===k+1?'sel':'')+'"><td>'+ML[k]+'</td>';
    ['opp','pipe','po','bill'].forEach(i => {
      if(!x.val){t+='<td><span class="pill n">—</span></td>'; return;}
      const vv=valI(x,i), p=x.meta[i]? vv/x.meta[i]*100 : null, hj = x.hoja && x.hoja[i]!==undefined ? ' <span class="muted small">(hoja '+x.hoja[i]+'%)</span>' : '';
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
function saveMetas(){try{localStorage.setItem(MKEY, JSON.stringify(D.ind.meses.map(x=>x.meta)));}catch(e){}}
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
  const m=new Map(), F=['ab','fav','nv','d30','d60','d90','d91','pond','base','n'];
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
function modDeben(){
  const k=admK(), cat=new Map(), ter=new Map(), nm=admNombres();
  nm.forEach(n => {
    const M=D.adm.m[n];
    (M.deb_ctas||[]).forEach(c => {const v=c[3][k]; if(!v) return; if(c[0]==='imp' && v<=0) return;
      const key=c[0]+'\u0001'+c[1]+'\u0001'+c[2]; cat.set(key,(cat.get(key)||0)+v);});
    ((M.terc||{}).deb||[]).forEach(r => {const x=r[1][k]; if(x) ter.set(r[0],(ter.get(r[0])||0)+x);});
    ((M.terc||{}).imp||[]).forEach(r => {const x=r[1][k]; if(x>0) ter.set(r[0],(ter.get(r[0])||0)+x);});
  });
  const crows=[...cat.entries()].sort((a,b)=>b[1]-a[1]), ctot=crows.reduce((a,x)=>a+x[1],0);
  const trows=[...ter.entries()].filter(r => r[1]>0).sort((a,b)=>b[1]-a[1]), ttot=trows.reduce((a,x)=>a+x[1],0);
  const ab=abMap('cxc');
  let h='<h2 class="h2s">¿Quién nos debe?</h2><div class="small muted">Saldos a favor de la empresa a fin de '+ML[k]+': cartera de clientes, anticipos entregados, impuestos a favor (IVA, retenciones, saldos a favor con la DIAN), cuentas por cobrar a trabajadores/socios y deudores varios. Total · <b>'+cop(ctot)+'</b></div>';
  h+='<div class="grid2"><div><h3>Por concepto (cuenta contable)</h3>';
  if(!crows.length) h+='<div class="muted small">Sin saldos.</div>';
  else h+='<div class="ed"><table><thead><tr><th>Código</th><th>Cuenta</th><th>Saldo</th><th>%</th></tr></thead><tbody>'+
    crows.slice(0,25).map(r => {const p=r[0].split('\u0001'); return '<tr><td>'+esc(p[1])+'</td><td style="text-align:left">'+esc(p[2])+(p[0]==='imp'?' <span class="muted small">(impuesto a favor)</span>':'')+'</td><td>'+cop(r[1])+'</td><td>'+(ctot? pf.format(r[1]/ctot*100):'—')+'%</td></tr>';}).join('')+
    '</tbody></table></div>'+(crows.length>25?'<div class="muted small">+ '+(crows.length-25)+' cuentas más</div>':'');
  h+='</div><div><h3>Por tercero · '+cop(ttot)+'</h3>';
  if(!trows.length) h+='<div class="muted small">Sin saldos.</div>';
  else h+='<div class="ed"><table><thead><tr><th>Tercero</th><th>Saldo</th><th>%</th><th>Vencido hoy (cartera)</th><th>Mora prom.</th></tr></thead><tbody>'+
    trows.slice(0,20).map(r => {const a=ab.get(r[0]), v=a? a.d30+a.d60+a.d90+a.d91 : 0;
      return '<tr><td title="'+esc(r[0])+'">'+esc(r[0])+'</td><td>'+cop(r[1])+'</td><td>'+(ttot? pf.format(r[1]/ttot*100):'—')+'%</td><td>'+(a? cop(v):'—')+'</td><td>'+(a && a.base? nf.format(Math.round(a.pond/a.base))+' d':'—')+'</td></tr>';}).join('')+
    '</tbody></table></div>'+(trows.length>20?'<div class="muted small">+ '+(trows.length-20)+' terceros más</div>':'');
  h+='</div></div><div class="small muted" style="margin-top:6px">Las cuentas de impuestos (24*) solo se cuentan cuando tienen saldo a favor de la empresa (débito). Si un tercero es la DIAN, su saldo a favor aparece aquí con el nombre con que esté registrado en Odoo.</div>';
  return h;
}
function renderAb(lado,id){
  const el=document.getElementById(id||('ab_'+lado)); if(!el) return;
  const hoy=D.adm.hoy, m=abMap(lado), rows=[...m.values()].sort((a,b)=>b.ab-a.ab);
  const T=f=>rows.reduce((a,r)=>a+r[f],0), ab=T('ab'), venc=T('d30')+T('d60')+T('d90')+T('d91');
  const tit = lado==='cxc' ? 'Cartera de clientes: quién nos debe y cuántos días de mora' : 'Cuentas por pagar: a quién se le debe y cuántos días de mora';
  let h='<h3>'+tit+'</h3><div class="small muted">Saldos abiertos a hoy ('+hoy+'). Abierto '+cop(ab)+' · vencido '+cop(venc)+' ('+(ab? Math.round(venc/ab*100):0)+'%) · mora promedio ponderada '+(T('base')? nf.format(Math.round(T('pond')/T('base')))+' d':'—')+
    ' · saldos a favor/anticipos aparte: '+cop(T('fav'))+'. Haga clic en un tercero para ver sus facturas.</div>';
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
    case 'razon': return sec('c',modComposicion(k,false))+
      sec('p','<h2 class="h2s">¿A quién se le debe?</h2><div class="grid2"><div>'+modTerceros('prov','Proveedores y cuentas por pagar','cxp',12)+'</div><div>'+modTerceros('fin','Obligaciones financieras (bancos, tarjetas, particulares)',null,12)+'</div></div>'+
        '<div style="margin-top:12px">'+modTerceros('otp','Otros pasivos (anticipos recibidos, impuestos, nómina, retenciones)',null,12)+'</div>')+ph('cxp')+
      sec('a',modCuentas('pas','','Pasivo por cuenta contable',25));
    case 'apal': return sec('c',modComposicion(k,true))+sec('a',modCuentas('pas','','Pasivo por cuenta contable',20))+
      sec('p','<h2 class="h2s">¿A quién se le debe?</h2><div class="grid2"><div>'+modTerceros('prov','Proveedores y cuentas por pagar','cxp',10)+'</div><div>'+modTerceros('fin','Obligaciones financieras',null,10)+'</div></div>');
    case 'fin': return sec('c','<div class="grid2"><div>'+modCuentas('pas','21','Obligaciones financieras por cuenta',30)+'</div><div>'+modTerceros('fin','¿A quién se le debe? (por tercero)',null,20)+'</div></div>');
    case 'rot': case 'cobro': return sec('t',modTerceros('cxc','Cartera por cliente','cxc',15))+ph('cxc');
    case 'pago': return sec('t',modTerceros('prov','Cuentas por pagar por proveedor','cxp',15))+ph('cxp');
    case 'ciclo': return sec('i',modCuentas('act','14','Inventarios por cuenta contable (14*)',15))+ph('cxc');
    default: return '<div class="dsec muted small">Este indicador se digita a mano: use “Editar metas” → fila de Bienestar para registrar el % cumplido de cada mes.</div>';
  }
}
function lados(i){return {razon:['cxp'],apal:[],fin:[],rot:['cxc'],cobro:['cxc'],pago:['cxp'],ciclo:['cxc'],bienestar:[]}[i]||[];}

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
  h+='<div class="dp">'+modDeben()+'<div class="dsec" id="ab_cxc_deb"></div></div>';
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
  renderAb('cxc','ab_cxc_deb');
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
function admSave(){try{localStorage.setItem(AKEY, JSON.stringify({meta:AMETA, bien:ABIEN}));}catch(e){}}
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

function render(){
  compute(); updateCtrl(); updateTabs();
  if(st.tab==='com'){renderKpis(); renderInd(); renderChart(); renderRankings(); renderTabla();}
  else renderAdm();
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
  renderCtrl(); initAdm(); render();
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
renderTabs();
setAnio(MULTI.anio_defecto);
})();
</script></body></html>
"""


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
