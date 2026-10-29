from __future__ import annotations
import os
import asyncio
import sqlite3
from pathlib import Path
from urllib.parse import quote_plus, urlparse
import discord
import wavelink
from discord import app_commands
from discord.ext import commands
from music import Music, search
DB_PATH = Path(os.getenv('LEVIATAN_DB_PATH', 'laevatain_playlists.db'))
MEDIA_PATH = Path(os.getenv('LEVIATAN_PLAYLIST_MEDIA_PATH', Path(__file__).with_name('playlist_media')))
MAX_PLAYLISTS = 3
MAX_SONGS = 50
MAX_MEDIA_SIZE = 8 * 1024 * 1024
PLAYLIST_PAGE_SIZE = 10
MEDIA_TYPES = {'image/gif': '.gif', 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}

def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    return connection

def initialize() -> None:
    with connect() as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE IF NOT EXISTS playlists (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                guild_id INTEGER NOT NULL,\n                user_id INTEGER NOT NULL,\n                name TEXT NOT NULL COLLATE NOCASE,\n                UNIQUE(guild_id, user_id, name)\n            )')
        columns = {row[1] for row in connection.execute('PRAGMA table_info(playlists)')}
        if 'image_path' not in columns:
            connection.execute('ALTER TABLE playlists ADD COLUMN image_path TEXT')
        if 'show_avatar' not in columns:
            connection.execute('ALTER TABLE playlists ADD COLUMN show_avatar INTEGER NOT NULL DEFAULT 1')
        MEDIA_PATH.mkdir(parents=True, exist_ok=True)
        for row in connection.execute('SELECT id,guild_id,user_id,image_path FROM playlists WHERE image_path IS NOT NULL').fetchall():
            filename = Path(str(row['image_path']).replace('\\', '/')).name
            portable = MEDIA_PATH / str(row['guild_id']) / str(row['user_id']) / filename
            if portable.is_file() and str(row['image_path']) != str(portable):
                connection.execute('UPDATE playlists SET image_path=? WHERE id=?', (str(portable), row['id']))
        connection.execute("CREATE TABLE IF NOT EXISTS songs (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,\n                title TEXT NOT NULL,\n                uri TEXT NOT NULL,\n                author TEXT DEFAULT '',\n                duration_ms INTEGER DEFAULT 0,\n                position INTEGER NOT NULL DEFAULT 0\n            )")
        connection.execute('CREATE TABLE IF NOT EXISTS playlist_likes (\n                playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,\n                guild_id INTEGER NOT NULL,\n                user_id INTEGER NOT NULL,\n                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                PRIMARY KEY(playlist_id, guild_id, user_id)\n            )')

def playlist(guild_id: int, user_id: int, name: str) -> sqlite3.Row | None:
    with connect() as connection:
        return connection.execute('SELECT * FROM playlists WHERE guild_id=? AND user_id=? AND name=? COLLATE NOCASE', (guild_id, user_id, name)).fetchone()

def playlists(guild_id: int, user_id: int) -> list[sqlite3.Row]:
    with connect() as connection:
        return connection.execute('SELECT * FROM playlists WHERE guild_id=? AND user_id=? ORDER BY name COLLATE NOCASE', (guild_id, user_id)).fetchall()

def songs(playlist_id: int) -> list[sqlite3.Row]:
    with connect() as connection:
        return connection.execute('SELECT * FROM songs WHERE playlist_id=? ORDER BY position, id', (playlist_id,)).fetchall()

def playlist_like_count(playlist_id: int, guild_id: int) -> int:
    with connect() as connection:
        return connection.execute('SELECT COUNT(*) FROM playlist_likes WHERE playlist_id=? AND guild_id=?', (playlist_id, guild_id)).fetchone()[0]

