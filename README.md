# Laevatain

Este es mi proyecto de base de datos: un bot de musica para Discord con una pagina web para organizar playlists

La idea es poder crear una playlist desde Discord y verla en la pagina, o editarla desde la pagina y usarla despues en Discord

Esta es una copia publica para revisar el proyecto, no es la instalacion de mi servidor y no incluye el proyecto privado de Amiya

## Que tiene

- Playlists con un maximo de 50 canciones y 3 playlists por usuario y servidor
- Busqueda de canciones con Lavalink
- Favoritos y likes de playlists
- Arrastrar playlists para combinar canciones
- Editor de nombre, GIF y foto de perfil para mostrar la playlist con `/show playlist`
- Mini reproductor para controlar la musica que suena en Discord
- Vista de tablas, relaciones y registros solo para el administrador
- Modo de presentacion con perfiles de ejemplo

El audio sale por Discord, no por los parlantes del navegador

## Con que esta hecho

Uso Python para la logica, FastAPI para la pagina y discord.py para el bot

El frontend esta hecho con HTML, CSS y JavaScript, sin un framework de frontend

SQLite guarda las playlists y los usuarios en un archivo local, Wavelink conecta el bot con un servidor Lavalink v4 que se encarga del audio

Reutilice y adapte la logica de mi bot Amiya, pero Laevatain tiene su propio bot y su propia base de datos y no necesita ejecutar Amiya

## Como probar la pagina en local

Se necesita Python 3.13

```bash
git clone https://github.com/MelvinBProject/Laevatain.git
cd Laevatain
python -m venv .venv
```

En Windows PowerShell

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

En Linux

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Editar el archivo `.env` en privado

- `ADMIN_USERNAME`: nombre para entrar a la pagina
- `ADMIN_PASSWORD`: una contraseña nueva de al menos 12 caracteres
- `OWNER_DISCORD_ID`: ID del administrador en Discord, para una prueba solo web se puede usar un numero ficticio como `111111111111111111`
- Mantener `BOT_ENABLED=false` para revisar la pagina sin conectar Discord
- Mantener `DEMO_MODE=true` para la presentacion con un solo administrador

```bash
python main.py
```

Entrar a http://127.0.0.1:8088 con el usuario y contraseña elegidos

La base de datos se crea automaticamente en `data/laevatain.db`, no hay que crear las tablas a mano

El administrador se crea en el primer inicio, cambiar la contraseña en `.env` despues no cambia la de una base de datos que ya existe

Sin Discord y Lavalink se puede revisar la web y sus tablas, pero no buscar ni reproducir musica

## Conectar el bot

Crear una aplicacion propia en Discord Developer Portal e invitar su bot al servidor de pruebas con los permisos para comandos, mensajes y voz

Completar en el `.env` privado

```dotenv
DISCORD_TOKEN=
DISCORD_GUILD_ID=
OWNER_DISCORD_ID=
BOT_ENABLED=true
LAVALINK_URI=http://127.0.0.1:2333
LAVALINK_PASSWORD=
LAVALINK_IDENTIFIER=laevatain
```

Los campos vacios se completan con los datos de quien lo vaya a ejecutar, no hay credenciales compartidas

El repositorio incluye `compose.audio.yaml` y `lavalink/application.yml` para levantar un Lavalink v4 y un yt-cipher independientes, sin usar los servicios privados de mi servidor

Antes de iniciar, completar tambien `YOUTUBE_CIPHER_TOKEN` con otra contraseña nueva en el `.env`, las dos contraseñas de audio deben ser distintas y no deben ser las del servidor real

Con Docker instalado y encendido

```bash
docker compose -f compose.audio.yaml config --quiet
docker compose -f compose.audio.yaml up -d
docker compose -f compose.audio.yaml ps
python main.py
```

Lavalink queda disponible solo en `127.0.0.1:2333` para el programa Python, el cipher no publica ningun puerto y solo se comunica con Lavalink dentro de Docker

