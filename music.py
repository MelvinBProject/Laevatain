from __future__ import annotations
import asyncio
import html
import json
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlparse
import aiohttp
import discord
import wavelink
from discord import app_commands
from discord.ext import commands, tasks
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
WHITE = 16777215
LEVIATAN_BLUE = 3447003
PAGE_SIZE = 10
YOUTUBE_PLAYLIST_LIMIT = 50

class LoopMode(StrEnum):
    OFF = 'off'
    TRACK = 'track'
    QUEUE = 'queue'

class SpotifyResolveError(RuntimeError):
    pass

@dataclass(slots=True)
class QueueItem:
    track: wavelink.Playable
    requester: discord.Member | discord.User

@dataclass(slots=True)
class GuildState:
    queue: deque[QueueItem] = field(default_factory=deque)
    history: deque[QueueItem] = field(default_factory=lambda: deque(maxlen=50))
    current: QueueItem | None = None
    loop_mode: LoopMode = LoopMode.OFF
    player_message: discord.Message | None = None
    queue_message: discord.Message | None = None
    queue_view: QueueView | None = None
    text_channel_id: int | None = None
    volume: int = 100
    last_activity: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    failed_track_ids: set[str] = field(default_factory=set)
    recovery_track_ids: set[str] = field(default_factory=set)
    consecutive_failures: int = 0
    playback_blocked: bool = False
    playback_error: str | None = None

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def reset(self) -> None:
        self.queue.clear()
        self.current = None
        self.loop_mode = LoopMode.OFF
        self.player_message = None
        self.failed_track_ids.clear()
        self.recovery_track_ids.clear()
        self.consecutive_failures = 0
        self.playback_blocked = False
        self.playback_error = None
        self.touch()

