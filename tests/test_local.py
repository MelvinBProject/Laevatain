import asyncio
import io
import sqlite3
import sys
import tokenize
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import load_settings
from database import Database, PUBLIC_TABLES
from library import Library, LibraryError, safe_uri
from webapp import create_app


@pytest.fixture
def settings(tmp_path):
    return replace(load_settings(), db_path=tmp_path / 'test.db', owner_id=111111111111111111, admin_username='kur', admin_password='admin-test-password-2026', bot_enabled=False, allow_registration=True, discord_guild_id=123456789012345678, demo_mode=False, discord_token='', lavalink_password='')


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, run_bot=False)) as c:
        yield c


def login(client):
    response = client.post('/api/login', json={'username':'kur','password':'admin-test-password-2026'})
    assert response.status_code == 200
    client.headers['X-CSRF-Token'] = response.json()['csrf']
    return response.json()['user']


def song(number=1):
    return {'title': f'Cancion {number}', 'uri': f'https://www.youtube.com/watch?v=test{number:07}', 'author':'Artista', 'duration_ms':1000}


def create(client, name='Noche'):
    r = client.post('/api/playlists', json={'name':name})
    assert r.status_code == 200, r.text
    return r.json()


def add(client, playlist, count=2):
    r = client.post(f'/api/playlists/{playlist["id"]}/songs',json={'songs':[song(i) for i in range(count)],'version':playlist['version']})
    assert r.status_code == 200, r.text
    return client.get(f'/api/playlists/{playlist["id"]}').json()


def test_schema_is_admin_only_and_contains_actual_keys(client):
    assert client.get('/api/admin/schema').status_code == 401
    login(client)
    result = client.get('/api/admin/schema').json()
    assert {t['name'] for t in result['tables']} == set(PUBLIC_TABLES)
    assert sum(len(t['foreign_keys']) for t in result['tables']) == 2
    assert 'password_hash' not in str(result)
    assert 'web_sessions' not in str(result)
    assert [entry['version'] for entry in result['history']] == [1, 2]
    client.post('/api/logout')
    r = client.post('/api/register',json={'username':'alice','password':'alice-password-12345'})
    client.headers['X-CSRF-Token'] = r.json()['csrf']
    assert r.json()['user']['is_admin'] is False
    assert client.get('/api/admin/schema').status_code == 403


