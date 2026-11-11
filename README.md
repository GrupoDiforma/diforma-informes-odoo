# Dashboard de ventas - Grupo Diforma (Odoo 19)

Dashboard interactivo de ventas facturadas (USD y COP) e indicadores del area comercial.
GitHub Actions consulta Odoo (solo lectura) cada 6 horas y publica el resultado en GitHub Pages.

**La clave de Odoo NO esta en este repositorio.** Se guarda como *Secrets* de GitHub
(Settings > Secrets and variables > Actions): `ODOO_URL`, `ODOO_DB`, `ODOO_KEY`.

## Metas
Las metas iniciales estan en `metas_comercial.json`. En el dashboard se pueden editar con el boton "Editar metas"
(se guardan solo en el navegador de quien las edita).

## Uso local
```
pip install -r requirements.txt
# crear conexion.txt (no se sube al repo) con ODOO_URL=, ODOO_DB=, ODOO_KEY=
python dashboard_anual.py --anio 2026
```
