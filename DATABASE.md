# Base de datos

La base de datos se crea desde `database.py` y se guarda en SQLite

## Tablas de musica

| Tabla | Que guarda |
| --- | --- |
| `playlists` | ID, servidor, dueño, nombre, fecha, GIF, version y si muestra el avatar |
| `songs` | ID de cancion, playlist, titulo, enlace, autor, duracion y orden |
| `playlist_likes` | Que usuario dio like a que playlist y en que servidor |
| `likes` | Canciones favoritas de cada usuario |
| `profile_media` | Imagen del perfil por usuario y servidor |
| `user_quotes` | Texto del perfil por usuario y servidor |

Una playlist tiene muchas canciones, `songs.playlist_id` apunta a `playlists.id`

Una playlist tambien puede tener muchos likes, `playlist_likes.playlist_id` apunta a `playlists.id`

Al borrar una playlist se borran sus canciones y likes por la relacion `ON DELETE CASCADE`

El ID de usuario de Discord identifica al dueño, no se guarda su contraseña de Discord

En las tablas de musica ese ID es un identificador externo, no una llave foranea hacia `web_users`, porque tambien puede haber usuarios que solo usen Discord

`UNIQUE(guild_id,user_id,name)` evita repetir el nombre de una playlist del mismo dueño en el mismo servidor

La llave compuesta de `playlist_likes` evita que alguien de el mismo like varias veces

## Tablas de la web

- `web_users` guarda el usuario web, el hash de la contraseña, su Discord vinculado y si es administrador
- `web_sessions` guarda sesiones con vencimiento, relacionadas con `web_users`
- `link_codes` guarda los codigos temporales para vincular Discord, relacionados con `web_users`
- `app_state` guarda un numero de revision para detectar cambios
- `schema_history` registra las versiones de la estructura

Las contraseñas se guardan con scrypt y una sal aleatoria, no como texto normal

## Como se actualiza

La web manda una peticion a Python, Python valida al usuario y ejecuta la consulta SQL

Los comandos del bot usan la misma base de datos, no una copia distinta

Uso transacciones para que un cambio se guarde completo o no se guarde y consultas con parametros para los datos enviados por el usuario

Los triggers actualizan la revision y las versiones de playlists cuando cambian los registros, si dos personas editan una version vieja se rechaza el cambio para no sobrescribirla sin avisar

SQLite usa WAL para permitir lecturas mientras se procesan escrituras, sigue siendo una base de datos local con un escritor a la vez

La pestaña Database del administrador muestra las tablas reales del laboratorio, sus relaciones y registros para explicar esto en clase
