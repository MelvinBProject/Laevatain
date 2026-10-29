import discord
from discord.ext import commands
from channels import OWNER_ID
BOT_VERSION = '0.1.0-local'

class Version(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name='version', hidden=True)
    async def version(self, ctx: commands.Context) -> None:
        if ctx.author.bot or ctx.author.id != OWNER_ID:
            return
        embed = discord.Embed(title=f'Laevatain v{BOT_VERSION}', color=3447003)
        embed.add_field(name='Status', value='🟢 Online', inline=False)
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text='LAEVATAIN · MUSIC LAB')
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Version(bot))
