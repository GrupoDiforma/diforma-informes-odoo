# Metas compartidas (Apps Script)
1. Abrir la hoja "Metas Dashboard de Indicadores" > Extensiones > Apps Script. Borrar el contenido y pegar `Codigo.gs`. Guardar.
2. Configuración del proyecto (engranaje) > Propiedades de la secuencia de comandos > Agregar propiedad: `PIN` = la clave de edición que se quiera.
3. Implementar > Nueva implementación > tipo "Aplicación web". Ejecutar como: Yo. Quién tiene acceso: Cualquier persona. Autorizar los permisos.
4. Copiar la URL que termina en `/exec` y ponerla en `nube.json` (`{"url": "..."}`), subir al repo.
Cualquiera ve las metas guardadas; solo quien tenga la clave puede cambiarlas. Si se cambia el código hay que crear una nueva versión de la implementación.
