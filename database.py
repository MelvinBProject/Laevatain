import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager

PUBLIC_TABLES = ('playlists', 'songs', 'playlist_likes', 'likes', 'profile_media', 'user_quotes')
SCHEMA = [
    "CREATE TABLE IF NOT EXISTS playlists (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL COLLATE NOCASE, created_at TEXT NOT NULL DEFAULT (datetime('now')), image_path TEXT, version INTEGER NOT NULL DEFAULT 0, UNIQUE(guild_id,user_id,name))",
    "CREATE TABLE IF NOT EXISTS songs (id INTEGER PRIMARY KEY AUTOINCREMENT, playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE, title TEXT NOT NULL, uri TEXT NOT NULL, author TEXT DEFAULT '', duration_ms INTEGER DEFAULT 0, position INTEGER NOT NULL DEFAULT 0, added_at TEXT NOT NULL DEFAULT (datetime('now')))",
    "CREATE TABLE IF NOT EXISTS playlist_likes (playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, liked_at TEXT NOT NULL DEFAULT (datetime('now')), PRIMARY KEY(playlist_id,guild_id,user_id))",
    "CREATE TABLE IF NOT EXISTS likes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL, uri TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', duration_ms INTEGER NOT NULL DEFAULT 0, liked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,uri))",
    'CREATE TABLE IF NOT EXISTS profile_media (guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,image_path TEXT NOT NULL,PRIMARY KEY(guild_id,user_id))',
    "CREATE TABLE IF NOT EXISTS user_quotes (guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,quote TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT (datetime('now')),PRIMARY KEY(guild_id,user_id))",
    'CREATE TABLE IF NOT EXISTS web_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL, discord_id INTEGER UNIQUE, is_admin INTEGER NOT NULL DEFAULT 0)',
    'CREATE TABLE IF NOT EXISTS web_sessions (token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,csrf TEXT NOT NULL,expires INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS link_codes (code_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,expires INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS app_state (id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL DEFAULT 0)',
    "CREATE TABLE IF NOT EXISTS schema_history (id INTEGER PRIMARY KEY AUTOINCREMENT,version INTEGER NOT NULL,description TEXT NOT NULL,applied_at TEXT NOT NULL DEFAULT (datetime('now')))",
    'CREATE INDEX IF NOT EXISTS idx_pl ON playlists(guild_id,user_id)',
    'CREATE INDEX IF NOT EXISTS idx_songs ON songs(playlist_id,position)',
    'CREATE INDEX IF NOT EXISTS idx_likes ON playlist_likes(playlist_id)',
    'CREATE INDEX IF NOT EXISTS idx_likes_user ON likes(user_id,liked_at DESC)',
]


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f'{salt}:{digest}'


def verify_password(password, saved):
    return hmac.compare_digest(password_hash(password, saved.split(':')[0]), saved)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(row):
    return dict(id=str(row['id']), username=row['username'], discord_id=str(row['discord_id']) if row['discord_id'] else None, is_admin=bool(row['is_admin']), library_id=str(row['discord_id'] or -row['id']))


def public_row(row):
    return {k: str(v) if v is not None and (k == 'id' or k.endswith('_id')) else v for k, v in dict(row).items()}


class Database:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self, settings):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            for statement in SCHEMA:
                db.execute(statement)
            if not db.execute('SELECT 1 FROM schema_history WHERE version=1').fetchone():
                db.execute('INSERT INTO schema_history(version,description) VALUES(1,?)', ('Base local creada con las seis tablas de Amiya y las tablas de acceso web',))
            columns = {row[1] for row in db.execute('PRAGMA table_info(playlists)')}
            if 'show_avatar' not in columns:
                db.execute('ALTER TABLE playlists ADD COLUMN show_avatar INTEGER NOT NULL DEFAULT 1')
                db.execute('INSERT INTO schema_history(version,description) VALUES(2,?)', ('Playlist embed avatar visibility',))
            db.execute('INSERT OR IGNORE INTO app_state(id,revision) VALUES(1,0)')
            for table in PUBLIC_TABLES:
                for action in ('INSERT', 'UPDATE', 'DELETE'):
                    db.execute(f'CREATE TRIGGER IF NOT EXISTS rev_{table}_{action} AFTER {action} ON {table} BEGIN UPDATE app_state SET revision=revision+1 WHERE id=1; END')
            for action, reference in (('INSERT', 'NEW'), ('UPDATE', 'NEW'), ('DELETE', 'OLD')):
                db.execute(f'CREATE TRIGGER IF NOT EXISTS song_version_{action} AFTER {action} ON songs BEGIN UPDATE playlists SET version=version+1 WHERE id={reference}.playlist_id; END')
            db.execute('CREATE TRIGGER IF NOT EXISTS playlist_version AFTER UPDATE OF name,image_path ON playlists WHEN NEW.version=OLD.version BEGIN UPDATE playlists SET version=version+1 WHERE id=NEW.id; END')
            admin = db.execute('SELECT * FROM web_users WHERE is_admin=1').fetchone()
            if not admin:
                if len(settings.admin_password) < 12:
                    raise RuntimeError('Define ADMIN_PASSWORD en tu .env con al menos 12 caracteres')
                db.execute('INSERT INTO web_users(username,password_hash,discord_id,is_admin) VALUES(?,?,?,1)', (settings.admin_username, password_hash(settings.admin_password), settings.owner_id))

    def revision(self):
        with self.connect() as db:
            return db.execute('SELECT revision FROM app_state WHERE id=1').fetchone()[0]

    def session(self, token):
        with self.connect() as db:
            row = db.execute('SELECT u.*,s.csrf FROM web_sessions s JOIN web_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?', (token_hash(token), time.time())).fetchone()
            return row

    def link(self, code, discord_id):
        with self.connect(True) as db:
            row = db.execute('SELECT * FROM link_codes WHERE code_hash=? AND expires>?', (token_hash(code), time.time())).fetchone()
            if not row:
                raise ValueError('El codigo no existe o ya vencio')
            account = db.execute('SELECT * FROM web_users WHERE id=?', (row['user_id'],)).fetchone()
            if account['discord_id']:
                raise ValueError('Esta cuenta ya tiene Discord vinculado')
            if db.execute('SELECT 1 FROM web_users WHERE discord_id=?', (discord_id,)).fetchone():
                raise ValueError('Ese Discord ya esta vinculado a otra cuenta')
            for table in PUBLIC_TABLES:
                db.execute(f'UPDATE {table} SET user_id=? WHERE user_id=?', (discord_id, -row['user_id'])) if table != 'songs' else None
            db.execute('UPDATE web_users SET discord_id=? WHERE id=?', (discord_id, row['user_id']))
            db.execute('DELETE FROM link_codes WHERE user_id=?', (row['user_id'],))
            return account['username']
