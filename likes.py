from __future__ import annotations
import os
import sqlite3
from pathlib import Path
import discord
import wavelink
from discord import app_commands
from discord.ext import commands
DB_PATH = Path(os.getenv('LEVIATAN_DB_PATH', 'laevatain_playlists.db'))
LIKES_PAGE_SIZE = 10

def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection

def initialize() -> None:
    with connect() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS likes (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                user_id INTEGER NOT NULL,\n                title TEXT NOT NULL,\n                uri TEXT NOT NULL,\n                author TEXT NOT NULL DEFAULT '',\n                duration_ms INTEGER NOT NULL DEFAULT 0,\n                liked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                UNIQUE(user_id, uri)\n            )")
        connection.execute('CREATE INDEX IF NOT EXISTS idx_likes_user ON likes(user_id, liked_at DESC)')

def format_time(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    minutes, seconds = divmod(seconds, 60)
    return f'{minutes}:{seconds:02d}'

class LikesView(discord.ui.View):

    def __init__(self, rows: list[sqlite3.Row], owner: discord.User | discord.Member, viewer_id: int | None=None, public_navigation: bool=False) -> None:
        super().__init__(timeout=120)
        self.rows = rows
        self.owner = owner
        self.viewer_id = viewer_id or owner.id
        self.public_navigation = public_navigation
        self.page = 0
        self.message: discord.Message | None = None
        self._refresh_buttons()

    @property
    def pages(self) -> int:
        return max(1, (len(self.rows) + LIKES_PAGE_SIZE - 1) // LIKES_PAGE_SIZE)

    def _refresh_buttons(self) -> None:
        self.page = min(self.page, self.pages - 1)
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= self.pages - 1

    def embed(self) -> discord.Embed:
        start = self.page * LIKES_PAGE_SIZE
        visible = self.rows[start:start + LIKES_PAGE_SIZE]
        lines = []
        for index, row in enumerate(visible, start + 1):
            title = row['title'].replace('[', '\\[').replace(']', '\\]')
            artist = row['author'] or 'Unknown artist'
            lines.append(f"`{index:02d}`  **[{title}]({row['uri']})**\n\u3000\u3000{artist}  ·  `{format_time(row['duration_ms'])}`")
        embed = discord.Embed(description='\n\n'.join(lines), color=3447003)
        embed.set_author(name=f"{self.owner.display_name}'s favorites", icon_url=self.owner.display_avatar.url)
        embed.set_footer(text=f'Page {self.page + 1}/{self.pages}  •  {len(self.rows)} songs  •  LAEVATAIN')
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.public_navigation:
            return True
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message('This favorites library belongs to another user.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='◀', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label='▶', style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.page = min(self.pages - 1, self.page + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self) -> None:
        if not self.message:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            pass

class ClearLikesView(discord.ui.View):

    def __init__(self, owner_id: int, amount: int) -> None:
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.amount = amount

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This confirmation belongs to another user.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Yes, delete all', emoji='🗑️', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        with connect() as connection:
            deleted = connection.execute('DELETE FROM likes WHERE user_id=?', (self.owner_id,)).rowcount
        self.stop()
        await interaction.response.edit_message(content=f'Library cleared: I removed **{deleted} likes**.', embed=None, view=None)

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content='Operation cancelled. Your likes are untouched.', embed=None, view=None)

class Likes(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        initialize()

    def toggle(self, user_id: int, track: wavelink.Playable) -> bool:
        with connect() as connection:
            existing = connection.execute('SELECT id FROM likes WHERE user_id=? AND uri=?', (user_id, track.uri)).fetchone()
            if existing:
                connection.execute('DELETE FROM likes WHERE id=?', (existing['id'],))
                return False
            connection.execute('INSERT INTO likes(user_id,title,uri,author,duration_ms) VALUES(?,?,?,?,?)', (user_id, track.title, track.uri, track.author or '', track.length or 0))
            return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    @app_commands.command(name='likes', description='Show your favorite songs')
    @app_commands.describe(remove='Number of the song you want to remove from your likes', clear_all='Ask to delete all your favorite songs')
    async def likes_command(self, interaction: discord.Interaction, remove: app_commands.Range[int, 1] | None=None, clear_all: bool=False) -> None:
        if remove is not None and clear_all:
            await interaction.response.send_message('Choose either one song to remove or clear the entire collection.', ephemeral=True)
            return
        with connect() as connection:
            rows = connection.execute('SELECT * FROM likes WHERE user_id=? ORDER BY liked_at DESC, id DESC', (interaction.user.id,)).fetchall()
            if clear_all:
                if not rows:
                    await interaction.response.send_message('Your favorites library is already empty.', ephemeral=True)
                    return
                view = ClearLikesView(interaction.user.id, len(rows))
                await interaction.response.send_message(f'Do you want to permanently delete your **{len(rows)} likes**?', view=view, ephemeral=True)
                return
            if remove is not None:
                if remove > len(rows):
                    await interaction.response.send_message(f'Your likes only go up to **{len(rows)}**.', ephemeral=True)
                    return
                removed = rows[remove - 1]
                connection.execute('DELETE FROM likes WHERE id=? AND user_id=?', (removed['id'], interaction.user.id))
                await interaction.response.send_message(f"I removed **{removed['title']}** from your likes.", ephemeral=True)
                return
        if not rows:
            await interaction.response.send_message('Your favorites library is still empty, Endmin. Use **❤️ Like** in the player.', ephemeral=False)
            return
        view = LikesView(rows, interaction.user)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=False)
        view.message = await interaction.original_response()

    async def send_profile_likes(self, interaction: discord.Interaction, owner: discord.User | discord.Member) -> None:
        with connect() as connection:
            rows = connection.execute('SELECT * FROM likes WHERE user_id=? ORDER BY liked_at DESC, id DESC', (owner.id,)).fetchall()
        if not rows:
            await interaction.response.send_message(f'{owner.display_name} has not saved any favorite songs yet.', ephemeral=True)
            return
        view = LikesView(rows, owner, public_navigation=True)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=False)
        view.message = await interaction.original_response()

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, 'original', error)
        text = str(original) or 'I could not open your music library, Endmin.'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=False)
        else:
            await interaction.response.send_message(text, ephemeral=False)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Likes(bot))
