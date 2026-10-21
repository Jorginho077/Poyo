"""Comando ,pipp — mostra o IP público e a(s) porta(s) da VM.

Colocar em cogs/pipp.py (o bot carrega tudo em cogs/ sozinho).

Segurança: só o dono do bot usa, e a resposta vai na DM (IP não deve ficar
num canal de servidor). No canal, o bot só reage com ✅.

.env (opcional):
    SITE_API_PORT=8787          # já existe, é a porta da API do site
    PIPP_PORTAS=25565,3000      # outras portas que você queira listar
    SITE_DONOS_DO_BOT=123,456   # já existe, IDs extras autorizados
"""

from __future__ import annotations

import os

import aiohttp
import discord
from discord.ext import commands

SERVICOS_IP = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)


def _portas() -> list[str]:
    portas = [os.getenv("SITE_API_PORT", "8787").strip()]
    extras = os.getenv("PIPP_PORTAS", "")
    portas += [p.strip() for p in extras.split(",") if p.strip()]
    vistas: list[str] = []
    for p in portas:
        if p.isdigit() and p not in vistas:
            vistas.append(p)
    return vistas


def _autorizados() -> set[int]:
    ids = set()
    for p in os.getenv("SITE_DONOS_DO_BOT", "").split(","):
        p = p.strip()
        if p.isdigit():
            ids.add(int(p))
    return ids


async def _ip_publico() -> str | None:
    timeout = aiohttp.ClientTimeout(total=6)
    async with aiohttp.ClientSession(timeout=timeout) as sessao:
        for url in SERVICOS_IP:
            try:
                async with sessao.get(url) as resp:
                    if resp.status == 200:
                        ip = (await resp.text()).strip()
                        if ip:
                            return ip
            except Exception:
                continue
    return None


class Pipp(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _pode(self, usuario: discord.abc.User) -> bool:
        return usuario.id in _autorizados() or await self.bot.is_owner(usuario)

    @commands.command(name="pipp")
    async def pipp(self, ctx: commands.Context):
        """Mostra IP público e porta da VM (só dono do bot, via DM)."""
        if not await self._pode(ctx.author):
            return  # silencioso: não revela que o comando existe

        async with ctx.typing():
            ip = await _ip_publico()

        portas = _portas()
        emb = discord.Embed(title="🌐 Rede da VM", color=0xF8A5C2)
        emb.add_field(name="IP público", value=f"`{ip or 'não consegui obter'}`", inline=False)
        emb.add_field(
            name="Porta(s)",
            value=", ".join(f"`{p}`" for p in portas) or "—",
            inline=False,
        )
        if ip:
            emb.add_field(
                name="Endereço",
                value="\n".join(f"`{ip}:{p}`" for p in portas),
                inline=False,
            )
        emb.set_footer(text="IP pode mudar se a VM não tiver IP reservado.")

        try:
            await ctx.author.send(embed=emb)
        except discord.Forbidden:
            await ctx.reply(
                "Não consegui te chamar na DM. Libere DMs deste servidor e tente de novo.",
                mention_author=False,
            )
            return

        if ctx.guild:
            try:
                await ctx.message.add_reaction("✅")
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Pipp(bot))
