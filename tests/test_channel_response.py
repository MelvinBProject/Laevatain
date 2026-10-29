import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import channels


def test_set_channel_acknowledges_before_panel(tmp_path,monkeypatch):
    monkeypatch.setattr(channels,'CONFIG_PATH',tmp_path/'channels.json')
    monkeypatch.setattr(channels,'PANEL_PATH',tmp_path/'panels.json')
    calls=[]
    async def ack(**kwargs):
        calls.append('ack')
    async def confirm(*args,**kwargs):
        calls.append('confirm')
    async def panel(guild_id):
        calls.append('panel')
        raise OSError('network unavailable')
    cog=SimpleNamespace(channels={},show_idle=panel,idle_messages={},idle_message_ids={})
    interaction=SimpleNamespace(user=SimpleNamespace(id=channels.OWNER_ID),guild_id=1,guild=SimpleNamespace(me=object()),response=SimpleNamespace(defer=ack),followup=SimpleNamespace(send=confirm))
    channel=SimpleNamespace(guild=SimpleNamespace(id=1),id=2,mention='#music',permissions_for=lambda member:SimpleNamespace(view_channel=True,send_messages=True,embed_links=True))
    asyncio.run(channels.MusicChannels.set_channel.callback(cog,interaction,channel))
    assert calls==['ack','confirm','panel']
    assert cog.channels=={1:2}
    assert channels.load_channels()=={1:2}


def test_idle_does_not_delete_chat_by_default(monkeypatch):
    monkeypatch.delenv('LEVIATAN_CLEAN_CHAT',raising=False)
    channel=SimpleNamespace(purge=AsyncMock())
    asyncio.run(channels.MusicChannels.clear_for_idle(SimpleNamespace(),channel,123))
    channel.purge.assert_not_called()