def test_csrf_and_cross_origin(client):
    login(client)
    csrf = client.headers.pop('X-CSRF-Token')
    assert client.post('/api/playlists',json={'name':'Noche'}).status_code == 403
    client.headers['X-CSRF-Token'] = csrf
    assert client.post('/api/playlists',json={'name':'Noche'},headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.get('/api/status',headers={'host':'evil.example'}).status_code == 400


def test_playlist_crud_and_id_precision(client):
    user = login(client)
    p = create(client)
    assert p['user_id'] == str(111111111111111111)
    assert user['library_id'] == p['user_id']
    assert p['guild_id'] == '123456789012345678'
    p = add(client,p)
    ids = [s['id'] for s in p['songs']][::-1]
    assert client.put(f'/api/playlists/{p["id"]}/order',json={'ids':ids,'version':p['version']}).status_code == 200
    p = client.get(f'/api/playlists/{p["id"]}').json()
    assert [s['id'] for s in p['songs']] == ids
    assert client.patch(f'/api/playlists/{p["id"]}',json={'name':'Editada','version':p['version']}).status_code == 200
    p = client.get(f'/api/playlists/{p["id"]}').json()
    assert p['name'] == 'Editada'
    assert client.delete(f'/api/playlists/{p["id"]}?version={p["version"]}').status_code == 200
    assert client.get(f'/api/playlists/{p["id"]}').status_code == 404


def test_other_account_cannot_edit_or_transfer(client):
    login(client)
    p = add(client,create(client))
    client.post('/api/logout')
    r = client.post('/api/register',json={'username':'other','password':'other-password-12345'})
    client.headers['X-CSRF-Token'] = r.json()['csrf']
    own = create(client,'Otra')
    assert client.patch(f'/api/playlists/{p["id"]}',json={'name':'Robada','version':p['version']}).status_code == 404
    assert client.post('/api/transfer',json={'source':p['id'],'target':own['id'],'source_version':p['version'],'target_version':own['version']}).status_code == 404
    assert client.post(f'/api/playlists/{p["id"]}/remove',json={'ids':[p['songs'][0]['id']],'version':p['version']}).status_code == 404


def test_merge_deduplicates_and_preserves_source(client):
    login(client)
    source = add(client,create(client,'Origen'),3)
    target = add(client,create(client,'Destino'),1)
    r = client.post('/api/transfer',json={'source':source['id'],'target':target['id'],'source_version':source['version'],'target_version':target['version']})
    assert r.json() == {'added':2,'duplicates':1,'moved':0}
    assert len(client.get(f'/api/playlists/{source["id"]}').json()['songs']) == 3
    assert len(client.get(f'/api/playlists/{target["id"]}').json()['songs']) == 3


def test_move_selected_tracks(client):
    login(client)
    source = add(client,create(client,'Origen'),3)
    target = create(client,'Destino')
    r = client.post('/api/transfer',json={'source':source['id'],'target':target['id'],'ids':[source['songs'][0]['id']],'move':True,'source_version':source['version'],'target_version':target['version']})
    assert r.status_code == 200
    assert r.json()['moved'] == 1
    assert len(client.get(f'/api/playlists/{source["id"]}').json()['songs']) == 2
    assert len(client.get(f'/api/playlists/{target["id"]}').json()['songs']) == 1


def test_conflicts_and_invalid_order_do_not_overwrite(client):
    login(client)
    old = create(client)
    current = add(client,old)
    assert client.patch(f'/api/playlists/{old["id"]}',json={'name':'Stale','version':old['version']}).status_code == 409
    assert client.put(f'/api/playlists/{old["id"]}/order',json={'ids':[current['songs'][0]['id']]*2,'version':current['version']}).status_code == 400
    assert client.get(f'/api/playlists/{old["id"]}').json()['name'] == 'Noche'


def test_limits_and_rollback(client):
    login(client)
    source = create(client,'Origen')
    target = add(client,create(client,'Destino'),50)
    source = add(client,source,1)
    new_song = song(99)
    source = client.get(f'/api/playlists/{source["id"]}').json()
    assert client.post(f'/api/playlists/{source["id"]}/songs',json={'songs':[new_song],'version':source['version']}).status_code == 200
    source = client.get(f'/api/playlists/{source["id"]}').json()
    result = client.post('/api/transfer',json={'source':source['id'],'target':target['id'],'move':True,'source_version':source['version'],'target_version':target['version']})
    assert result.status_code == 400
    assert len(client.get(f'/api/playlists/{source["id"]}').json()['songs']) == 2
    create(client,'Tercera')
    assert client.post('/api/playlists',json={'name':'Cuarta'}).status_code == 400


def test_foreign_keys_cascade_and_favorites_independent(client,settings):
    login(client)
    p = add(client,create(client),1)
    client.post(f'/api/playlists/{p["id"]}/like')
    client.post(f'/api/songs/{p["songs"][0]["id"]}/favorite')
    assert len(client.get('/api/favorites').json()) == 1
    client.delete(f'/api/playlists/{p["id"]}?version={p["version"]}')
    with Database(settings.db_path).connect() as db:
        assert db.execute('SELECT COUNT(*) FROM songs').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM playlist_likes').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM likes').fetchone()[0] == 1


