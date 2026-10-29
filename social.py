from __future__ import annotations
import sqlite3
import os
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands
from playlists import DB_PATH, MAX_MEDIA_SIZE, MEDIA_TYPES
PROFILE_MEDIA_PATH = Path(os.getenv('LEVIATAN_PROFILE_MEDIA_PATH', Path(__file__).with_name('profile_media')))

def safe_profile_media_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    root = PROFILE_MEDIA_PATH.resolve()
    path = Path(raw_path).resolve()
    return path if path.is_relative_to(root) else None

def initialize_profile_media() -> None:
    PROFILE_MEDIA_PATH.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=15) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE IF NOT EXISTS profile_media (\n                guild_id INTEGER NOT NULL,\n                user_id INTEGER NOT NULL,\n                image_path TEXT NOT NULL,\n                PRIMARY KEY(guild_id, user_id)\n            )')
        for row in connection.execute('SELECT guild_id,user_id,image_path FROM profile_media').fetchall():
            filename = Path(str(row['image_path']).replace('\\', '/')).name
            portable = PROFILE_MEDIA_PATH / str(row['guild_id']) / filename
            if portable.is_file() and str(row['image_path']) != str(portable):
                connection.execute('UPDATE profile_media SET image_path=? WHERE guild_id=? AND user_id=?', (str(portable), row['guild_id'], row['user_id']))

class ProfileView(discord.ui.View):

    def __init__(self, owner: discord.User | discord.Member) -> None:
        super().__init__(timeout=120)
        self.owner = owner

    @discord.ui.button(label='View likes', emoji='❤', style=discord.ButtonStyle.primary)
    async def likes(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        likes_cog = interaction.client.get_cog('Likes')
        if not likes_cog or not hasattr(likes_cog, 'send_profile_likes'):
            await interaction.response.send_message('The favorites library is unavailable right now.', ephemeral=True)
            return
        await likes_cog.send_profile_likes(interaction, self.owner)

class Social(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        initialize_profile_media()

    @app_commands.command(name='profile', description='Show off your Laevatain music profile')
    @app_commands.describe(user='The user whose music profile you want to view', image='A custom image or GIF for your own profile (max. 8 MB)')
    async def profile(self, interaction: discord.Interaction, user: discord.Member | None=None, image: discord.Attachment | None=None) -> None:
        target = user or interaction.user
        guild_id = interaction.guild_id or 0
        if image and target.id != interaction.user.id:
            await interaction.response.send_message('You can only change the image on your own profile.', ephemeral=True)
            return
        if image:
            extension = MEDIA_TYPES.get((image.content_type or '').lower())
            if not extension:
                await interaction.response.send_message('The file must be PNG, JPG, WEBP, or GIF.', ephemeral=True)
                return
            if image.size > MAX_MEDIA_SIZE:
                await interaction.response.send_message('The image exceeds the 8 MB limit.', ephemeral=True)
                return
        await interaction.response.defer(ephemeral=False, thinking=True)
        with sqlite3.connect(DB_PATH, timeout=15) as connection:
            connection.row_factory = sqlite3.Row
            if image:
                data = await image.read()
                folder = PROFILE_MEDIA_PATH / str(guild_id)
                folder.mkdir(parents=True, exist_ok=True)
                new_path = folder / f'{target.id}{extension}'
                old_row = connection.execute('SELECT image_path FROM profile_media WHERE guild_id=? AND user_id=?', (guild_id, target.id)).fetchone()
                old_path = safe_profile_media_path(old_row['image_path'] if old_row else None)
                temporary = folder / f'{target.id}.uploading'
                temporary.write_bytes(data)
                temporary.replace(new_path)
                if old_path and old_path != new_path and old_path.is_file():
                    old_path.unlink()
                connection.execute('INSERT INTO profile_media(guild_id, user_id, image_path)\n                       VALUES(?, ?, ?)\n                       ON CONFLICT(guild_id, user_id)\n                       DO UPDATE SET image_path=excluded.image_path', (guild_id, target.id, str(new_path)))
            likes = connection.execute('SELECT COUNT(*) FROM likes WHERE user_id=?', (target.id,)).fetchone()[0]
            user_playlists = connection.execute('SELECT * FROM playlists WHERE guild_id=? AND user_id=? ORDER BY name COLLATE NOCASE', (guild_id, target.id)).fetchall()
            playlist_songs = connection.execute('SELECT COUNT(*) FROM songs s\n                   JOIN playlists p ON p.id=s.playlist_id\n                   WHERE p.guild_id=? AND p.user_id=?', (guild_id, target.id)).fetchone()[0]
            playlist_counts = {row['playlist_id']: row['amount'] for row in connection.execute('SELECT p.id AS playlist_id, COUNT(s.id) AS amount\n                       FROM playlists p LEFT JOIN songs s ON s.playlist_id=p.id\n                       WHERE p.guild_id=? AND p.user_id=? GROUP BY p.id', (guild_id, target.id)).fetchall()}
            media_row = connection.execute('SELECT image_path FROM profile_media WHERE guild_id=? AND user_id=?', (guild_id, target.id)).fetchone()
        saved_total = likes + playlist_songs
        embed = discord.Embed(description=f'**{target.display_name}** has given **{likes} likes** to songs and keeps **{saved_total} saved songs** in their music profile.', color=3447003)
        embed.set_author(name='Music profile')
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name='Likes', value=f'`{likes}`', inline=True)
        embed.add_field(name='Playlists', value=f'`{len(user_playlists)}/3`', inline=True)
        embed.add_field(name='Saved songs', value=f'`{saved_total}`', inline=True)
        collection = '\n'.join((f"**{item['name']}**  ·  {playlist_counts.get(item['id'], 0)}/50" for item in user_playlists)) if user_playlists else 'No playlists have been created yet.'
        embed.add_field(name='Playlist collection', value=collection, inline=False)
        embed.set_footer(text='LAEVATAIN • MUSIC LAB PROFILE')
        profile_media = safe_profile_media_path(media_row['image_path'] if media_row else None)
        view = ProfileView(target)
        if profile_media and profile_media.is_file():
            filename = f'profile_{target.id}{profile_media.suffix.lower()}'
            embed.set_image(url=f'attachment://{filename}')
            await interaction.followup.send(embed=embed, file=discord.File(profile_media, filename=filename), view=view, ephemeral=False)
        else:
            await interaction.followup.send(embed=embed, view=view, ephemeral=False)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Social(bot))
