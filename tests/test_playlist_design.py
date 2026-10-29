import asyncio
import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from database import Database
from test_local import settings, client, login, create, add
from playlist_design import validate_gif
from library import LibraryError


def picture(format='GIF'):
    output=io.BytesIO()
    Image.new('RGB',(4,4),'red').save(output,format=format)
    return output.getvalue()


def design(p, **extra):
    return dict(name='My embed',show_avatar=False,version=p['version'],**extra)


def test_design_saved_gif_and_discord_embed(client,settings):
    from playlists import PlaylistPagesView
    login(client)
    p=create(client)
    data=picture()
    result=client.put(f"/api/playlists/{p['id']}/appearance",json=design(p,gif_base64=base64.b64encode(data).decode()))
    assert result.status_code==200,result.text
    saved=result.json()
    assert saved['name']=='My embed' and saved['show_avatar']==0
    assert client.get(f"/api/playlists/{p['id']}/art").content==data
    with Database(settings.db_path).connect() as db:
        row=db.execute('SELECT * FROM playlists WHERE id=?',(p['id'],)).fetchone()
    owner=SimpleNamespace(display_name='Kur',display_avatar=SimpleNamespace(url='https://cdn.discordapp.com/test.gif'))
    embed=PlaylistPagesView.embed(SimpleNamespace(page=0,pages=1,tracks=[],item=row,owner=owner,attachment_name='playlist.gif'))
    assert embed.title=='My embed'
    assert not embed.author.icon_url and not embed.thumbnail.url
    assert embed.image.url=='attachment://playlist.gif'
    assert client.put(f"/api/playlists/{p['id']}/appearance",json=design(p)).status_code==409
    removed=client.put(f"/api/playlists/{p['id']}/appearance",json=design(saved,remove_gif=True))
    assert removed.status_code==200
    assert client.get(f"/api/playlists/{p['id']}/art").status_code==404


@pytest.mark.parametrize('data',[b'GIF89a-broken',b'<svg/>',picture('PNG'),b'X'*(8*1024*1024+1)],ids=['broken','svg','png','oversized'])
def test_rejects_non_gif_or_oversized(data):
    with pytest.raises(LibraryError):validate_gif(data)


def test_appearance_ownership_and_csrf(client,settings):
    login(client)
    p=create(client)
    assert client.put(f"/api/playlists/{p['id']}/appearance",json=design(p),headers={'X-CSRF-Token':'bad'}).status_code==403
    with Database(settings.db_path).connect(True) as db:
        db.execute('UPDATE playlists SET user_id=99 WHERE id=?',(p['id'],))
    assert client.put(f"/api/playlists/{p['id']}/appearance",json=design(p)).status_code==404


def test_design_keeps_fifty_song_limit(client):
    login(client)
    p=add(client,create(client),50)
    assert len(p['songs'])==50
    r=client.put(f"/api/playlists/{p['id']}/appearance",json=design(p))
    assert r.status_code==200
    from test_local import song
    assert client.post(f"/api/playlists/{p['id']}/songs",json={'songs':[song(51)],'version':r.json()['version']}).status_code==400


def test_show_playlist_alias_uses_same_renderer():
    from playlists import Playlists
    async def check():
        callback=AsyncMock()
        cog=SimpleNamespace(show=SimpleNamespace(callback=callback))
        interaction=object()
        await Playlists.show_playlist.callback(cog,interaction,'My embed')
        callback.assert_awaited_once_with(cog,interaction,'My embed')
    asyncio.run(check())
