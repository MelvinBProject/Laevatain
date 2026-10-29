import asyncio
import hmac
import re
import secrets
import sqlite3
import time
from dataclasses import replace
from collections import defaultdict, deque
from contextlib import asynccontextmanager
import os
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from config import ROOT
from database import Database, PUBLIC_TABLES, password_hash, public_row, public_user, token_hash, verify_password
from library import Library, LibraryError, safe_uri


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r'^[A-Za-z0-9_]+$')
    password: str = Field(min_length=10, max_length=128)


class Name(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    version: int = 0


class Song(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    uri: str = Field(min_length=8, max_length=2000)
    author: str = Field(default='', max_length=200)
    duration_ms: int = Field(default=0, ge=0, le=86400000)


class AddSongs(BaseModel):
    songs: list[Song] = Field(min_length=1, max_length=50)
    version: int


class Selection(BaseModel):
    ids: list[int] = Field(max_length=50)
    version: int


class Transfer(BaseModel):
    source: int
    target: int
    ids: list[int] | None = Field(default=None, max_length=50)
    move: bool = False
    source_version: int
    target_version: int


class Query(BaseModel):
    query: str = Field(min_length=1, max_length=1000)


class PlaylistDesign(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    show_avatar: bool = True
    version: int
    gif_base64: str | None = Field(default=None, max_length=11184812)
    remove_gif: bool = False


class PlaybackControl(BaseModel):
    action: Literal['pause', 'resume', 'skip', 'seek']
    identifier: str = Field(min_length=1, max_length=500)
    position_ms: int = Field(default=0, ge=0, le=86400000)


def create_app(settings, run_bot=True):
    database = Database(settings.db_path)
    library = Library(database)
    runtime = {'bot': None, 'error': None}
    attempts = defaultdict(deque)

    async def bot_worker(bot):
        import discord
        from dotenv import dotenv_values
        from bot_runtime import LaevatainBot
        while True:
            try:
                runtime['bot'] = bot
                runtime['error'] = None
                async with bot:
                    await bot.start(bot.settings.discord_token)
            except asyncio.CancelledError:
                raise
            except discord.LoginFailure:
                runtime['error'] = 'Discord rejected the saved token. Update DISCORD_TOKEN in your private .env. Automatic retry in 60 seconds.'
            except discord.Forbidden:
                runtime['error'] = 'Discord denied access. Check the lab server invitation and application command permissions.'
            except Exception as exc:
                runtime['error'] = f'Discord connection unavailable ({type(exc).__name__}). Automatic retry in 60 seconds.'
            await asyncio.sleep(60)
            private = dotenv_values(ROOT / '.env')
            token = (private.get('DISCORD_TOKEN') or settings.discord_token).strip()
            bot = LaevatainBot(replace(settings, discord_token=token), database)

    @asynccontextmanager
    async def lifespan(app):
        database.initialize(settings)
        if settings.demo_mode:
            from presentation import seed_profiles
            seed_profiles(database, settings.discord_guild_id or 0)
        task = None
        if run_bot and settings.bot_enabled:
            if not settings.discord_token or not settings.discord_guild_id:
                runtime['error'] = 'Set the new token and lab server ID first'
            else:
                from bot_runtime import LaevatainBot
                runtime['bot'] = LaevatainBot(settings, database)
                task = asyncio.create_task(bot_worker(runtime['bot']))
        yield
        if runtime['bot']:
            await runtime['bot'].close()
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title='Laevatain local', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.database = database
    app.state.runtime = runtime
    hosts = ['127.0.0.1', 'localhost', '[::1]', 'testserver']
    if settings.deployment_mode:
        hosts.extend(h.strip() for h in os.getenv('WEB_ALLOWED_HOSTS', '').split(',') if h.strip())
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware('http')
    async def security(request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin != f'{request.url.scheme}://{request.headers.get("host")}':
                return JSONResponse({'detail': 'Origin not allowed'}, status_code=403)
            try:
                content_length = int(request.headers.get('content-length', 0))
            except ValueError:
                return JSONResponse({'detail': 'Invalid request'}, status_code=400)
            design_upload = bool(re.fullmatch(r'/api/playlists/[0-9]+/appearance', request.url.path))
            if content_length > (12000000 if design_upload else 150000):
                return JSONResponse({'detail': 'Request too large'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://cdn.discordapp.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(LibraryError)
    async def library_error(request, exc):
        return JSONResponse({'detail': exc.message}, status_code=exc.status)

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request, exc):
        return JSONResponse({'detail': 'That name or record already exists. No changes were saved'}, status_code=409)

    def current_user(request: Request):
        row = database.session(request.cookies.get('laevatain_session', ''))
        if row is None:
            raise HTTPException(401, 'Sign in first')
        if settings.demo_mode and (not row['is_admin'] or row['discord_id'] != settings.owner_id):
            raise HTTPException(403, 'This local presentation only allows the owner account')
        if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(request.headers.get('x-csrf-token', ''), row['csrf']):
            raise HTTPException(403, 'Your session could not validate this change. Reload the page')
        return row

    def admin_user(user=Depends(current_user)):
        if not user['is_admin']:
            raise HTTPException(403, 'This view is for the administrator only')
        return user

    def identity(user):
        return user['discord_id'] or -user['id']

    def throttle(request):
        key = request.client.host if request.client else 'local'
        now = time.monotonic()
        entries = attempts[key]
        while entries and entries[0] < now - 60:
            entries.popleft()
        if len(entries) >= 10:
            raise HTTPException(429, 'Wait a minute before trying again')
        entries.append(now)

    def open_session(user, response):
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        with database.connect(True) as db:
            db.execute('DELETE FROM web_sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO web_sessions VALUES(?,?,?,?)', (token_hash(token), user['id'], csrf, int(time.time())+28800))
        response.set_cookie('laevatain_session', token, httponly=True, secure=settings.deployment_mode, samesite='strict', max_age=28800, path='/')
        return {'user': public_user(user), 'csrf': csrf}

    @app.post('/api/login')
    def login(body: Credentials, request: Request, response: Response):
        throttle(request)
        with database.connect() as db:
            user = db.execute('SELECT * FROM web_users WHERE username=?', (body.username,)).fetchone()
        if not user or not verify_password(body.password, user['password_hash']):
            raise HTTPException(401, 'Incorrect username or password')
        if settings.demo_mode and (not user['is_admin'] or user['discord_id'] != settings.owner_id):
            raise HTTPException(403, 'This local presentation only allows the owner account')
        return open_session(user, response)

    @app.post('/api/register')
    def register(body: Credentials, request: Request, response: Response):
        throttle(request)
        if settings.demo_mode or not settings.allow_registration:
            raise HTTPException(403, 'Registration is closed')
        with database.connect(True) as db:
            cursor = db.execute('INSERT INTO web_users(username,password_hash) VALUES(?,?)', (body.username, password_hash(body.password)))
            user = db.execute('SELECT * FROM web_users WHERE id=?', (cursor.lastrowid,)).fetchone()
        return open_session(user, response)

    @app.post('/api/logout')
    def logout(request: Request, response: Response, user=Depends(current_user)):
        with database.connect(True) as db:
            db.execute('DELETE FROM web_sessions WHERE token_hash=?', (token_hash(request.cookies.get('laevatain_session','')),))
        response.delete_cookie('laevatain_session')
        return {'ok': True}

    @app.get('/api/me')
    def me(user=Depends(current_user)):
        return {'user': public_user(user), 'csrf': user['csrf']}

    @app.get('/api/status')
    def status(user=Depends(current_user)):
        bot = runtime['bot']
        state = bot.status(user['discord_id']) if bot else {'bot': 'disabled', 'audio': False, 'version': '0.1.0-local', 'current': None}
        state['notice'] = runtime['error'] or state.get('playback_error')
        if runtime['error']:
            state['bot'] = 'error'
        state['demo_mode'] = settings.demo_mode
        state['revision'] = database.revision()
        return state

    @app.get('/api/playlists')
    def playlists(community: bool = False, user=Depends(current_user)):
        items = library.playlists(identity(user), community)
        from presentation import profile_name
        for item in items:
            item['owner_name'], item['is_demo'] = profile_name(int(item['user_id']), settings.owner_id)
        return items

    @app.get('/api/public-settings')
    def public_settings():
        return {'registration': settings.allow_registration and not settings.demo_mode, 'demo': settings.demo_mode}

    @app.get('/api/admin/overview')
    def overview(user=Depends(admin_user)):
        from presentation import profile_name
        with database.connect() as db:
            rows = db.execute('SELECT p.user_id,COUNT(DISTINCT p.id) AS playlists,COUNT(s.id) AS songs FROM playlists p LEFT JOIN songs s ON s.playlist_id=p.id GROUP BY p.user_id ORDER BY p.user_id DESC').fetchall()
            profiles = []
            for row in rows:
                name, demo = profile_name(row['user_id'], settings.owner_id)
                profiles.append(dict(user_id=str(row['user_id']), name=name, demo=demo, playlists=row['playlists'], songs=row['songs']))
        return {'profiles': profiles, 'revision': database.revision(), 'sync_interval': 3}

    @app.post('/api/playlists')
    def create_playlist(body: Name, user=Depends(current_user)):
        playlist_id = library.create(identity(user), settings.discord_guild_id or 0, body.name)
        return library.detail(playlist_id)

    @app.get('/api/playlists/{playlist_id}')
    def playlist_detail(playlist_id: int, user=Depends(current_user)):
        return library.detail(playlist_id)

    @app.patch('/api/playlists/{playlist_id}')
    def rename_playlist(playlist_id: int, body: Name, user=Depends(current_user)):
        library.rename(identity(user), playlist_id, body.name, body.version)
        return library.detail(playlist_id)

    @app.put('/api/playlists/{playlist_id}/appearance')
    def update_design(playlist_id: int, body: PlaylistDesign, user=Depends(current_user)):
        from playlist_design import save_design
        return save_design(library, identity(user), playlist_id, body)

    @app.get('/api/playlists/{playlist_id}/art')
    def playlist_art(playlist_id: int, user=Depends(current_user)):
        from playlist_design import media_path
        item = library.detail(playlist_id)
        path = media_path(database, item.get('image_path'))
        if not path:
            raise HTTPException(404, 'No GIF saved')
        return FileResponse(path, media_type='image/gif')

    @app.get('/api/playlists/{playlist_id}/preview')
    async def playlist_preview(playlist_id: int, user=Depends(current_user)):
        from presentation import profile_name
        item = library.detail(playlist_id)
        owner_id = int(item['user_id'])
        owner_name, _ = profile_name(owner_id, settings.owner_id)
        avatar = None
        bot = runtime['bot']
        guild = bot.get_guild(settings.discord_guild_id) if bot else None
        member = guild.get_member(owner_id) if guild else None
        if not member and bot and owner_id > 0:
            member = bot.get_user(owner_id)
            if not member:
                try:
                    member = await asyncio.wait_for(bot.fetch_user(owner_id), 5)
                except Exception:
                    pass
        if member:
            owner_name = member.display_name
            avatar = str(member.display_avatar.url)
        return {'owner_name':owner_name, 'avatar_url':avatar}

    @app.delete('/api/playlists/{playlist_id}')
    def delete_playlist(playlist_id: int, version: int, user=Depends(current_user)):
        library.delete(identity(user), playlist_id, version)
        return {'ok': True}

    @app.post('/api/playlists/{playlist_id}/songs')
    def add_songs(playlist_id: int, body: AddSongs, user=Depends(current_user)):
        return {'added': library.add(identity(user), playlist_id, [s.model_dump() for s in body.songs], body.version)}

    @app.put('/api/playlists/{playlist_id}/order')
    def order_songs(playlist_id: int, body: Selection, user=Depends(current_user)):
        library.reorder(identity(user), playlist_id, body.ids, body.version)
        return {'ok': True}

    @app.post('/api/playlists/{playlist_id}/remove')
    def remove_songs(playlist_id: int, body: Selection, user=Depends(current_user)):
        library.remove(identity(user), playlist_id, body.ids, body.version)
        return {'ok': True}

    @app.post('/api/transfer')
    def transfer(body: Transfer, user=Depends(current_user)):
        return library.transfer(identity(user), body.source, body.target, body.ids, body.move, body.source_version, body.target_version)

    @app.post('/api/playlists/{playlist_id}/like')
    def like_playlist(playlist_id: int, user=Depends(current_user)):
        return {'liked': library.like(identity(user), playlist_id)}

    @app.post('/api/songs/{song_id}/favorite')
    def favorite_song(song_id: int, user=Depends(current_user)):
        return {'liked': library.favorite(identity(user), song_id)}

    @app.get('/api/favorites')
    def favorites(user=Depends(current_user)):
        with database.connect() as db:
            return [public_row(r) for r in db.execute('SELECT * FROM likes WHERE user_id=? ORDER BY liked_at DESC,id DESC LIMIT 500', (identity(user),))]

    @app.delete('/api/favorites/{favorite_id}')
    def remove_favorite(favorite_id: int, user=Depends(current_user)):
        with database.connect(True) as db:
            db.execute('DELETE FROM likes WHERE id=? AND user_id=?', (favorite_id, identity(user)))
        return {'ok': True}

    @app.post('/api/link-code')
    def link_code(user=Depends(current_user)):
        if user['discord_id']:
            raise HTTPException(409, 'This account is already linked to Discord')
        code = secrets.token_urlsafe(18)
        with database.connect(True) as db:
            db.execute('DELETE FROM link_codes WHERE user_id=? OR expires<?', (user['id'], time.time()))
            db.execute('INSERT INTO link_codes VALUES(?,?,?)', (token_hash(code), user['id'], int(time.time())+600))
        return {'code': code, 'expires_in': 600}

    @app.post('/api/resolve')
    async def resolve(body: Query, user=Depends(current_user)):
        bot = runtime['bot']
        if not bot or not bot.status()['audio']:
            raise HTTPException(503, 'Lavalink is unavailable. Please try searching again shortly')
        if '://' in body.query:
            safe_uri(body.query)
        from music import search
        try:
            tracks = await asyncio.wait_for(search(body.query), 30)
            return [dict(title=t.title, uri=t.uri, author=t.author or '', duration_ms=t.length or 0) for t in tracks[:50] if t.uri]
        except Exception:
            raise HTTPException(502, 'The source could not resolve the search. Try another link')

    @app.post('/api/player/control')
    async def control_player(body: PlaybackControl, user=Depends(current_user)):
        if not user['discord_id']:
            raise HTTPException(400, 'Link Discord in My profile first')
        bot = runtime['bot']
        if not bot:
            raise HTTPException(503, 'Laevatain is offline')
        try:
            await asyncio.wait_for(bot.control_from_web(user['discord_id'], body.action, body.identifier, body.position_ms), 12)
            return {'ok': True}
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception:
            raise HTTPException(502, 'Playback control failed. Refresh and try again')

    @app.post('/api/playlists/{playlist_id}/play')
    async def play(playlist_id: int, user=Depends(current_user)):
        if not user['discord_id']:
            raise HTTPException(400, 'Link Discord in My profile first')
        bot = runtime['bot']
        if not bot:
            raise HTTPException(503, 'The lab bot is offline')
        with database.connect() as db:
            library.target(db, playlist_id, identity(user))
        try:
            count = await asyncio.wait_for(bot.play_from_web(user['discord_id'], library.detail(playlist_id)), 100)
            return {'queued': count}
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception:
            raise HTTPException(502, 'Could not start audio. Check Lavalink and the voice channel')

    @app.get('/api/admin/schema')
    def schema(user=Depends(admin_user)):
        with database.connect() as db:
            tables = []
            for name in PUBLIC_TABLES:
                sql = db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', ('table', name)).fetchone()[0]
                tables.append({'name': name, 'sql': sql, 'columns': [dict(r) for r in db.execute(f'PRAGMA table_info("{name}")')], 'foreign_keys': [dict(r) for r in db.execute(f'PRAGMA foreign_key_list("{name}")')], 'count': db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0], 'rows': [public_row(r) for r in db.execute(f'SELECT * FROM "{name}" LIMIT 8')]})
            history = [dict(r) for r in db.execute('SELECT * FROM schema_history ORDER BY id')]
            return {'tables': tables, 'history': history, 'notice': 'These are real tables. The animation only explains their creation and does not execute SQL. Session and password tables are hidden.'}

    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    app.mount('/gifs', StaticFiles(directory=ROOT / 'gifs'), name='gifs')

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'static' / 'index.html')

    return app
