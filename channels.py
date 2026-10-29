from __future__ import annotations
import asyncio
import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands, tasks
OWNER_ID = int(os.getenv('OWNER_DISCORD_ID', '0'))
CONFIG_PATH = Path(os.getenv('LEVIATAN_CHANNELS_PATH', Path(__file__).with_name('music_channels.json')))
PANEL_PATH = Path(os.getenv('LEVIATAN_PANELS_PATH', Path(__file__).with_name('panel_messages.json')))
GIFS_PATH = Path(os.getenv('LEVIATAN_GIFS_PATH', Path(__file__).with_name('gifs')))
USER_MESSAGE_TTL = 10
BOT_MESSAGE_TTL = 30
QUEUE_MESSAGE_TTL = 60

def load_channels() -> dict[int, int]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        return {int(guild_id): int(channel_id) for guild_id, channel_id in data.items()}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}

def save_channels(channels: dict[int, int]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({str(key): value for key, value in channels.items()}, indent=2), encoding='utf-8')

def load_panel_ids() -> dict[int, int]:
    if not PANEL_PATH.exists():
        return {}
    try:
        data = json.loads(PANEL_PATH.read_text(encoding='utf-8'))
        return {int(guild_id): int(message_id) for guild_id, message_id in data.items()}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}

def save_panel_ids(message_ids: dict[int, int]) -> None:
    PANEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    PANEL_PATH.write_text(json.dumps({str(key): value for key, value in message_ids.items()}, indent=2), encoding='utf-8')

