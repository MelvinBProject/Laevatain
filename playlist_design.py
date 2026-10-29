import base64
import io
import uuid
import warnings
from pathlib import Path

from PIL import Image

from library import LibraryError

MAX_GIF_BYTES = 8 * 1024 * 1024


def validate_gif(data):
    if not data or len(data) > MAX_GIF_BYTES:
        raise LibraryError('Choose a GIF up to 8 MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != 'GIF':
                    raise ValueError('Not a GIF')
                pixels = 0
                for frame in range(1001):
                    try:
                        image.seek(frame)
                    except EOFError:
                        break
                    if frame == 1000 or image.width * image.height > 4000000:
                        raise ValueError('GIF too large')
                    pixels += image.width * image.height
                    if pixels > 150000000:
                        raise ValueError('GIF too complex')
                    image.load()
    except Exception:
        raise LibraryError('Use a valid GIF with up to 1000 frames and a reasonable resolution')
    return data


def media_path(database, raw):
    if not raw:
        return None
    root = (database.path.parent / 'playlist_media').resolve()
    path = Path(raw).resolve()
    return path if path.is_relative_to(root) and path.suffix.lower() == '.gif' and path.is_file() else None


def save_design(library, user_id, playlist_id, body):
    with library.database.connect() as db:
        library.target(db, playlist_id, user_id, body.version)
    name = body.name.strip()
    if not name:
        raise LibraryError('Enter a playlist name')
    data = None
    if body.gif_base64:
        try:
            data = base64.b64decode(body.gif_base64, validate=True)
        except ValueError:
            raise LibraryError('Invalid GIF upload')
        validate_gif(data)
    created = None
    try:
        with library.database.connect(True) as db:
            row = library.target(db, playlist_id, user_id, body.version)
            image_path = row['image_path']
            if body.remove_gif:
                image_path = None
            if data:
                folder = library.database.path.parent / 'playlist_media' / str(row['guild_id']) / str(user_id)
                folder.mkdir(parents=True, exist_ok=True)
                created = folder / f'{playlist_id}-{uuid.uuid4().hex}.gif'
                created.write_bytes(data)
                image_path = str(created)
            db.execute('UPDATE playlists SET name=?,image_path=?,show_avatar=?,version=version+1 WHERE id=?', (name,image_path,int(body.show_avatar),playlist_id))
    except Exception:
        if created:
            created.unlink(missing_ok=True)
        raise
    return library.detail(playlist_id)