Para apagar solamente este entorno de audio

```bash
docker compose -f compose.audio.yaml stop
```

Esta plantilla no debe ejecutarse en la VPS de produccion, no se conecta a las redes de Amiya ni reemplaza sus contenedores

Los detalles y limites estan en [AUDIO.md](AUDIO.md)

Despues de reiniciar el programa, usar `/set channel` como propietario para elegir el canal de musica y entrar a un canal de voz

Algunos comandos son `/play`, `/pause`, `/resume`, `/skip`, `/stop`, `/queue`, `/seek`, `/playlist create`, `/playlist add`, `/playlist play`, `/show playlist`, `/likes`, `/profile` y `/version`

En modo demo solo el propietario controla el bot, para probar varias cuentas se desactiva `DEMO_MODE` y se activa `ALLOW_REGISTRATION`; cada cuenta puede vincular su Discord con el codigo de la pagina y `/link`

## Donde esta cada cosa

| Archivo | Para que sirve |
| --- | --- |
| `main.py` | Inicia la web y el bot |
| `config.py` | Lee la configuracion privada |
| `webapp.py` | Login y rutas que usa la pagina |
| `database.py` | Tablas, conexiones y sesiones |
| `library.py` | Reglas para editar playlists y canciones |
| `bot_runtime.py` | Conecta Discord con la web y Lavalink |
| `music.py` | Reproduccion y cola de canciones |
| `playlists.py` y `playlist_design.py` | Comandos de playlists y sus GIF |
| `channels.py`, `likes.py`, `social.py` | Canal de musica, favoritos y perfiles |
| `presentation.py` | Perfiles ficticios de la presentacion |
| `static/` | Pagina, estilos y botones |
| `tests/` | Pruebas automaticas |

## Como se guardan las canciones

No guardo el archivo de audio en SQLite, guardo el titulo, enlace, autor, duracion y posicion en la playlist

Cuando alguien quiere escucharla, el bot manda la busqueda o enlace a Lavalink y este intenta obtener el audio para reproducirlo en Discord

Hay un apoyo de yt-dlp para leer metadatos de playlists, no es el motor de reproduccion

La web y Discord trabajan con la misma base de datos, la pagina consulta cambios cada 3 segundos y actualiza lo que muestra

## Pruebas

```bash
python -m pytest -q
```

Las pruebas usan datos ficticios y bases temporales, no necesitan entrar a mi servidor ni conectarse a Discord

Revisan permisos, sesiones, playlists, limite de canciones, cambios simultaneos, controles del reproductor y validacion de GIF

## Despliegue y limites

Para la presentacion use una VPS Linux con Docker, un contenedor para Python y otro para HTTPS, con los datos guardados fuera del contenedor

El `Dockerfile` deja el proceso como usuario sin privilegios, pero no configura HTTPS ni publica puertos por si solo

Esta copia se inicia solo en localhost por defecto, no hay que abrirla a internet para revisarla

Un despliegue publico necesita HTTPS, configurar `DEPLOYMENT_MODE`, los hosts permitidos, almacenamiento persistente y un proxy de confianza que no deje acceder directamente al backend

La musica depende de Discord, Lavalink y las fuentes externas, encontrar una cancion no garantiza que YouTube permita reproducirla siempre

Es un proyecto de laboratorio, no un servicio con disponibilidad garantizada ni una plataforma de reproduccion independiente de Discord

## Que deje fuera por privacidad

No se incluyen tokens, contraseñas reales, archivos `.env`, IP de la VPS, claves SSH, certificados, registros, bases de datos reales ni archivos de otros servicios

Los usuarios y contraseñas que aparecen en `tests/` son datos de prueba y no sirven para iniciar sesion en una instalacion real

Tampoco se incluyen los GIF usados en la presentacion, la portada publica es un SVG simple y se pueden agregar GIF propios en `gifs/1.gif` y `gifs/3.gif` para el panel de Discord

No subir la carpeta `data/` ni el `.env` al hacer cambios
