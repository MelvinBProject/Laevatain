# Lavalink y el cipher

Estas son las piezas externas que necesita Laevatain para reproducir musica

Python recibe el comando y Wavelink se comunica con Lavalink, el plugin youtube-source busca la cancion y usa yt-cipher cuando necesita resolver las firmas de la URL de audio, despues Lavalink envia el audio al canal de voz de Discord

El cipher ayuda con las firmas del reproductor, no quita restricciones de cuenta ni garantiza que YouTube acepte todas las peticiones

## Componentes incluidos

- Lavalink 4.2.2 Alpine
- youtube-source en la revision `f45bbb7aebfcbc1c553769e04af6cd43afa8b7c3`
- yt-cipher de kikkia

Las imagenes estan fijadas por digest a las versiones verificadas en la instalacion que se uso como referencia, el plugin usa una revision concreta de su repositorio de snapshots

No copie el codigo de esos proyectos ni sus binarios, Docker descarga sus imagenes y Lavalink descarga el plugin

La disponibilidad de esos archivos externos y los cambios de YouTube pueden hacer que esta configuracion necesite actualizarse

## Credenciales

`LAVALINK_PASSWORD` protege la conexion del bot con Lavalink y `YOUTUBE_CIPHER_TOKEN` protege el cipher

Cada persona debe crear sus propias contraseñas y dejarlas en su `.env`, nunca en los archivos YAML ni en GitHub

OAuth y los PO tokens son opcionales en la plantilla y vienen vacios, si una fuente exige autenticacion se necesitan credenciales propias compatibles con el plugin

El cliente TV necesita autenticacion, los demas clientes pueden servir de alternativa pero no se garantiza que todos funcionen en cualquier red

Para usar un refresh token propio se configura `YOUTUBE_OAUTH_ENABLED=true` y `YOUTUBE_OAUTH_REFRESH_TOKEN` en privado, la plantilla no inicia el flujo interactivo de OAuth

Los nombres `YOUTUBE_PO_TOKEN` y `YOUTUBE_PO_VISITOR_DATA` tambien se pasan desde el `.env` si se necesitan, no son contraseñas para iniciar sesion en la web

No compartir registros sin revisarlos porque pueden contener datos de autenticacion de los servicios externos

## Separacion

El proyecto Docker se llama `laevatain-audio-lab`, tiene una red propia y no monta el socket de Docker ni archivos de otros bots

El puerto 2333 solo se abre en localhost y el 8001 del cipher no se publica, los procesos usan los usuarios sin privilegios de sus imagenes oficiales

Si el puerto 2333 ya esta ocupado se debe elegir otro puerto local en Compose y cambiar `LAVALINK_URI`, no detener el servicio que ya lo use

La pagina sigue arrancando con `python main.py`, estos dos contenedores solo agregan el audio y no abren la web a internet

## Fuentes de estos componentes

- [Lavalink](https://github.com/lavalink-devs/Lavalink)
- [Plugin youtube-source](https://github.com/lavalink-devs/youtube-source)
- [yt-cipher](https://github.com/kikkia/yt-cipher)

Son proyectos externos con sus propias licencias y condiciones, no desarrollados por mi
