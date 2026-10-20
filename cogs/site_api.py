"""Cog que sobe a API do site dentro do próprio bot.

Etapa 1: servidor HTTP + login com Discord + /me + servidores.
Se faltar alguma variável do .env, a API simplesmente não sobe e o bot
continua normal (só avisa no console o que falta).
"""

from __future__ import annotations

import discord
from aiohttp import web
from discord.ext import commands, tasks

from . import _site_config as cfg
from . import _site_db as db
from ._site_rotas import criar_app


class SiteApi(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.runner: web.AppRunner | None = None

    async def cog_load(self) -> None:
        faltando = cfg.faltando()
        if faltando:
            print(f"[site_api] desligada, faltam no .env: {', '.join(faltando)}")
            return

        db.iniciar()
        self.runner = web.AppRunner(criar_app(self.bot))
        await self.runner.setup()
        await web.TCPSite(self.runner, cfg.HOST, cfg.PORT).start()
        self.limpeza.start()
        print(f"[site_api] no ar em http://{cfg.HOST}:{cfg.PORT}/api/v1")

    async def cog_unload(self) -> None:
        if self.limpeza.is_running():
            self.limpeza.cancel()
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None

    @tasks.loop(hours=6)
    async def limpeza(self) -> None:
        db.limpar_expirados()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self.runner is None:
            return
        for guild in self.bot.guilds:
            db.registrar_guild(guild.id)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self.runner is not None:
            db.registrar_guild(guild.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SiteApi(bot))
