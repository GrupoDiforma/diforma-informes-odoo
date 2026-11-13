# Dashboard de Indicadores - Grupo Diforma

Dashboard HTML autocontenido (JSON incrustado, JS puro, SVG) generado en Python desde Odoo SH V19
(https://grupodiforma.odoo.com, API JSON-2, **solo lectura**). Publicado en
https://grupodiforma.github.io/diforma-informes-odoo/ por GitHub Actions (cada 6 h y en cada push a `main`).

## Reglas de trabajo
- Este clon es la única fuente de trabajo. Se responde en español.
- **Credenciales:** `conexion.txt` (ODOO_URL=, ODOO_DB=, ODOO_KEY=) se crea a mano y NUNCA se sube. Está en
  `.gitignore` junto con `.env`, `informes/`, `site/`, `*.xlsx`, `diagnostico*.txt`. Antes de cada commit verificar
  `git check-ignore conexion.txt` y que la clave no aparezca en el diff ni en el HTML generado. En Actions las
  credenciales son Secrets (`ODOO_URL`, `ODOO_DB`, `ODOO_KEY`) del entorno `github-pages`.
- **Odoo es solo lectura:** jamás llamar `create`/`write`/`unlink` ni nada que modifique datos.
- **Vista previa:** `python servidor_local.py` → http://localhost:8000. Regenera `site/index.html` con datos reales al
  arrancar y cuando cambia un `.py`/`.json`; el navegador se recarga solo. `/__regenerar` trae datos frescos.
  La página carga asíncrona (`nubeCargar().then(()=>setAnio(...))`): en pruebas esperar al selector `#anio`.
- **Publicar:** solo cuando el usuario diga "publica": `git fetch` + rebase sobre `origin/main`, commit y push a `main`.
  Luego revisar que el workflow "Publicar dashboard" termine bien.

## Estructura
- `informe_ventas.py`: clase `Odoo` (conexión) y ventas desde líneas de facturas contabilizadas (cifra oficial COP, también USD con TRM).
- `indicadores_comercial.py` + `metas_comercial.json`: oportunidades, pipeline, PO cerrado, billing. Helpers `leer_todo`, `_local`, `_ym`.
- `indicadores_admin.py` + `metas_admin.json`: 8 indicadores administrativos y detalle de saldos.
- `indicadores_diseno.py` + `metas_diseno.json`: 5 indicadores. `calcular(odoo, anio, cfg, avisos)` devuelve conteos; los cocientes se calculan en el JS.
- `indicadores_produccion.py` + `metas_produccion.json`: 4 indicadores (3 con meta + 1 informativo).
- `brief_diforma.py`, `produccion_diagnostico*.py`: diagnósticos/calibración de solo lectura.
- `dashboard_anual.py`: `construir_datos`, `construir_multi` y la `PLANTILLA` HTML/JS. Pestañas en `TABS=[['com',…],['adm',…],['dis',…],['pro',…]]`.
- `publicar_sitio.py`: genera `site/index.html` multi-año (lo usa Actions y `servidor_local.py`).
- `apps_script/Codigo.gs` + `nube.json`: metas compartidas en Google Sheets (escritura con PIN).

## Patrón del dashboard
- Selectores globales: año, periodo (año completo o mes) y empresa (por defecto Grupo Diforma S.A.S; opción todas).
- Cada pestaña: tarjetas con semáforo, tabla de 12 meses, panel de detalle por indicador, metas editables.
- Metas: fuente fija en `metas_<area>.json`; ediciones en `localStorage` (`diforma_metas_<area>_<año>`) o en la nube (Apps Script).
- Semáforo: verde si cumple; amarillo si queda hasta 25% por encima (menor es mejor) o ≥70% de la meta (mayor es mejor); rojo si peor.
- Datos manuales (Bienestar, Proyectos IA, proveedores nuevos opcional) se digitan en el panel de metas; un valor digitado prevalece sobre Odoo.
- Cada tarjeta/fila muestra "Según la hoja: X" cuando la hoja de Google tiene el dato.

## Nueva área (procedimiento)
1. Leer la hoja de indicadores del área (Google Sheets, una pestaña por mes).
2. Clasificar: lo genera Odoo / en parte / manual.
3. Diagnóstico de solo lectura contra Odoo con varias formas de contar y comparación contra la hoja.
4. Si nada cuadra, preguntar al usuario la definición exacta del responsable.
5. Crear `indicadores_<area>.py` + `metas_<area>.json` y la pestaña (insertar su JS antes de `function render()`).
6. Probar con Odoo simulado (clase Fake con `call`/`search_read`) y navegador headless (esperar `#anio`, revisar errores JS), luego vista previa con datos reales.

## Particularidades de Odoo
- Multiempresa: Grupo Diforma S.A.S (id 1) y Disenni S.A.S. (id 2). Los códigos de cuenta dependen de la empresa: leer con contexto `allowed_company_ids`.
- Datos solo desde 2026-01-01 (asiento de apertura). 2/1/2026: carga de 432 proveedores. 4/1/2026: carga de >5.100 briefs sin historial (se excluye enero en Diseño).
- Saldos: `account.move.line` con `parent_state=posted`, `balance = débito − crédito`. Clase PUC por el **primer dígito del código** (1 activo, 2 pasivo, 3 patrimonio, 4 ingreso, 5/6/7 gasto), no por `account_type`.
- Inventario con valoración periódica; solo "productos en proceso" en 14*.
- Diforma Brief = `crm.lead` (acción 949). Etapas: New 1, Design 2, Proposition 3, Budget 4, Quotation 5, Won 7. Nombre "17573-C Descripción" (proyecto + letra). Historial de etapas escaso (`mail.tracking.value`, ~1.500 cambios).
- Ingreso esperado a veces digitado en COP en campo USD: si > 500.000 se convierte con la TRM.
- `crm.brief_approved` (acción 950): leer con campos explícitos (`display_name`, `cantidad_total`, `has_message` dan "Expected singleton"). `costo` es POR UNIDAD (× `cantidad`). Tipos: 1 Producción, 2 Prototipo, 3 Prototipo Facturable, 4 Garantía, 5 Mantenimiento, 6 Servicios. State 1 Borrador / 2 Procesado.
- Facturas de cliente ↔ proyectos por `analytic_distribution` (claves = ids de cuentas analíticas, posiblemente "id,id"); `account.move.brief_id` → crm.lead.

## Definiciones de indicadores
### Administrativa
Razón de endeudamiento (pasivo/activo), obligaciones financieras/activo, apalancamiento, rotación de cartera, días de cobro,
días de mora al pagar, ciclo de caja, bienestar (manual). Saldos abiertos por tercero: pagos/créditos sin conciliar y anticipos
de clientes (28*) se aplican FIFO a las facturas más antiguas. Impuestos (24*, 1355*) como posición neta con la DIAN.
Vinculadas (p. ej. Disenni) separadas. Los saldos abiertos y la mora son a la fecha de generación, no al mes elegido.

### Diseño (hoja "Indicadores Area (Diseño)", responsable Vanessa Rojas)
- Presentado = brief con `fecha_diseno_real` en el mes. Vendido = pasa a Won en el mes (chatter). Devuelto = vuelve a Diseño desde etapa posterior. Novedad = mismo proyecto con otra letra.
- Efectividad (>40%) = vendidos/presentados. Eficiencia (<50%) = % presentados con novedad (no reproduce la hoja). Devueltos (<15%). Destacados (>50%) = `dis_destacado`/presentados. Proyectos IA (>30%) manual.

### Producción (hoja "Indicadores Area (Produccion)", Diego Robayo desde abr-2026)
- Solo tipos Producción y Prototipo Facturable; Borrador se ignora. Garantía = brief aprobado tipo Garantía, en su mes de creación.
- Proyecto ejecutado = mes de su PRIMERA factura de cliente contabilizada (vía analítica del plan).
- Costo real = apuntes analíticos netos de las cuentas hijas del plan, excluyendo categorías Venta y Servicio Exportacion.
- Cero garantías (≥100%) = (ejecutados − garantías)/ejecutados × 100.
- Proveedor de servicios (≥100%) = proveedores nuevos × 100 / `prov_por_mes`. Nuevo = primera factura de proveedor con analítica a proyectos.
- Eficiencia en el costo (ahorro ≥5%) = (proyectado − real)/proyectado × 100; solo proyectos con proyectado ≥ $100.000 y real > 0.
- Costo de garantías: informativo. Se muestra desde feb-2026 (proveedores desde mar-2026). No depende del selector de empresa.
