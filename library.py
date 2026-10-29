from urllib.parse import urlsplit

from database import public_row


class LibraryError(Exception):
    def __init__(self, message, status=400):
        self.message = message
        self.status = status


def safe_uri(value):
    try:
        url = urlsplit(value)
        host = (url.hostname or '').lower()
        port = url.port
    except ValueError:
        raise LibraryError('Invalid link')
    allowed = ('youtube.com', 'youtu.be', 'soundcloud.com', 'bandcamp.com', 'vimeo.com', 'twitch.tv')
    if url.scheme != 'https' or url.username or url.password or port not in (None, 443) or not any(host == h or host.endswith('.' + h) for h in allowed):
        raise LibraryError('Use an HTTPS link from YouTube, SoundCloud, Bandcamp, Vimeo or Twitch')
    return value


class Library:
    def __init__(self, database):
        self.database = database

    def target(self, db, playlist_id, user_id=None, version=None):
        row = db.execute('SELECT * FROM playlists WHERE id=?', (playlist_id,)).fetchone()
        if not row or user_id is not None and row['user_id'] != user_id:
            raise LibraryError('You do not have access to this playlist', 404)
        if version is not None and row['version'] != version:
            raise LibraryError('This playlist changed in another window or in Discord. Reload before saving', 409)
        return row

    def playlists(self, user_id, community=False):
        with self.database.connect() as db:
            where = '' if community else 'WHERE p.user_id=?'
            rows = db.execute(f'SELECT p.*, (SELECT COUNT(*) FROM songs s WHERE s.playlist_id=p.id) AS song_count, (SELECT COUNT(*) FROM playlist_likes l WHERE l.playlist_id=p.id) AS like_count FROM playlists p {where} ORDER BY p.id DESC LIMIT 200', () if community else (user_id,)).fetchall()
            return [public_row(row) for row in rows]

    def detail(self, playlist_id):
        with self.database.connect() as db:
            target = self.target(db, playlist_id)
            result = public_row(target)
            result['songs'] = [public_row(row) for row in db.execute('SELECT * FROM songs WHERE playlist_id=? ORDER BY position,id', (playlist_id,))]
            return result

    def create(self, user_id, guild_id, name):
        name = name.strip()
        if not name or len(name) > 40:
            raise LibraryError('The name must contain between 1 and 40 characters')
        with self.database.connect(True) as db:
            count = db.execute('SELECT COUNT(*) FROM playlists WHERE user_id=? AND guild_id=?', (user_id, guild_id)).fetchone()[0]
            if count >= 3:
                raise LibraryError('You can create up to 3 playlists per server')
            cursor = db.execute('INSERT INTO playlists(user_id,guild_id,name) VALUES(?,?,?)', (user_id, guild_id, name))
            return cursor.lastrowid

    def rename(self, user_id, playlist_id, name, version):
        name = name.strip()
        if not name or len(name) > 40:
            raise LibraryError('The name must contain between 1 and 40 characters')
        with self.database.connect(True) as db:
            self.target(db, playlist_id, user_id, version)
            db.execute('UPDATE playlists SET name=?,version=version+1 WHERE id=?', (name, playlist_id))

    def delete(self, user_id, playlist_id, version):
        with self.database.connect(True) as db:
            self.target(db, playlist_id, user_id, version)
            db.execute('DELETE FROM playlists WHERE id=?', (playlist_id,))

    def add(self, user_id, playlist_id, songs, version):
        if not songs or len(songs) > 50:
            raise LibraryError('Select between 1 and 50 songs')
        with self.database.connect(True) as db:
            self.target(db, playlist_id, user_id, version)
            existing = {row[0] for row in db.execute('SELECT uri FROM songs WHERE playlist_id=?', (playlist_id,))}
            pending = []
            for song in songs:
                uri = safe_uri(song['uri'])
                if uri not in existing:
                    existing.add(uri)
                    pending.append(song)
            count = db.execute('SELECT COUNT(*) FROM songs WHERE playlist_id=?', (playlist_id,)).fetchone()[0]
            if count + len(pending) > 50:
                raise LibraryError('The playlist would exceed 50 songs. No changes were saved')
            position = db.execute('SELECT COALESCE(MAX(position),0) FROM songs WHERE playlist_id=?', (playlist_id,)).fetchone()[0]
            for index, song in enumerate(pending, position + 1):
                db.execute('INSERT INTO songs(playlist_id,title,uri,author,duration_ms,position) VALUES(?,?,?,?,?,?)', (playlist_id, song['title'], song['uri'], song.get('author', ''), song.get('duration_ms', 0), index))
            return len(pending)

    def reorder(self, user_id, playlist_id, ids, version):
        with self.database.connect(True) as db:
            self.target(db, playlist_id, user_id, version)
            current = {row[0] for row in db.execute('SELECT id FROM songs WHERE playlist_id=?', (playlist_id,))}
            if set(ids) != current or len(ids) != len(current):
                raise LibraryError('The order must include every song exactly once')
            for position, song_id in enumerate(ids, 1):
                db.execute('UPDATE songs SET position=? WHERE id=?', (position, song_id))

    def remove(self, user_id, playlist_id, ids, version):
        with self.database.connect(True) as db:
            self.target(db, playlist_id, user_id, version)
            for song_id in ids:
                if not db.execute('SELECT 1 FROM songs WHERE id=? AND playlist_id=?', (song_id, playlist_id)).fetchone():
                    raise LibraryError('A song does not belong to this playlist')
                db.execute('DELETE FROM songs WHERE id=?', (song_id,))

    def transfer(self, user_id, source, target, ids, move, source_version, target_version):
        if source == target:
            raise LibraryError('Choose a different playlist')
        with self.database.connect(True) as db:
            self.target(db, source, user_id, source_version)
            self.target(db, target, user_id, target_version)
            rows = db.execute('SELECT * FROM songs WHERE playlist_id=? ORDER BY position,id', (source,)).fetchall()
            if ids is not None:
                if not set(ids).issubset({r['id'] for r in rows}):
                    raise LibraryError('The selection contains songs from another playlist')
                rows = [r for r in rows if r['id'] in ids]
            existing = {r[0] for r in db.execute('SELECT uri FROM songs WHERE playlist_id=?', (target,))}
            pending = []
            for row in rows:
                if row['uri'] not in existing:
                    existing.add(row['uri'])
                    pending.append(row)
            total = db.execute('SELECT COUNT(*) FROM songs WHERE playlist_id=?', (target,)).fetchone()[0]
            if total + len(pending) > 50:
                raise LibraryError('The destination would exceed 50 songs. No changes were saved')
            position = db.execute('SELECT COALESCE(MAX(position),0) FROM songs WHERE playlist_id=?', (target,)).fetchone()[0]
            for index, row in enumerate(pending, position + 1):
                db.execute('INSERT INTO songs(playlist_id,title,uri,author,duration_ms,position) VALUES(?,?,?,?,?,?)', (target, row['title'], row['uri'], row['author'], row['duration_ms'], index))
            if move:
                for row in rows:
                    db.execute('DELETE FROM songs WHERE id=?', (row['id'],))
            return {'added': len(pending), 'duplicates': len(rows) - len(pending), 'moved': len(rows) if move else 0}

    def favorite(self, user_id, song_id):
        with self.database.connect(True) as db:
            song = db.execute('SELECT * FROM songs WHERE id=?', (song_id,)).fetchone()
            if not song:
                raise LibraryError('The song does not exist', 404)
            existing = db.execute('SELECT id FROM likes WHERE user_id=? AND uri=?', (user_id, song['uri'])).fetchone()
            if existing:
                db.execute('DELETE FROM likes WHERE id=?', (existing['id'],))
                return False
            db.execute('INSERT INTO likes(user_id,title,uri,author,duration_ms) VALUES(?,?,?,?,?)', (user_id, song['title'], song['uri'], song['author'] or '', song['duration_ms'] or 0))
            return True

    def like(self, user_id, playlist_id):
        with self.database.connect(True) as db:
            playlist = self.target(db, playlist_id)
            key = (playlist_id, playlist['guild_id'], user_id)
            existing = db.execute('SELECT 1 FROM playlist_likes WHERE playlist_id=? AND guild_id=? AND user_id=?', key).fetchone()
            if existing:
                db.execute('DELETE FROM playlist_likes WHERE playlist_id=? AND guild_id=? AND user_id=?', key)
            else:
                db.execute('INSERT INTO playlist_likes(playlist_id,guild_id,user_id) VALUES(?,?,?)', key)
            return not bool(existing)