def toggle_playlist_like(playlist_id: int, guild_id: int, user_id: int) -> tuple[bool, int]:
    with connect() as connection:
        existing = connection.execute('SELECT 1 FROM playlist_likes WHERE playlist_id=? AND guild_id=? AND user_id=?', (playlist_id, guild_id, user_id)).fetchone()
        if existing:
            connection.execute('DELETE FROM playlist_likes WHERE playlist_id=? AND guild_id=? AND user_id=?', (playlist_id, guild_id, user_id))
            added = False
        else:
            connection.execute('INSERT INTO playlist_likes(playlist_id,guild_id,user_id) VALUES(?,?,?)', (playlist_id, guild_id, user_id))
            added = True
        count = connection.execute('SELECT COUNT(*) FROM playlist_likes WHERE playlist_id=? AND guild_id=?', (playlist_id, guild_id)).fetchone()[0]
    return (added, count)

def playlist_by_id(guild_id: int, user_id: int, playlist_id: int) -> sqlite3.Row | None:
    with connect() as connection:
        return connection.execute('SELECT * FROM playlists WHERE id=? AND guild_id=? AND user_id=?', (playlist_id, guild_id, user_id)).fetchone()

def safe_media_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    root = MEDIA_PATH.resolve()
    path = Path(raw_path).resolve()
    return path if path.is_relative_to(root) else None

def delete_playlist_media(target: sqlite3.Row) -> None:
    path = safe_media_path(target['image_path'] if 'image_path' in target.keys() else None)
    if path and path.is_file():
        path.unlink()

async def playlist_name_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not interaction.guild_id:
        return []
    available = playlists(interaction.guild_id, interaction.user.id)
    filtered = [item for item in available if current.lower() in item['name'].lower()]
    return [app_commands.Choice(name=item['name'], value=item['name']) for item in filtered]

class PlaylistMenu(discord.ui.Select):

    def __init__(self, view: PlaylistMenuView, available: list[sqlite3.Row]) -> None:
        action_text = 'delete' if view.action == 'delete' else 'play'
        options = []
        for item in available:
            count = len(songs(item['id']))
            options.append(discord.SelectOption(label=item['name'][:100], value=str(item['id']), description=f'{count}/50 songs', emoji='🗂️' if view.action == 'delete' else '▶️'))
        super().__init__(placeholder=f'Select the playlist you want to {action_text}', min_values=1, max_values=1, options=options)
        self.menu_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        playlist_id = int(self.values[0])
        await self.menu_view.cog.handle_menu_action(interaction, self.menu_view.action, playlist_id)

class PlaylistMenuView(discord.ui.View):

    def __init__(self, cog: Playlists, owner_id: int, action: str, available: list[sqlite3.Row]) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.owner_id = owner_id
        self.action = action
        self.add_item(PlaylistMenu(self, available))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This form belongs to another user.', ephemeral=False)
            return False
        return True

