from __future__ import annotations
import time
import discord
from discord.ext import commands
GUIDE_COOLDOWN = 10

class Guide(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cooldowns: dict[tuple[int, int], float] = {}

    @staticmethod
    def embed(user: discord.abc.User) -> discord.Embed:
        embed = discord.Embed(title='the music room music guide', description=f'{user.mention}, I am **Laevatain**. I can join you with music, favorites, and personal playlists. Here is the operations guide.', color=3447003)
        embed.add_field(name='Start listening', value='Join a voice channel and use **`/play`** in the music channel.\nI accept names, YouTube links, YouTube playlists, and Spotify songs.', inline=False)
        embed.add_field(name='Player controls', value='`/pause` · `/resume` · `/skip` · `/stop` · `/queue`\n`/remove` · `/loop` · `/shuffle` · `/volume` · `/seek` · `/disconnect`', inline=False)
        embed.add_field(name='Favorites and profile', value='Press **❤ Like** in the player and view them with `/likes`.\nUse `/likes remove:` with a song number to remove it.\n`/profile` posts your profile; its `image` option changes your personal cover.', inline=False)
        embed.add_field(name='Your playlists', value='`/playlist create` · `/playlist add` · `/playlist remove`\n`/playlist play` · `/playlist delete` · `/playlist image`\n`/playlist list` posts a collection with pages, links, likes, and the **🎧 Listen to playlist** button.', inline=False)
        embed.add_field(name='Music channel', value='Music operations take place in the assigned channel. Profiles, likes, and playlists can be shown elsewhere; **🎧 Listen to playlist** also works from any channel.', inline=False)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text='LAEVATAIN • MUSIC LAB ASSISTANT')
        return embed

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not self.bot.user or self.bot.user not in message.mentions:
            return
        guild_id = message.guild.id if message.guild else 0
        key = (guild_id, message.author.id)
        now = time.monotonic()
        if now - self.cooldowns.get(key, 0.0) < GUIDE_COOLDOWN:
            return
        self.cooldowns[key] = now
        await message.reply(embed=self.embed(message.author), mention_author=False, allowed_mentions=discord.AllowedMentions.none())

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Guide(bot))
