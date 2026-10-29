import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot_runtime import LaevatainBot
from test_local import client, settings, login


def player_bot():
    channel = SimpleNamespace(id=10)
    track = SimpleNamespace(identifier='song', length=120000, is_seekable=True, is_stream=False)
    player = SimpleNamespace(channel=channel, current=track, pause=AsyncMock(), seek=AsyncMock())
    member = SimpleNamespace(voice=SimpleNamespace(channel=channel))
    guild = SimpleNamespace(voice_client=player, get_member=lambda _: member)
    music = SimpleNamespace(skip_current=AsyncMock())
    bot = SimpleNamespace(settings=SimpleNamespace(discord_guild_id=1, demo_mode=True, owner_id=42), get_guild=lambda _: guild, get_cog=lambda _: music, is_ready=lambda: True, status=lambda: {'audio':True}, web_locks={})
    return bot, player, member, music


def test_player_controls_and_seek_limit():
    async def run():
        bot, player, member, music = player_bot()
        for action in ('pause','resume','seek','skip'):
            await LaevatainBot.control_from_web(bot,42,action,'song',999999)
        assert player.pause.await_args_list[0].args == (True,)
        assert player.pause.await_args_list[1].args == (False,)
        player.seek.assert_awaited_once_with(119000)
        music.skip_current.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize('case',['other_owner','wrong_channel','stale_track','live_stream','offline'])
def test_player_rejects_unsafe_controls(case):
    async def run():
        bot, player, member, music = player_bot()
        user, identifier, action = 42, 'song', 'pause'
        if case == 'other_owner': user=99
        if case == 'wrong_channel': member.voice.channel=SimpleNamespace(id=11)
        if case == 'stale_track': identifier='old-song'
        if case == 'live_stream': player.current.is_stream=True; action='seek'
        if case == 'offline': bot.is_ready=lambda: False
        with pytest.raises(ValueError):
            await LaevatainBot.control_from_web(bot,user,action,identifier,1000)
        player.pause.assert_not_awaited()
        player.seek.assert_not_awaited()
        music.skip_current.assert_not_awaited()
    asyncio.run(run())


def test_player_endpoint_requires_auth_and_csrf(client):
    payload={'action':'pause','identifier':'song'}
    assert client.post('/api/player/control',json=payload).status_code in (401,403)
    login(client)
    assert client.post('/api/player/control',json=payload,headers={'X-CSRF-Token':'wrong'}).status_code==403
    assert client.post('/api/player/control',json={'action':'delete','identifier':'song'}).status_code==422
    assert client.post('/api/player/control',json=payload).status_code==503