def test_link_requires_one_time_code_and_migrates(client,settings):
    r = client.post('/api/register',json={'username':'member','password':'member-password-12345'})
    client.headers['X-CSRF-Token'] = r.json()['csrf']
    p = create(client)
    assert int(p['user_id']) < 0
    code = client.post('/api/link-code').json()['code']
    db = Database(settings.db_path)
    assert db.link(code,123123123123123123) == 'member'
    assert client.get('/api/me').json()['user']['library_id'] == '123123123123123123'
    assert client.get('/api/playlists').json()[0]['user_id'] == '123123123123123123'
    with pytest.raises(ValueError):
        db.link(code,123123123123123123)


@pytest.mark.parametrize('uri',['http://127.0.0.1/secret','https://youtube.com.evil.example/test','file:///etc/passwd','https://youtube.com:abc/test','https://username:pass@youtube.com/test'])
def test_url_validation(uri):
    with pytest.raises(LibraryError):
        safe_uri(uri)


def test_data_survives_restart(client,settings):
    login(client)
    p = add(client,create(client),1)
    with TestClient(create_app(settings,run_bot=False)) as other:
        login(other)
        assert other.get(f'/api/playlists/{p["id"]}').json()['songs'][0]['title'] == 'Cancion 0'


def test_bot_writes_are_seen_by_web(client,settings):
    login(client)
    p = create(client)
    old_revision = client.get('/api/status').json()['revision']
    with Database(settings.db_path).connect(True) as db:
        db.execute('INSERT INTO songs(playlist_id,title,uri,position) VALUES(?,?,?,1)',(p['id'],'Desde Discord',song()['uri']))
    assert client.get('/api/status').json()['revision'] > old_revision
    assert client.get(f'/api/playlists/{p["id"]}').json()['version'] > p['version']


def test_concurrent_edits_have_one_winner(client,settings):
    login(client)
    p = create(client)
    lib = Library(Database(settings.db_path))
    def change(name):
        try:
            lib.rename(settings.owner_id,int(p['id']),name,p['version'])
            return 200
        except LibraryError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change,['Primera','Segunda'])) == [200,409]


def test_cookies_security_and_no_secret_assets(client):
    r=client.post('/api/login',json={'username':'kur','password':'admin-test-password-2026'})
    assert 'HttpOnly' in r.headers['set-cookie']
    assert 'SameSite=strict' in r.headers['set-cookie']
    assert client.get('/.env').status_code == 404
    assert client.get('/data/laevatain.db').status_code == 404
    assert client.get('/').headers['x-frame-options'] == 'DENY'
    assert client.get('/static/cover.svg').status_code == 200


def test_python_has_no_comments():
    root=Path(__file__).resolve().parents[1]
    for path in list(root.glob('*.py'))+list((root/'tests').glob('*.py')):
        tokens=tokenize.generate_tokens(io.StringIO(path.read_text(encoding='utf-8')).readline)
        assert not any(token.type==tokenize.COMMENT for token in tokens),path.name


def test_bot_extensions_load_without_connecting(settings,tmp_path,monkeypatch):
    monkeypatch.setenv('LEVIATAN_DB_PATH',str(settings.db_path))
    monkeypatch.setenv('LEVIATAN_CHANNELS_PATH',str(tmp_path/'channels.json'))
    monkeypatch.setenv('LEVIATAN_PANELS_PATH',str(tmp_path/'panels.json'))
    monkeypatch.setenv('LEVIATAN_PLAYLIST_MEDIA_PATH',str(tmp_path/'playlist_media'))
    monkeypatch.setenv('LEVIATAN_PROFILE_MEDIA_PATH',str(tmp_path/'profile_media'))
    Database(settings.db_path).initialize(settings)
    from bot_runtime import LaevatainBot, LabCommands
    async def check():
        bot=LaevatainBot(settings,Database(settings.db_path))
        async with bot:
            for name in ('channels','guide','likes','music','playlists','social','version'):
                await bot.load_extension(name)
            await bot.add_cog(LabCommands(bot))
            commands={c.name for c in bot.tree.get_commands()}
            assert {'play','playlist','likes','profile','version','link'}.issubset(commands)
            assert bot.get_cog('MusicChannels').cleaner.is_running() is False
    asyncio.run(check())
