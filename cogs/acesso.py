"""Liga o cargo de acesso total (veja `_acesso.py`) sem mexer no bot.py.

Ao carregar, esta cog troca o contexto dos comandos pelo `ContextoPoyo`,
que enxerga o cargo como todas as permissões. Ao descarregar, volta ao normal.
"""

from __future__ import annotations

from discord.ext import commands

from ._acesso import ContextoPoyo


class Acesso(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        original = self.bot.get_context

        async def get_context(origem, /, *, cls=None):
            return await original(origem, cls=cls or ContextoPoyo)

        # atributo da instancia vence o metodo da classe (vale pra prefixo e slash)
        self.bot.get_context = get_context

    async def cog_unload(self) -> None:
        self.bot.__dict__.pop("get_context", None)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Acesso(bot))
