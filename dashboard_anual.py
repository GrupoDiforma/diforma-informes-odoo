#!/usr/bin/env python3
"""
Dashboard anual interactivo de ventas facturadas - Grupo Diforma SAS (Odoo 19)

Uso:
    python dashboard_anual.py               # año actual
    python dashboard_anual.py --anio 2025   # otro año

Incluye los indicadores del area comercial (ver indicadores_comercial.py y metas_comercial.json).
Genera un unico archivo HTML (informes/dashboard_ventas_AAAA.html) que se abre en el
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
        ftipo=ftipo, r=filas, pr=prev_filas, avisos=avisos,
    )
    try:
        billing = act.groupby("mes")["usd"].sum().to_dict()
        datos["ind"] = ic.calcular(odoo, anio, ic.cargar_config(), billing, avisos)
    except (RuntimeError, requests.RequestException, OSError, ValueError, KeyError) as e:
        avisos.append("No se pudieron calcular los indicadores del area comercial: " + str(e)[:200])
        datos["ind"] = None
    return datos, avisos


PLANTILLA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard de ventas __ANIO__ - Grupo Diforma</title>
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
@media(max-width:760px){.ind{grid-template-columns:1fr 1fr}}
@media(max-width:760px){.kpis{grid-template-columns:1fr}.grid2{grid-template-columns:1fr}}
</style></head><body><div class="wrap">
<h1>Dashboard de ventas facturadas __ANIO__</h1>
<div class="sub" id="sub"></div>
<div id="avisos"></div>
<div class="ctrl" id="ctrl"></div>
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
</div>
<script type="application/json" id="datos">__DATOS__</script>
<script>
(function(){
const D = JSON.parse(document.getElementById('datos').textContent);
const MC = D.meses_cortos, ML = D.meses_largos, U = D.ultimo_mes;
const nf = new Intl.NumberFormat('es-CO',{maximumFractionDigits:0});
const nf1 = new Intl.NumberFormat('es-CO',{maximumFractionDigits:1});
const pf = new Intl.NumberFormat('es-CO',{minimumFractionDigits:1,maximumFractionDigits:1});
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Por defecto se muestra solo Grupo Diforma S.A.S (se puede cambiar a "Todas las empresas")
const EMP_DEF = D.empresas.findIndex(e => e.toLowerCase().indexOf('grupo diforma') >= 0);
const st = {mes: D.mes_inicial, mon: 'usd', emp: EMP_DEF};
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
  let h = '<button class="chip todo" data-m="0">Año completo</button>';
  for(let i=1;i<=12;i++) h += '<button class="chip" data-m="'+i+'">'+MC[i-1]+'</button>';
  h += '<span class="sep"></span>';
  if(D.empresas.length>1){
    h += '<select id="emp"><option value="-1">Todas las empresas</option>' +
         D.empresas.map((e,i) => '<option value="'+i+'"'+(i===st.emp?' selected':'')+'>'+esc(e)+'</option>').join('') + '</select>';
  }
  h += '<span class="seg"><button data-c="usd">USD</button><button data-c="cop">COP</button></span>';
  const el = document.getElementById('ctrl'); el.innerHTML = h;
  el.querySelectorAll('.chip').forEach(b => b.onclick = () => {st.mes=+b.dataset.m; render();});
  el.querySelectorAll('.seg button').forEach(b => b.onclick = () => {st.mon=b.dataset.c; render();});
  const se = document.getElementById('emp'); if(se) se.onchange = () => {st.emp=+se.value; render();};
}
function updateCtrl(){
  document.querySelectorAll('#ctrl .chip').forEach(b => b.classList.toggle('on', +b.dataset.m===st.mes));
  document.querySelectorAll('#ctrl .seg button').forEach(b => b.classList.toggle('on', b.dataset.c===st.mon));
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
const MKEY='diforma_metas_'+D.anio, IK=['opp','pipe','po','bill'];
let DEFM=null;
function initMetas(){
  if(!D.ind) return;
  DEFM=D.ind.meses.map(x => Object.assign({}, x.meta));
  try{const sv=JSON.parse(localStorage.getItem(MKEY)||'null');
    if(sv) sv.forEach((m,k) => {if(m && D.ind.meses[k]) IK.forEach(i => {if(typeof m[i]==='number' && m[i]>=0) D.ind.meses[k].meta[i]=m[i];});});}catch(e){}
}
function saveMetas(){try{localStorage.setItem(MKEY, JSON.stringify(D.ind.meses.map(x=>x.meta)));}catch(e){}}
const hayCambios = () => D.ind.meses.some((x,k) => IK.some(i => x.meta[i]!==DEFM[k][i]));
function estMetas(){document.getElementById('metas_est').textContent = hayCambios() ? 'Metas modificadas a mano (guardadas en este navegador).' : 'Metas de la hoja de indicadores.';}
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

function render(){compute(); updateCtrl(); renderKpis(); renderInd(); renderChart(); renderRankings(); renderTabla();}
document.getElementById('sub').textContent = 'Grupo Diforma SAS · generado '+D.generado+' · datos hasta '+ML[U-1]+' '+D.anio;
document.getElementById('avisos').innerHTML = D.avisos.map(a => '<div class="aviso">⚠ '+esc(a)+'</div>').join('');
initMetas();
if(D.ind){
  document.getElementById('btn_metas').onclick = () => {
    const e=document.getElementById('ind_edit'), ab=e.style.display==='none';
    e.style.display=ab?'':'none'; if(ab) renderEdit();
  };
  estMetas();
}
renderCtrl(); render();
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
        datos, avisos = construir_datos(odoo, args.anio)
    except (RuntimeError, requests.RequestException) as e:
        sys.exit(f"Error: {e}")
    if datos is None:
        sys.exit(f"No hay facturas publicadas en {args.anio}.")
    for a in avisos:
        print("AVISO:", a)

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    ruta = salida / f"dashboard_ventas_{args.anio}.html"
    js = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    ruta.write_text(PLANTILLA.replace("__ANIO__", str(args.anio)).replace("__DATOS__", js), encoding="utf-8")
    print("Listo:", ruta.resolve())


if __name__ == "__main__":
    main()