class PlaylistPagesView(discord.ui.View):

    def __init__(self, cog: Playlists, item: sqlite3.Row, tracks: list[sqlite3.Row], owner: discord.abc.User, attachment_name: str | None=None) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.item = item
        self.tracks = tracks
        self.owner = owner
        self.attachment_name = attachment_name
        self.page = 0
        self.likes = playlist_like_count(item['id'], item['guild_id'])
        self.refresh_buttons()

    @property
    def pages(self) -> int:
        return max(1, (len(self.tracks) + PLAYLIST_PAGE_SIZE - 1) // PLAYLIST_PAGE_SIZE)

    @staticmethod
    def youtube_url(track: sqlite3.Row) -> str:
        uri = (track['uri'] or '').strip()
        host = urlparse(uri).netloc.lower()
        if host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'music.youtube.com'}:
            return uri
        query = ' '.join((part for part in (track['title'], track['author']) if part))
        return f'https://www.youtube.com/results?search_query={quote_plus(query)}'

    def refresh_buttons(self) -> None:
        self.page = min(self.page, self.pages - 1)
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= self.pages - 1
        self.like.label = f'❤ {self.likes}'

    def embed(self) -> discord.Embed:
        start = self.page * PLAYLIST_PAGE_SIZE
        lines = []
        for number, track in enumerate(self.tracks[start:start + PLAYLIST_PAGE_SIZE], start + 1):
            title = track['title'].replace('[', '\\[').replace(']', '\\]')
            lines.append(f'`{number:02d}`  [{title}]({self.youtube_url(track)})')
        embed = discord.Embed(title=self.item['name'], description='\n'.join(lines) or 'This playlist is empty.', color=0xA92E44)
        if 'show_avatar' not in self.item.keys() or self.item['show_avatar']:
            embed.set_author(name=f"{self.owner.display_name}'s playlist", icon_url=self.owner.display_avatar.url)
            embed.set_thumbnail(url=self.owner.display_avatar.url)
        else:
            embed.set_author(name=f"{self.owner.display_name}'s playlist")
        embed.set_footer(text=f'Page {self.page + 1} / {self.pages} · {len(self.tracks)}/50 songs · LAEVATAIN')
        if self.attachment_name:
            embed.set_image(url=f'attachment://{self.attachment_name}')
        return embed

    @discord.ui.button(label='◀', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self.refresh_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label='▶', style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = min(self.pages - 1, self.page + 1)
        self.refresh_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label='❤ 0', style=discord.ButtonStyle.danger)
    async def like(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.user.id == self.item['user_id']:
            await interaction.response.send_message('This is your own playlist; likes belong to other listeners.', ephemeral=False)
            return
        try:
            added, self.likes = toggle_playlist_like(self.item['id'], self.item['guild_id'], interaction.user.id)
        except sqlite3.Error:
            await interaction.response.send_message('I could not register the like. Please try again.', ephemeral=False)
            return
        self.refresh_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)
        if added:
            await interaction.followup.send(f"{interaction.user.mention} liked the playlist **{self.item['name']}**. ❤", ephemeral=False)
        else:
            await interaction.followup.send(f"You removed your like from **{self.item['name']}**.", ephemeral=False)

    @discord.ui.button(label='Listen to playlist', emoji='🎧', style=discord.ButtonStyle.primary)
    async def play(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        channels = interaction.client.get_cog('MusicChannels')
        channel_id = channels.configured_channel_id(interaction.guild_id) if channels else None
        guild = interaction.guild
        output_channel = guild.get_channel(channel_id) if guild and channel_id else None
        if not isinstance(output_channel, discord.TextChannel):
            await interaction.response.send_message('No music channel is configured for this server yet.', ephemeral=False)
            return
        await interaction.response.defer(ephemeral=False, thinking=True)
        target = playlist_by_id(interaction.guild_id, self.item['user_id'], self.item['id'])
        if not target:
            await interaction.followup.send('This playlist no longer exists.', ephemeral=False)
            return
        try:
            await self.cog.play_selected(interaction, target, output_channel)
        except app_commands.CheckFailure as error:
            await interaction.followup.send(str(error), ephemeral=False)

class Playlists(commands.Cog):
    group = app_commands.Group(name='playlist', description='Manage your Laevatain playlists')
    show_group = app_commands.Group(name='show', description='Share your music library')

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        initialize()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.command and interaction.command.name == 'list':
            return True
        channels = self.bot.get_cog('MusicChannels')
        if channels and (not channels.is_music_channel(interaction)):
            raise app_commands.CheckFailure(channels.channel_error(interaction))
        return True

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, 'original', error)
        text = str(original) or 'I could not complete that action, Endmin.'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=False)
        else:
            await interaction.response.send_message(text, ephemeral=False)

    async def send_playlist_menu(self, interaction: discord.Interaction, action: str) -> None:
        available = playlists(interaction.guild_id, interaction.user.id)
        if not available:
            await interaction.response.send_message('You do not have any playlists yet. Use `/playlist create` to make one.', ephemeral=False)
            return
        verb = 'Delete a playlist' if action == 'delete' else 'Play a playlist'
        embed = discord.Embed(title=verb, description='Select an option in the menu below.', color=3447003)
        embed.set_footer(text=f'{len(available)}/3 playlists • LAEVATAIN')
        view = PlaylistMenuView(self, interaction.user.id, action, available)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

    async def handle_menu_action(self, interaction: discord.Interaction, action: str, playlist_id: int) -> None:
        target = playlist_by_id(interaction.guild_id, interaction.user.id, playlist_id)
        if not target:
            await interaction.response.edit_message(content='That playlist no longer exists.', embed=None, view=None)
            return
        if action == 'delete':
            count = len(songs(target['id']))
            delete_playlist_media(target)
            with connect() as connection:
                connection.execute('DELETE FROM playlists WHERE id=?', (target['id'],))
            await interaction.response.edit_message(content=f"Playlist **{target['name']}** deleted along with {count} songs.", embed=None, view=None)
            return
        await interaction.response.defer(ephemeral=False, thinking=True)
        await self.play_selected(interaction, target)

    async def play_selected(self, interaction: discord.Interaction, target: sqlite3.Row, output_channel: discord.TextChannel | None=None) -> None:
        stored = songs(target['id'])
        if not stored:
            await interaction.followup.send('That playlist is empty.', ephemeral=False)
            return
        music = self.bot.get_cog('Music')
        if not isinstance(music, Music):
            await interaction.followup.send('The music player is unavailable.', ephemeral=False)
            return
        await music.connect(interaction)
        resolved: list[wavelink.Playable] = []
        for row in stored:
            result = await search(row['uri'])
            if result:
                resolved.append(result[0])
        count = await music.enqueue(interaction, resolved, channel=output_channel)
        await interaction.followup.send(f"**{target['name']}**: {count} songs added to the queue.", ephemeral=False)

    @group.command(name='create', description='Create a playlist')
    @app_commands.describe(name='Name for your playlist')
    async def create(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 40]) -> None:
        current = playlists(interaction.guild_id, interaction.user.id)
        if len(current) >= MAX_PLAYLISTS:
            await interaction.response.send_message(f'You can save up to {MAX_PLAYLISTS} playlists, Endmin.', ephemeral=False)
            return
        try:
            with connect() as connection:
                connection.execute('INSERT INTO playlists(guild_id,user_id,name) VALUES(?,?,?)', (interaction.guild_id, interaction.user.id, name.strip()))
        except sqlite3.IntegrityError:
            await interaction.response.send_message('A playlist with that name already exists in your library.', ephemeral=False)
            return
        await interaction.response.send_message(f'Playlist **{name}** created, Endmin.', ephemeral=False)

    @group.command(name='add', description='Add a song or YouTube playlist')
    @app_commands.autocomplete(name=playlist_name_autocomplete)
    @app_commands.describe(name='Select one of your playlists', song='Song name or supported URL')
    async def add(self, interaction: discord.Interaction, name: str, song: str) -> None:
        await interaction.response.defer(ephemeral=False, thinking=True)
        target = playlist(interaction.guild_id, interaction.user.id, name.strip())
        if not target:
            await interaction.followup.send('I could not find that playlist, Endmin.', ephemeral=False)
            return
        found = await search(song)
        if not found:
            await interaction.followup.send('I could not find any songs with those details.', ephemeral=False)
            return
        existing = {row['uri'] for row in songs(target['id'])}
        added = 0
        with connect() as connection:
            next_position = len(existing) + 1
            for track in found:
                if len(existing) >= MAX_SONGS or track.uri in existing:
                    continue
                connection.execute('INSERT INTO songs(playlist_id,title,uri,author,duration_ms,position) VALUES(?,?,?,?,?,?)', (target['id'], track.title, track.uri, track.author or '', track.length or 0, next_position))
                existing.add(track.uri)
                next_position += 1
                added += 1
        await interaction.followup.send(f'Library updated: **{added} songs** added to **{name}**.', ephemeral=False)

    @group.command(name='remove', description='Remove a song by position')
    @app_commands.autocomplete(name=playlist_name_autocomplete)
    @app_commands.describe(name='Select one of your playlists', position='The number shown in the playlist')
    async def remove(self, interaction: discord.Interaction, name: str, position: app_commands.Range[int, 1]) -> None:
        target = playlist(interaction.guild_id, interaction.user.id, name.strip())
        rows = songs(target['id']) if target else []
        if not target or position > len(rows):
            await interaction.response.send_message('The playlist or position is invalid, Endmin.', ephemeral=False)
            return
        with connect() as connection:
            connection.execute('DELETE FROM songs WHERE id=?', (rows[position - 1]['id'],))
        await interaction.response.send_message(f"I removed **{rows[position - 1]['title']}** from the library.", ephemeral=False)

    @group.command(name='image', description='Add a GIF to a playlist')
    @app_commands.autocomplete(name=playlist_name_autocomplete)
    @app_commands.describe(name='Select one of your playlists', image='GIF up to 8 MB')
    async def image(self, interaction: discord.Interaction, name: str, image: discord.Attachment) -> None:
        target = playlist(interaction.guild_id, interaction.user.id, name.strip())
        if not target:
            await interaction.response.send_message('I could not find that playlist.', ephemeral=False)
            return
        extension = '.gif'
        if (image.content_type or '').lower() != 'image/gif':
            await interaction.response.send_message('The file must be a GIF.', ephemeral=False)
            return
        if image.size > MAX_MEDIA_SIZE:
            await interaction.response.send_message('The image exceeds the 8 MB limit.', ephemeral=False)
            return
        await interaction.response.defer(ephemeral=False, thinking=True)
        data = await image.read()
        from playlist_design import validate_gif
        from library import LibraryError
        try:
            await asyncio.to_thread(validate_gif, data)
        except LibraryError as exc:
            await interaction.followup.send(exc.message, ephemeral=False)
            return
        folder = MEDIA_PATH / str(interaction.guild_id) / str(interaction.user.id)
        folder.mkdir(parents=True, exist_ok=True)
        new_path = folder / f"{target['id']}{extension}"
        old_path = safe_media_path(target['image_path'])
        temporary = folder / f"{target['id']}.uploading"
        temporary.write_bytes(data)
        temporary.replace(new_path)
        if old_path and old_path != new_path and old_path.is_file():
            old_path.unlink()
        with connect() as connection:
            connection.execute('UPDATE playlists SET image_path=? WHERE id=? AND guild_id=? AND user_id=?', (str(new_path), target['id'], interaction.guild_id, interaction.user.id))
        await interaction.followup.send(f"Image updated for **{target['name']}**.", ephemeral=False)

    @group.command(name='delete', description='Delete a playlist')
    async def delete(self, interaction: discord.Interaction) -> None:
        await self.send_playlist_menu(interaction, 'delete')

    @group.command(name='list', description='Show one of your playlists')
    @app_commands.autocomplete(name=playlist_name_autocomplete)
    @app_commands.describe(name='Select the playlist you want to view')
    async def show(self, interaction: discord.Interaction, name: str) -> None:
        item = playlist(interaction.guild_id, interaction.user.id, name.strip())
        if not item:
            await interaction.response.send_message('I could not find that playlist.', ephemeral=False)
            return
        tracks = songs(item['id'])
        media = safe_media_path(item['image_path'])
        if media and media.is_file():
            filename = f"playlist_{item['id']}{media.suffix.lower()}"
            view = PlaylistPagesView(self, item, tracks, interaction.user, filename)
            await interaction.response.send_message(embed=view.embed(), file=discord.File(media, filename=filename), view=view, ephemeral=False)
        else:
            view = PlaylistPagesView(self, item, tracks, interaction.user)
            await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=False)

    @show_group.command(name='playlist', description='Show your playlist with its saved GIF and avatar settings')
    @app_commands.autocomplete(name=playlist_name_autocomplete)
    @app_commands.describe(name='Select the playlist you want to share')
    async def show_playlist(self, interaction: discord.Interaction, name: str) -> None:
        await self.show.callback(self, interaction, name)

    @group.command(name='play', description='Play one of your playlists')
    async def play(self, interaction: discord.Interaction) -> None:
        await self.send_playlist_menu(interaction, 'play')

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Playlists(bot))
