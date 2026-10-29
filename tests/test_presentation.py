import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from test_local import settings, login, create, add
from webapp import create_app
from database import Database
from presentation import seed_profiles


def test_demo_registration_closed_and_profile_labels(settings):
    settings = replace(settings, demo_mode=True)
    with TestClient(create_app(settings, run_bot=False)) as client:
        assert client.get('/api/public-settings').json() == {'registration':False,'demo':True}
        assert client.post('/api/register',json={'username':'other','password':'test-password-123'}).status_code == 403
        login(client)
        rows = client.get('/api/playlists?community=true').json()
        assert len(rows) == 2
        assert all(p['is_demo'] for p in rows)
        assert {p['owner_name'] for p in rows} == {'Mika','Ren'}
        assert client.get('/api/playlists').json() == []
        assert client.patch('/api/playlists/'+rows[0]['id'],json={'name':'changed','version':rows[0]['version']}).status_code == 404
        seed_profiles(Database(settings.db_path), settings.discord_guild_id)
        assert len(client.get('/api/playlists?community=true').json()) == 2


def test_ten_character_login_password(settings):
    settings = replace(settings, admin_password='test123456', demo_mode=True)
    from database import password_hash
    db = Database(settings.db_path)
    db.initialize(replace(settings,admin_password='initial-test-password'))
    with db.connect(True) as connection:
        connection.execute('UPDATE web_users SET password_hash=?',(password_hash(settings.admin_password),))
    with TestClient(create_app(settings,run_bot=False)) as client:
        assert client.post('/api/login',json={'username':'kur','password':'test123456'}).status_code == 200


def test_actual_discord_playlist_command_and_web_share_data(settings,monkeypatch):
    import playlists
    monkeypatch.setattr(playlists,'DB_PATH',settings.db_path)
    with TestClient(create_app(settings,run_bot=False)) as client:
        login(client)
        interaction=SimpleNamespace(guild_id=settings.discord_guild_id,user=SimpleNamespace(id=settings.owner_id),response=SimpleNamespace(send_message=AsyncMock()))
        asyncio.run(playlists.Playlists.create.callback(None,interaction,'From Discord'))
        rows=client.get('/api/playlists').json()
        assert rows[0]['name']=='From Discord'
        updated=add(client,rows[0],2)
        assert len(playlists.songs(int(updated['id'])))==2
        assert client.patch('/api/playlists/'+updated['id'],json={'name':'From Web','version':updated['version']}).status_code==200
        assert playlists.playlist(settings.discord_guild_id,settings.owner_id,'From Web') is not None


def test_demo_tree_rejects_other_discord_users(settings):
    from bot_runtime import LaevatainBot
    settings=replace(settings,demo_mode=True)
    async def check():
        bot=LaevatainBot(settings,Database(settings.db_path))
        async with bot:
            other=SimpleNamespace(guild_id=settings.discord_guild_id,user=SimpleNamespace(id=1),response=SimpleNamespace(send_message=AsyncMock()))
            assert not await bot.tree.interaction_check(other)
            owner=SimpleNamespace(guild_id=settings.discord_guild_id,user=SimpleNamespace(id=settings.owner_id))
            assert await bot.tree.interaction_check(owner)
    asyncio.run(check())