def format_time(milliseconds: int | float | None) -> str:
    seconds = max(0, int(milliseconds or 0) // 1000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'

def thumbnail(track: wavelink.Playable) -> str | None:
    if track.artwork:
        return track.artwork
    match = re.search('(?:v=|youtu\\.be/)([A-Za-z0-9_-]{11})', track.uri or '')
    return f'https://img.youtube.com/vi/{match.group(1)}/hqdefault.jpg' if match else None

def clean_youtube_playlist_url(query: str) -> str:
    if 'youtube.com' not in query and 'youtu.be' not in query:
        return query
    match = re.search('[?&]list=([A-Za-z0-9_-]+)', query)
    return f'https://www.youtube.com/playlist?list={match.group(1)}' if match else query

def is_youtube_playlist_url(query: str) -> bool:
    return bool(('youtube.com' in query or 'youtu.be' in query) and re.search('[?&]list=([A-Za-z0-9_-]+)', query))

def playlist_entry_urls(info: dict) -> list[str]:
    urls: list[str] = []
    for entry in info.get('entries') or []:
        if not isinstance(entry, dict):
            continue
        video_id = str(entry.get('id') or '').strip()
        url = str(entry.get('webpage_url') or entry.get('url') or '').strip()
        if video_id and (not url.startswith(('http://', 'https://'))):
            url = f'https://www.youtube.com/watch?v={video_id}'
        if url.startswith(('https://www.youtube.com/watch', 'https://youtu.be/')):
            urls.append(url)
        if len(urls) >= YOUTUBE_PLAYLIST_LIMIT:
            break
    return urls

async def extract_youtube_playlist_urls(query: str) -> list[str]:

    def extract() -> list[str]:
        options = {'quiet': True, 'no_warnings': True, 'extract_flat': 'in_playlist', 'skip_download': True, 'ignoreerrors': True, 'playlistend': YOUTUBE_PLAYLIST_LIMIT}
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(query, download=False)
        return playlist_entry_urls(info or {})
    try:
        return await asyncio.wait_for(asyncio.to_thread(extract), timeout=30)
    except (DownloadError, asyncio.TimeoutError):
        return []

async def load_playlist_tracks(urls: list[str]) -> list[wavelink.Playable]:
    tracks: list[wavelink.Playable] = []
    for url in urls:
        try:
            result = await wavelink.Playable.search(url)
        except wavelink.WavelinkException:
            continue
        if isinstance(result, wavelink.Playlist):
            tracks.extend(result.tracks[:1])
        elif result:
            tracks.append(result[0])
    return tracks

def is_spotify_url(query: str) -> bool:
    try:
        host = (urlparse(query.strip()).hostname or '').lower()
    except ValueError:
        return False
    return host in {'open.spotify.com', 'spotify.link'} or host.endswith('.spotify.com')

async def resolve_spotify_track(query: str) -> str:
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            source_url = query.strip()
            if (urlparse(source_url).hostname or '').lower() == 'spotify.link':
                async with session.get(source_url, allow_redirects=True, headers={'User-Agent': 'Mozilla/5.0'}) as redirect:
                    redirect.raise_for_status()
                    source_url = str(redirect.url)
            async with session.get('https://open.spotify.com/oembed', params={'url': source_url}, headers={'User-Agent': 'Laevatain-Discord-Music/1.0'}) as response:
                if response.status == 404:
                    raise SpotifyResolveError('That Spotify link does not exist or is not public, Endmin.')
                response.raise_for_status()
                data = await response.json()
            entity_url = str(data.get('iframe_url') or '')
            if not entity_url:
                html_embed = str(data.get('html') or '')
                match = re.search('src=["\\\']([^"\\\']+)', html_embed)
                entity_url = html.unescape(match.group(1)) if match else ''
            if '/track/' not in entity_url:
                raise SpotifyResolveError('For now I can read individual Spotify songs, but not albums or playlists.')
            async with session.get(entity_url, headers={'User-Agent': 'Mozilla/5.0'}) as embed_response:
                embed_response.raise_for_status()
                embed_html = await embed_response.text()
    except SpotifyResolveError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise SpotifyResolveError('Spotify did not respond in time. Please try again, Endmin.') from exc
    next_data = re.search('<script[^>]+id=["\\\']__NEXT_DATA__["\\\'][^>]*>(.*?)</script>', embed_html, flags=re.DOTALL)
    try:
        payload = json.loads(html.unescape(next_data.group(1))) if next_data else {}
        entity = payload['props']['pageProps']['state']['data']['entity']
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        entity = {}
    title = str(entity.get('name') or entity.get('title') or data.get('title') or '').strip()
    artists = [str(artist.get('name') or '').strip() for artist in entity.get('artists', []) if isinstance(artist, dict) and artist.get('name')]
    if not title or not artists:
        raise SpotifyResolveError('I could open the link, but Spotify did not let me confirm the artist. Try searching `artist + song`, Endmin.')
    return f"{' '.join(artists)} - {title} official audio"

async def search(query: str) -> list[wavelink.Playable]:
    raw = await resolve_spotify_track(query) if is_spotify_url(query) else query.strip()
    raw = clean_youtube_playlist_url(raw)
    playlist_url = is_youtube_playlist_url(raw)
    for attempt in range(3):
        try:
            result = await wavelink.Playable.search(raw)
            if isinstance(result, wavelink.Playlist):
                return list(result.tracks)
            if result:
                return [result[0]]
        except wavelink.WavelinkException:
            if attempt == 2:
                if playlist_url:
                    urls = await extract_youtube_playlist_urls(raw)
                    return await load_playlist_tracks(urls)
                raise
        await asyncio.sleep(0.4 * (attempt + 1))
    if playlist_url:
        urls = await extract_youtube_playlist_urls(raw)
        return await load_playlist_tracks(urls)
    return []

class QueueView(discord.ui.View):

    def __init__(self, cog: Music, guild_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.page = 0
        self._refresh_buttons()

    @property
    def items(self) -> list[QueueItem]:
        return list(self.cog.state(self.guild_id).queue)

    def _refresh_buttons(self) -> None:
        pages = max(1, (len(self.items) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(self.page, pages - 1)
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= pages - 1

    def embed(self) -> discord.Embed:
        pages = max(1, (len(self.items) + PAGE_SIZE - 1) // PAGE_SIZE)
        start = self.page * PAGE_SIZE
        lines = []
        for number, item in enumerate(self.items[start:start + PAGE_SIZE], start + 1):
            title = item.track.title.replace('[', '\\[').replace(']', '\\]')
            lines.append(f'`{number}.` [{title}]({item.track.uri}) — {item.requester.display_name}')
        embed = discord.Embed(description='\n'.join(lines) or 'The music queue is empty, Endmin.', color=WHITE)
        embed.set_author(name='🎶 Playback queue')
        embed.set_footer(text=f'Page {self.page + 1} / {pages} · {len(self.items)} songs · ♢ Laevatain ♢')
        return embed

    @discord.ui.button(label='◀', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label='▶', style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        pages = max(1, (len(self.items) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(pages - 1, self.page + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

class PlayerView(discord.ui.View):

    def __init__(self, cog: Music | None=None) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    def music(self, interaction: discord.Interaction) -> Music:
        cog = self.cog or interaction.client.get_cog('Music')
        if not isinstance(cog, Music):
            raise RuntimeError('The music module is unavailable.')
        return cog

    async def allowed(self, interaction: discord.Interaction) -> bool:
        cog = self.music(interaction)
        channels = interaction.client.get_cog('MusicChannels')
        if channels and (not channels.is_music_channel(interaction)):
            await interaction.response.send_message(channels.channel_error(interaction), ephemeral=False)
            return False
        if not await cog.same_voice(interaction):
            await interaction.response.send_message('Endmin, you need to be in the same voice channel.', ephemeral=False)
            return False
        return True

    @discord.ui.button(label='⏯', style=discord.ButtonStyle.primary, custom_id='laevatain:pause')
    async def pause(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        cog = self.music(interaction)
        player = cog.player(interaction.guild)
        await player.pause(not player.paused)
        cog.state(interaction.guild_id).touch()
        await interaction.response.defer()
        await cog.refresh(interaction.guild_id)

    @discord.ui.button(label='⏭', style=discord.ButtonStyle.secondary, custom_id='laevatain:skip')
    async def skip(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        await interaction.response.defer()
        await self.music(interaction).skip_current(interaction.guild)

    @discord.ui.button(label='↻', style=discord.ButtonStyle.success, custom_id='laevatain:loop')
    async def loop(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        cog = self.music(interaction)
        state = cog.state(interaction.guild_id)
        state.loop_mode = LoopMode.TRACK if state.loop_mode is LoopMode.OFF else LoopMode.OFF
        state.touch()
        await interaction.response.defer()
        await cog.refresh(interaction.guild_id)

    @discord.ui.button(label='❤️ Like', style=discord.ButtonStyle.primary, custom_id='laevatain:like')
    async def like(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self.allowed(interaction):
            return
        cog = self.music(interaction)
        state = cog.state(interaction.guild_id)
        likes = interaction.client.get_cog('Likes')
        if not state.current or not likes:
            await interaction.response.send_message('There is no active song to save, Endmin.', ephemeral=False)
            return
        added = likes.toggle(interaction.user.id, state.current.track)
        message = f'{interaction.user.mention}, I saved this song to your favorites. ❤️' if added else f'{interaction.user.mention}, I removed this song from your favorites.'
        await interaction.response.send_message(message, ephemeral=False)

    @discord.ui.button(label='🎶', style=discord.ButtonStyle.secondary, custom_id='laevatain:queue')
    async def queue(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        cog = self.music(interaction)
        await cog.show_queue(interaction)

class Music(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.states: dict[int, GuildState] = {}
        self.pending_response_ids: set[int] = set()
        self.view = PlayerView(self)
        bot.add_view(self.view)
        self.updater.start()

    def cog_unload(self) -> None:
        self.updater.cancel()

    def state(self, guild_id: int) -> GuildState:
        return self.states.setdefault(guild_id, GuildState())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            raise app_commands.CheckFailure('This action can only be used inside a server, Endmin.')
        channels = self.bot.get_cog('MusicChannels')
        if channels and (not channels.is_music_channel(interaction)):
            raise app_commands.CheckFailure(channels.channel_error(interaction))
        command_name = interaction.command.name if interaction.command else ''
        if command_name == 'play' or not interaction.guild.voice_client:
            return True
        if not await self.same_voice(interaction):
            raise app_commands.CheckFailure('Endmin, you need to be in the same voice channel.')
        return True

    @staticmethod
    def player(guild: discord.Guild | None) -> wavelink.Player:
        if not guild or not isinstance(guild.voice_client, wavelink.Player):
            raise app_commands.CheckFailure('There is no active music session right now.')
        return guild.voice_client

    async def same_voice(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        player = interaction.guild.voice_client
        return bool(interaction.user.voice and player and (interaction.user.voice.channel == player.channel))

    async def connect(self, interaction: discord.Interaction) -> wavelink.Player:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure('This action can only be used inside a server, Endmin.')
        voice = interaction.user.voice
        if not voice or not voice.channel:
            raise app_commands.CheckFailure('Join a voice channel first, Endmin. I will join you.')
        existing = interaction.guild.voice_client
        if existing:
            player = self.player(interaction.guild)
            if player.channel != voice.channel:
                raise app_commands.CheckFailure('I am already in another voice session in this server.')
            return player
        for attempt in range(2):
            try:
                return await voice.channel.connect(cls=wavelink.Player, timeout=45, reconnect=True, self_deaf=True)
            except (asyncio.TimeoutError, discord.ClientException) as exc:
                partial = interaction.guild.voice_client
                if partial:
                    try:
                        await partial.disconnect(force=True)
                    except (discord.ClientException, discord.HTTPException):
                        pass
                if attempt == 0:
                    await asyncio.sleep(2)
                    continue
                raise app_commands.CheckFailure('I could not connect to that voice channel after two attempts. Please try again in a moment.') from exc
        raise app_commands.CheckFailure('I could not establish a voice connection.')

    def embed(self, guild: discord.Guild, state: GuildState) -> discord.Embed:
        item = state.current
        if not item:
            return discord.Embed(description='the music room is quiet, Endmin.', color=LEVIATAN_BLUE)
        player = guild.voice_client
        position = player.position if isinstance(player, wavelink.Player) else 0
        track = item.track
        loop = {LoopMode.OFF: 'Off', LoopMode.TRACK: 'Track', LoopMode.QUEUE: 'Queue'}[state.loop_mode]
        embed = discord.Embed(description=f'**[{track.title}]({track.uri})**', color=LEVIATAN_BLUE)
        embed.add_field(name='Artist', value=f"`{track.author or '—'}`", inline=True)
        embed.add_field(name='Duration', value=f'`{format_time(track.length)}`', inline=True)
        embed.add_field(name='Requested by', value=item.requester.mention, inline=True)
        embed.add_field(name='Volume', value=f'`{state.volume}%`', inline=True)
        embed.add_field(name='Loop', value=f'`{loop}`', inline=True)
        embed.add_field(name='In queue', value=f'`{len(state.queue)}`', inline=True)
        if (image := thumbnail(track)):
            embed.set_thumbnail(url=image)
        embed.set_footer(text='♢ Laevatain — the music room moves forward ♢')
        return embed

    async def refresh(self, guild_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        state = self.state(guild_id)
        if not guild or not state.current or (not state.player_message):
            return
        try:
            await state.player_message.edit(embed=self.embed(guild, state), view=self.view)
        except discord.NotFound:
            state.player_message = None
        except discord.HTTPException:
            pass

    async def refresh_queue(self, guild_id: int) -> bool:
        state = self.state(guild_id)
        if not state.queue_message:
            return False
        view = state.queue_view or QueueView(self, guild_id)
        state.queue_view = view
        view._refresh_buttons()
        try:
            await state.queue_message.edit(embed=view.embed(), view=view)
            return True
        except discord.NotFound:
            state.queue_message = None
            state.queue_view = None
            return False
        except discord.HTTPException:
            return False

    async def show_queue(self, interaction: discord.Interaction) -> None:
        state = self.state(interaction.guild_id)
        if state.queue_message:
            await interaction.response.defer()
            if await self.refresh_queue(interaction.guild_id):
                return
            view = QueueView(self, interaction.guild_id)
            state.queue_view = view
            state.queue_message = await interaction.followup.send(embed=view.embed(), view=view, wait=True)
            return
        view = QueueView(self, interaction.guild_id)
        state.queue_view = view
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=False)
        state.queue_message = await interaction.original_response()

    async def publish(self, guild: discord.Guild, channel: discord.abc.Messageable) -> None:
        state = self.state(guild.id)
        if not state.current:
            return
        channels = self.bot.get_cog('MusicChannels')
        if channels:
            await channels.hide_idle(guild.id)
        if state.player_message:
            await self.refresh(guild.id)
            return
        state.player_message = await channel.send(embed=self.embed(guild, state), view=self.view)

    async def play_next(self, guild: discord.Guild) -> None:
        state = self.state(guild.id)
        player = self.player(guild)
        async with state.lock:
            if state.playback_blocked or player.playing or player.paused or (not state.queue):
                return
            state.current = state.queue.popleft()
            state.touch()
            try:
                await player.play(state.current.track, volume=state.volume)
            except Exception:
                failed = state.current
                state.current = None
                state.consecutive_failures += 1
                if failed:
                    state.failed_track_ids.add(failed.track.identifier)
                raise
            await self.refresh_queue(guild.id)
            channel = guild.get_channel(state.text_channel_id) if state.text_channel_id else None
            if channel:
                await self.publish(guild, channel)

    async def skip_current(self, guild: discord.Guild) -> None:
        state = self.state(guild.id)
        state.loop_mode = LoopMode.OFF if state.loop_mode is LoopMode.TRACK else state.loop_mode
        await self.player(guild).skip(force=True)

    async def queue_recovery_candidate(self, state: GuildState, failed: QueueItem) -> bool:
        if state.consecutive_failures >= 2 or len(state.recovery_track_ids) >= 2:
            return False
        identifier = failed.track.identifier
        if identifier in state.recovery_track_ids:
            return False
        state.recovery_track_ids.add(identifier)
        query = ' '.join((part for part in (failed.track.author, failed.track.title) if part)).strip()
        if not query:
            return False
        try:
            results = await wavelink.Playable.search(query)
        except wavelink.WavelinkException:
            return False
        replacement = next((track for track in results if track.identifier != identifier), None)
        if replacement is None:
            return False
        state.queue.appendleft(QueueItem(replacement, failed.requester))
        state.touch()
        return True

    async def enqueue(self, interaction: discord.Interaction, tracks: list[wavelink.Playable], channel: discord.abc.Messageable | None=None) -> int:
        state = self.state(interaction.guild_id)
        state.playback_blocked = False
        state.playback_error = None
        state.consecutive_failures = 0
        state.recovery_track_ids.clear()
        requester = interaction.user
        for track in tracks[:100]:
            state.queue.append(QueueItem(track, requester))
        output_channel = channel or interaction.channel
        state.text_channel_id = output_channel.id
        state.touch()
        await self.play_next(interaction.guild)
        await self.refresh_queue(interaction.guild_id)
        await self.publish(interaction.guild, output_channel)
        return min(100, len(tracks))

    @app_commands.command(name='play', description='Play music from search, YouTube, or Spotify')
    @app_commands.describe(query='Name, YouTube URL, YouTube playlist, or Spotify song')
    async def play_command(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(ephemeral=False, thinking=True)
        response_id: int | None = None
        try:
            response_id = (await interaction.original_response()).id
            self.pending_response_ids.add(response_id)
        except discord.HTTPException:
            pass
        try:
            try:
                tracks = await search(query)
            except SpotifyResolveError as error:
                await interaction.followup.send(str(error), ephemeral=False)
                return
            if not tracks:
                await interaction.followup.send('I could not find that song, Endmin. Try another name or link.', ephemeral=False)
                return
            await self.connect(interaction)
            count = await self.enqueue(interaction, tracks)
            text = f'The playlist is ready: **{count} songs** in queue.' if count > 1 else f'I added **{tracks[0].title}** for you, Endmin.'
            await interaction.followup.send(text, ephemeral=False)
        finally:
            if response_id is not None:
                self.pending_response_ids.discard(response_id)

    @app_commands.command(name='pause', description='Pause playback')
    async def pause_command(self, interaction: discord.Interaction) -> None:
        player = self.player(interaction.guild)
        await player.pause(True)
        await interaction.response.send_message('Playback paused, Endmin.', ephemeral=False)
        await self.refresh(interaction.guild_id)

    @app_commands.command(name='resume', description='Resume playback')
    async def resume_command(self, interaction: discord.Interaction) -> None:
        player = self.player(interaction.guild)
        await player.pause(False)
        await interaction.response.send_message('Resuming the music session.', ephemeral=False)
        await self.refresh(interaction.guild_id)

    @app_commands.command(name='skip', description='Skip the current song')
    async def skip_command(self, interaction: discord.Interaction) -> None:
        await self.skip_current(interaction.guild)
        await interaction.response.send_message('Understood, Endmin. Moving to the next song.', ephemeral=False)

    @app_commands.command(name='stop', description='Stop music and clear the queue')
    async def stop_command(self, interaction: discord.Interaction) -> None:
        state = self.state(interaction.guild_id)
        state.queue.clear()
        state.loop_mode = LoopMode.OFF
        await self.refresh_queue(interaction.guild_id)
        await self.player(interaction.guild).stop(force=True)
        await interaction.response.send_message('Music session stopped. The queue has been cleared.', ephemeral=False)

    @app_commands.command(name='queue', description='Show the playback queue')
    async def queue_command(self, interaction: discord.Interaction) -> None:
        await self.show_queue(interaction)

    @app_commands.command(name='remove', description='Remove a song from the queue by position')
    @app_commands.describe(position='The number shown next to the song in /queue')
    async def remove_command(self, interaction: discord.Interaction, position: app_commands.Range[int, 1]) -> None:
        state = self.state(interaction.guild_id)
        items = list(state.queue)
        if position > len(items):
            await interaction.response.send_message(f'The queue only has **{len(items)} songs**, Endmin.', ephemeral=False)
            return
        removed = items.pop(position - 1)
        state.queue = deque(items)
        state.touch()
        await interaction.response.send_message(f'I removed **{removed.track.title}** from position `{position}`.', ephemeral=False)
        await self.refresh_queue(interaction.guild_id)
        await self.refresh(interaction.guild_id)

    @app_commands.command(name='loop', description='Set the loop mode')
    @app_commands.choices(mode=[app_commands.Choice(name='Off', value='off'), app_commands.Choice(name='Track', value='track'), app_commands.Choice(name='Queue', value='queue')])
    async def loop_command(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        self.state(interaction.guild_id).loop_mode = LoopMode(mode.value)
        await interaction.response.send_message(f'Loop set to **{mode.name}**, Endmin.', ephemeral=False)
        await self.refresh(interaction.guild_id)

    @app_commands.command(name='shuffle', description='Shuffle the queued songs')
    async def shuffle_command(self, interaction: discord.Interaction) -> None:
        state = self.state(interaction.guild_id)
        items = list(state.queue)
        random.shuffle(items)
        state.queue = deque(items)
        await interaction.response.send_message(f'I shuffled the **{len(items)} songs** in the queue.', ephemeral=False)
        await self.refresh_queue(interaction.guild_id)
        await self.refresh(interaction.guild_id)

    @app_commands.command(name='volume', description='Set the volume from 0 to 200')
    @app_commands.describe(percent='Volume percentage from 0 to 200')
    async def volume_command(self, interaction: discord.Interaction, percent: app_commands.Range[int, 0, 200]) -> None:
        state = self.state(interaction.guild_id)
        state.volume = percent
        await self.player(interaction.guild).set_volume(percent)
        await interaction.response.send_message(f'Audio level set to **{percent}%**.', ephemeral=False)
        await self.refresh(interaction.guild_id)

    @app_commands.command(name='seek', description='Jump to a position in the song')
    @app_commands.describe(position='Position as MM:SS, HH:MM:SS, or seconds')
    async def seek_command(self, interaction: discord.Interaction, position: str) -> None:
        try:
            parts = [int(part) for part in position.split(':')]
            if not 1 <= len(parts) <= 3 or any((part < 0 for part in parts)):
                raise ValueError
            seconds = sum((value * 60 ** index for index, value in enumerate(reversed(parts))))
        except ValueError:
            await interaction.response.send_message('I need a valid position: seconds, `MM:SS`, or `HH:MM:SS`.', ephemeral=False)
            return
        player = self.player(interaction.guild)
        if not player.current or seconds * 1000 > player.current.length:
            await interaction.response.send_message('That position is outside the song, Endmin.', ephemeral=False)
            return
        await player.seek(seconds * 1000)
        await interaction.response.send_message(f'Position set to **{format_time(seconds * 1000)}**.', ephemeral=False)
        await self.refresh(interaction.guild_id)

    @app_commands.command(name='disconnect', description='Disconnect Laevatain from voice')
    async def disconnect_command(self, interaction: discord.Interaction) -> None:
        state = self.state(interaction.guild_id)
        player = self.player(interaction.guild)
        message = state.player_message
        state.reset()
        await self.refresh_queue(interaction.guild_id)
        await player.disconnect()
        channels = self.bot.get_cog('MusicChannels')
        if channels:
            await channels.show_idle(interaction.guild_id, message)
        await interaction.response.send_message('I disconnected from voice, Endmin.', ephemeral=False)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player = payload.player
        if player is None or not player.guild:
            return
        state = self.state(player.guild.id)
        previous = state.current
        if previous is None or payload.track.identifier != previous.track.identifier:
            return
        if payload.reason.lower() == 'replaced':
            return
        failed = payload.track.identifier in state.failed_track_ids or payload.reason.lower() in {'loadfailed', 'cleanup'}
        state.failed_track_ids.discard(payload.track.identifier)
        if failed:
            state.consecutive_failures += 1
            if state.consecutive_failures >= 3:
                state.playback_blocked = True
                state.playback_error = 'The audio source rejected three attempts. Automatic playback is stopped; your saved playlists are unchanged.'
        else:
            state.consecutive_failures = 0
            state.playback_error = None
            state.history.append(previous)
            if state.loop_mode is LoopMode.TRACK:
                state.queue.appendleft(previous)
            elif state.loop_mode is LoopMode.QUEUE:
                state.queue.append(previous)
        state.current = None
        await self.refresh_queue(player.guild.id)
        if failed:
            await asyncio.sleep(min(2 * state.consecutive_failures, 15))
        if state.queue and not state.playback_blocked:
            try:
                await self.play_next(player.guild)
            except Exception:
                state.touch()
        else:
            message = state.player_message
            state.player_message = None
            channels = self.bot.get_cog('MusicChannels')
            if channels:
                await channels.show_idle(player.guild.id, message)

    @commands.Cog.listener()
    async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload) -> None:
        if payload.player and payload.player.guild:
            state = self.state(payload.player.guild.id)
            state.failed_track_ids.add(payload.track.identifier)
            state.playback_error = 'The source could not provide playable audio. This is not a playlist or database error.'
            if state.consecutive_failures == 0:
                channel = payload.player.guild.get_channel(state.text_channel_id) if state.text_channel_id else None
                if channel:
                    try:
                        await channel.send('The audio source failed to load this song. I will try a limited recovery; your saved playlists are safe.')
                    except discord.HTTPException:
                        pass
            if state.current and state.current.track.identifier == payload.track.identifier:
                await self.queue_recovery_candidate(state, state.current)

    @commands.Cog.listener()
    async def on_wavelink_track_stuck(self, payload: wavelink.TrackStuckEventPayload) -> None:
        if payload.player and payload.player.guild:
            self.state(payload.player.guild.id).failed_track_ids.add(payload.track.identifier)

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        print(f'Lavalink ready: {payload.node.identifier}')
        if payload.resumed:
            return
        for guild_id, state in list(self.states.items()):
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            if state.current:
                state.queue.appendleft(state.current)
                state.current = None
            if state.queue and isinstance(guild.voice_client, wavelink.Player):
                try:
                    await self.play_next(guild)
                except Exception:
                    state.touch()

    @tasks.loop(seconds=10)
    async def updater(self) -> None:
        now = time.monotonic()
        for guild_id, state in list(self.states.items()):
            guild = self.bot.get_guild(guild_id)
            if not guild or not isinstance(guild.voice_client, wavelink.Player):
                continue
            player = guild.voice_client
            if state.current:
                await self.refresh(guild_id)
            if state.queue and (not state.current) and (not player.playing) and (not player.paused):
                try:
                    await self.play_next(guild)
                except Exception:
                    state.touch()
            no_listeners = bool(player.channel) and all((member.bot for member in player.channel.members))
            idle = not player.playing and (not player.paused) and (now - state.last_activity > self.bot.settings.idle_timeout)
            if no_listeners or idle:
                message = state.player_message
                state.reset()
                await player.disconnect()
                channels = self.bot.get_cog('MusicChannels')
                if channels:
                    await channels.show_idle(guild_id, message)

    @updater.before_loop
    async def before_updater(self) -> None:
        await self.bot.wait_until_ready()

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, 'original', error)
        text = str(original) or 'I could not complete that action, Endmin.'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=False)
        else:
            await interaction.response.send_message(text, ephemeral=False)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