class MusicChannels(commands.Cog):
    set_group = app_commands.Group(name='set', description='Laevatain configuration')

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.channels = load_channels()
        self.idle_messages: dict[int, discord.Message] = {}
        self.idle_message_ids = load_panel_ids()
        self.gif_indexes: dict[int, int] = {}
        self.idle_locks: dict[int, asyncio.Lock] = {}
        self.idle_transitions: set[int] = set()
        self.idle_gifs = [GIFS_PATH / f'{number}.gif' for number in (1, 3)]
        self.idle_gifs = [path for path in self.idle_gifs if path.is_file()]
        if os.getenv('LEVIATAN_CLEAN_CHAT', 'false').lower() == 'true':
            self.cleaner.start()
        self.gif_rotator.start()

    def cog_unload(self) -> None:
        self.cleaner.cancel()
        self.gif_rotator.cancel()

    def configured_channel_id(self, guild_id: int | None) -> int | None:
        return self.channels.get(guild_id) if guild_id else None

    def is_music_channel(self, interaction: discord.Interaction) -> bool:
        configured = self.configured_channel_id(interaction.guild_id)
        return configured is not None and interaction.channel_id == configured

    def channel_error(self, interaction: discord.Interaction) -> str:
        configured = self.configured_channel_id(interaction.guild_id)
        if configured:
            return f'Endmin, please use <#{configured}> for music requests.'
        return 'No music channel has been assigned in the music room yet. The owner must use `/set channel`.'

    @staticmethod
    def idle_embed(attachment_name: str | None=None) -> discord.Embed:
        embed = discord.Embed(title='the music room is quiet', description='No music is currently playing.\nUse **`/play`** whenever you want to begin.', color=3447003)
        if attachment_name:
            embed.set_image(url=f'attachment://{attachment_name}')
        embed.set_footer(text='LAEVATAIN • MUSIC LAB')
        return embed

    def next_idle_gif(self, guild_id: int) -> tuple[discord.File | None, str | None]:
        if not self.idle_gifs:
            return (None, None)
        index = self.gif_indexes.get(guild_id, 0) % len(self.idle_gifs)
        self.gif_indexes[guild_id] = (index + 1) % len(self.idle_gifs)
        path = self.idle_gifs[index]
        filename = f'laevatain_idle_{index + 1}.gif'
        return (discord.File(path, filename=filename), filename)

    async def hide_idle(self, guild_id: int) -> None:
        lock = self.idle_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            self.idle_transitions.add(guild_id)
            try:
                await self._hide_idle(guild_id)
            finally:
                self.idle_transitions.discard(guild_id)

    async def _hide_idle(self, guild_id: int) -> None:
        message = self.idle_messages.pop(guild_id, None)
        stored_id = self.idle_message_ids.pop(guild_id, None)
        save_panel_ids(self.idle_message_ids)
        if not message and stored_id:
            channel_id = self.channels.get(guild_id)
            guild = self.bot.get_guild(guild_id)
            channel = guild.get_channel(channel_id) if guild and channel_id else None
            if isinstance(channel, discord.TextChannel):
                try:
                    message = await channel.fetch_message(stored_id)
                except discord.HTTPException:
                    pass
        if message:
            try:
                await message.delete()
            except discord.HTTPException:
                pass

    async def show_idle(self, guild_id: int, message: discord.Message | None=None) -> None:
        lock = self.idle_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            self.idle_transitions.add(guild_id)
            try:
                await self._show_idle(guild_id, message)
            finally:
                self.idle_transitions.discard(guild_id)

    async def _show_idle(self, guild_id: int, message: discord.Message | None=None) -> None:
        channel_id = self.channels.get(guild_id)
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild and channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        gif, filename = self.next_idle_gif(guild_id)
        embed = self.idle_embed(filename)
        current = message or self.idle_messages.get(guild_id)
        if not current and (stored_id := self.idle_message_ids.get(guild_id)):
            try:
                current = await channel.fetch_message(stored_id)
            except discord.HTTPException:
                self.idle_message_ids.pop(guild_id, None)
                save_panel_ids(self.idle_message_ids)
        if current:
            try:
                attachments = [gif] if gif else []
                await current.edit(content=None, embed=embed, view=None, attachments=attachments)
                self.idle_messages[guild_id] = current
                self.idle_message_ids[guild_id] = current.id
                save_panel_ids(self.idle_message_ids)
                await self.clear_for_idle(channel, current.id)
                return
            except discord.HTTPException:
                self.idle_messages.pop(guild_id, None)
                if gif:
                    gif.close()
                gif, filename = self.next_idle_gif(guild_id)
                embed = self.idle_embed(filename)
        if gif:
            self.idle_messages[guild_id] = await channel.send(embed=embed, file=gif)
        else:
            self.idle_messages[guild_id] = await channel.send(embed=embed)
        self.idle_message_ids[guild_id] = self.idle_messages[guild_id].id
        save_panel_ids(self.idle_message_ids)
        await self.clear_for_idle(channel, self.idle_messages[guild_id].id)

    async def clear_for_idle(self, channel: discord.TextChannel, keep_id: int) -> None:
        if os.getenv('LEVIATAN_CLEAN_CHAT', 'false').lower() != 'true':
            return
        music = self.bot.get_cog('Music')
        pending_ids = set(music.pending_response_ids) if music else set()
        try:
            await channel.purge(limit=100, check=lambda candidate: candidate.author.id == self.bot.user.id and candidate.id != keep_id and candidate.id not in pending_ids, bulk=True)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        music = self.bot.get_cog('Music')
        for guild_id in self.channels:
            state = music.states.get(guild_id) if music else None
            if not state or not state.current:
                await self.show_idle(guild_id)

    @set_group.command(name='channel', description='Set the only channel for music commands')
    @app_commands.describe(channel='The text channel Laevatain will use for music')
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message('Sorry. Only my owner can change this configuration.', ephemeral=True)
            return
        if not interaction.guild_id or channel.guild.id != interaction.guild_id:
            await interaction.response.send_message('That channel does not belong to this ship. Select one from this server.', ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=False)
        permissions = channel.permissions_for(interaction.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            await interaction.followup.send('I need View Channel, Send Messages and Embed Links in that channel.')
            return
        if self.channels.get(interaction.guild_id) != channel.id:
            self.idle_messages.pop(interaction.guild_id, None)
            self.idle_message_ids.pop(interaction.guild_id, None)
            save_panel_ids(self.idle_message_ids)
        self.channels[interaction.guild_id] = channel.id
        save_channels(self.channels)
        await interaction.followup.send(f'Music channel saved: {channel.mention}. Join a voice channel and use /play.', ephemeral=False)
        try:
            await asyncio.wait_for(self.show_idle(interaction.guild_id), timeout=20)
        except Exception as exc:
            logging.warning('Music channel saved; panel unavailable (%s)', type(exc).__name__)

    @tasks.loop(seconds=10)
    async def cleaner(self) -> None:
        music = self.bot.get_cog('Music')
        for guild_id, channel_id in list(self.channels.items()):
            guild = self.bot.get_guild(guild_id)
            channel = guild.get_channel(channel_id) if guild else None
            if not isinstance(channel, discord.TextChannel):
                continue
            protected_id: int | None = None
            queue_id: int | None = None
            pending_ids: set[int] = set()
            idle = self.idle_messages.get(guild_id)
            idle_id = idle.id if idle and idle.channel.id == channel_id else None
            if music:
                pending_ids = set(music.pending_response_ids)
                state = music.states.get(guild_id)
                if state and state.player_message and (state.player_message.channel.id == channel_id):
                    protected_id = state.player_message.id
                if state and state.queue_message and (state.queue_message.channel.id == channel_id):
                    queue_id = state.queue_message.id
            if guild_id in self.idle_transitions:
                continue
            if not protected_id and (not idle_id):
                await self.show_idle(guild_id)
                idle = self.idle_messages.get(guild_id)
                idle_id = idle.id if idle and idle.channel.id == channel_id else None
            now = datetime.now(timezone.utc)

            def should_delete(message: discord.Message) -> bool:
                if message.id in {protected_id, idle_id} or message.id in pending_ids:
                    return False
                age = (now - message.created_at).total_seconds()
                if message.id == queue_id:
                    return age >= QUEUE_MESSAGE_TTL
                if message.author.bot and message.embeds:
                    embed = message.embeds[0]
                    footer = embed.footer.text or ''
                    author = embed.author.name or ''
                    stale_panel = embed.title == 'the music room is quiet' or author == '🎶 Playback queue' or 'the music room moves forward' in footer
                    if stale_panel:
                        return True
                sent_by_bot = message.author.bot or message.webhook_id is not None
                ttl = BOT_MESSAGE_TTL if sent_by_bot else USER_MESSAGE_TTL
                return age >= ttl
            try:
                await channel.purge(limit=100, check=should_delete, bulk=True)
            except (discord.Forbidden, discord.HTTPException):
                pass

    @cleaner.before_loop
    async def before_cleaner(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=2)
    async def gif_rotator(self) -> None:
        music = self.bot.get_cog('Music')
        for guild_id in self.channels:
            state = music.states.get(guild_id) if music else None
            if not state or not state.current:
                await self.show_idle(guild_id)

    @gif_rotator.before_loop
    async def before_gif_rotator(self) -> None:
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicChannels(bot))
