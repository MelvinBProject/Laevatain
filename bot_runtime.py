import asyncio
import logging
import os
import sqlite3

import discord
import wavelink
from discord import app_commands
from discord.ext import commands, tasks


class LabTree(app_commands.CommandTree):
    async def on_error(self, interaction, error):
        original = getattr(error, 'original', error)
        logging.warning('Slash command failed (%s)', type(original).__name__)
        message = 'The request could not finish because of a connection or permission error. Please try again shortly.'
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception as exc:
            logging.warning('Could not deliver command error (%s)', type(exc).__name__)

    async def interaction_check(self, interaction):
        if interaction.guild_id != self.client.settings.discord_guild_id:
            await interaction.response.send_message('This bot only works in its configured lab server', ephemeral=True)
            return False
        if self.client.settings.demo_mode and interaction.user.id != self.client.settings.owner_id:
            await interaction.response.send_message('This presentation is controlled by the owner', ephemeral=True)
            return False
        return True


class LabCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='link', description='Link your local website account with your Discord account')
    async def link(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        try:
            name = await asyncio.to_thread(self.bot.database.link, code.strip(), interaction.user.id)
            await interaction.followup.send(f'Account linked: {name}', ephemeral=True)
        except (ValueError, sqlite3.IntegrityError):
            await interaction.followup.send('The code expired, the account is already linked or a playlist name is repeated', ephemeral=True)

    @app_commands.command(name='version', description='Show the lab version to the owner')
    async def version(self, interaction: discord.Interaction):
        if interaction.user.id != self.bot.settings.owner_id:
            await interaction.response.send_message('Owner only', ephemeral=True)
            return
        embed = discord.Embed(title='Laevatain v0.1.0-local', description='Online / local lab', color=0x9E403E)
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class LaevatainBot(commands.Bot):
    def __init__(self, settings, database):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.message_content = os.getenv('MESSAGE_CONTENT_INTENT', 'false').lower() == 'true'
        super().__init__(command_prefix=commands.when_mentioned_or('!'), intents=intents, tree_cls=LabTree, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.database = database
        self.node_lock = asyncio.Lock()
        self.last_error = None
        self.web_locks = {}

    async def setup_hook(self):
        for extension in ('channels', 'guide', 'likes', 'music', 'playlists', 'social', 'version'):
            await self.load_extension(extension)
        await self.add_cog(LabCommands(self))
        guild = discord.Object(id=self.settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.reconnect_node.start()

    async def on_ready(self):
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name='your music library'))
        logging.info('Laevatain connected to the lab')

    @tasks.loop(seconds=20)
    async def reconnect_node(self):
        if not self.settings.lavalink_password:
            return
        async with self.node_lock:
            try:
                try:
                    node = wavelink.Pool.get_node(self.settings.lavalink_identifier)
                except wavelink.InvalidNodeException:
                    node = None
                if node and node.status is wavelink.NodeStatus.CONNECTED:
                    return
                if node:
                    await asyncio.wait_for(wavelink.Pool.reconnect(), 12)
                else:
                    node = wavelink.Node(uri=self.settings.lavalink_uri, password=self.settings.lavalink_password, identifier=self.settings.lavalink_identifier, retries=None, resume_timeout=300, inactive_player_timeout=None)
                    await asyncio.wait_for(wavelink.Pool.connect(nodes=[node], client=self), 12)
                self.last_error = None
            except Exception:
                self.last_error = 'Lavalink is unavailable. Retrying'

    @reconnect_node.before_loop
    async def before_node(self):
        await self.wait_until_ready()

    def status(self, discord_id=None):
        try:
            node = wavelink.Pool.get_node(self.settings.lavalink_identifier)
            audio = node.status is wavelink.NodeStatus.CONNECTED
        except wavelink.InvalidNodeException:
            audio = False
        result = {'bot': 'online' if self.is_ready() else 'connecting', 'audio': audio, 'version': '0.1.0-local', 'current': None}
        guild = self.get_guild(self.settings.discord_guild_id)
        member = guild.get_member(discord_id) if guild and discord_id else None
        music = self.get_cog('Music')
        if member and music:
            state = music.states.get(guild.id)
            if state:
                result['playback_error'] = state.playback_error
            if state and state.current:
                player = guild.voice_client
                track = player.current if player else None
                if track:
                    result['current'] = {'title': track.title, 'author': track.author or '', 'identifier': track.identifier, 'queue': len(state.queue), 'guild': guild.name, 'position_ms': player.position, 'duration_ms': track.length, 'paused': player.paused, 'seekable': track.is_seekable and not track.is_stream, 'live': track.is_stream, 'can_control': bool(member.voice and member.voice.channel == player.channel)}
        return result

    async def control_from_web(self, discord_id, action, identifier, position_ms=0):
        guild = self.get_guild(self.settings.discord_guild_id)
        member = guild.get_member(discord_id) if guild else None
        if self.settings.demo_mode and discord_id != self.settings.owner_id:
            raise ValueError('Only the owner can control this demo')
        if not self.is_ready() or not self.status()['audio']:
            raise ValueError('The bot or Lavalink is not connected')
        lock = self.web_locks.setdefault(self.settings.discord_guild_id, asyncio.Lock())
        async with lock:
            player = guild.voice_client if guild else None
            if not member or not member.voice or not player or member.voice.channel != player.channel:
                raise ValueError('Join the same voice channel as Laevatain first')
            track = player.current
            if not track or track.identifier != identifier:
                raise ValueError('The song changed. Wait for the player to refresh')
            if action == 'pause':
                await player.pause(True)
            elif action == 'resume':
                await player.pause(False)
            elif action == 'skip':
                await self.get_cog('Music').skip_current(guild)
            elif action == 'seek':
                if not track.is_seekable or track.is_stream:
                    raise ValueError('This stream does not support seeking')
                await player.seek(max(0, min(position_ms, max(0, track.length - 1000))))
            else:
                raise ValueError('Unknown playback action')

    async def play_from_web(self, discord_id, playlist):
        from music import QueueItem, search

        if not self.is_ready() or not self.status()['audio']:
            raise ValueError('The bot or Lavalink is not connected')
        guild = self.get_guild(self.settings.discord_guild_id)
        if not guild or int(playlist['guild_id']) not in (0, guild.id):
            raise ValueError('The playlist does not belong to this lab')
        member = guild.get_member(discord_id)
        if not member or not member.voice or not member.voice.channel:
            raise ValueError('Join a voice channel with your linked account first')
        channels = self.get_cog('MusicChannels')
        channel_id = channels.configured_channel_id(guild.id)
        output = guild.get_channel(channel_id) if channel_id else None
        if output is None:
            raise ValueError('Set the lab channel with /set channel first')
        lock = self.web_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            if guild.voice_client and guild.voice_client.channel != member.voice.channel:
                raise ValueError('The bot is in another voice channel')
            tracks = []
            for song in playlist['songs'][:50]:
                found = await asyncio.wait_for(search(song['uri']), 20)
                if found:
                    tracks.append(found[0])
            if not tracks:
                raise ValueError('Could not load the saved songs')
            if not member.voice or not member.voice.channel:
                raise ValueError('You are no longer in the voice channel')
            if guild.voice_client and guild.voice_client.channel != member.voice.channel:
                raise ValueError('The bot changed voice channels')
            if not guild.voice_client:
                await member.voice.channel.connect(cls=wavelink.Player, timeout=30, reconnect=True, self_deaf=True)
            music = self.get_cog('Music')
            state = music.state(guild.id)
            state.playback_blocked = False
            state.playback_error = None
            state.consecutive_failures = 0
            state.recovery_track_ids.clear()
            if len(state.queue) + len(tracks) > 200:
                raise ValueError('The queue reached the lab limit')
            state.queue.extend(QueueItem(track, member) for track in tracks)
            state.text_channel_id = output.id
            state.touch()
            await music.play_next(guild)
            return len(tracks)

    async def close(self):
        self.reconnect_node.cancel()
        for voice in list(self.voice_clients):
            try:
                await voice.disconnect(force=True)
            except Exception:
                pass
        await wavelink.Pool.close()
        await super().close()
