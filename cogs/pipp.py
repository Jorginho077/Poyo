"""Comando ,pipp e /pipp — mostra o IP público e a(s) porta(s) da VM.

Colocar em cogs/pipp.py (o bot carrega tudo em cogs/ sozinho, só precisa reiniciar).

Funciona de dois jeitos: `,pipp` (prefixo) e `/pipp` (slash).
Segurança: só o dono do bot usa, e a resposta vai na DM (IP não deve ficar
num canal de servidor). No canal só aparece um aviso curto que some sozinho.

Quem é "dono do bot": quem está em SITE_DONOS_DO_BOT (a mesma lista da API do site)
ou o dono da aplicação no Discord. Quem não for dono recebe um aviso curto com o
próprio ID (e o console imprime `[pipp] negado ...`). PIPP_AVISAR_NEGADO=0 deixa mudo.

.env (opcional):
    SITE_DONOS_DO_BOT=123,456   # IDs autorizados (já usado pela API do site)
    SITE_API_PORT=8787          # porta da API do site
    SITE_API_URL=https://...    # endereço público da API, se tiver
    PIPP_PORTAS=25565,3000      # outras portas que você queira listar
"""

from __future__ import annotations

import os

import aiohttp
import discord
from discord.ext import commands

try:  # mesma fonte de verdade da API do site
    from ._site_config import API_URL, DONOS_DO_BOT, HOST, PORT
except Exception:  # cog funciona mesmo se a API do site não existir
    API_URL, DONOS_DO_BOT, HOST = "", (), "127.0.0.1"
    PORT = int(os.getenv("SITE_API_PORT", "8787"))

SERVICOS_IP = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)


def _portas() -> list[str]:
    portas = [str(PORT)]
    extras = os.getenv("PIPP_PORTAS", "")
    portas += [p.strip() for p in extras.split(",") if p.strip()]
    vistas: list[str] = []
    for p in portas:
        if p.isdigit() and p not in vistas:
            vistas.append(p)
    return vistas


def _autorizados() -> set[int]:
    ids = {int(i) for i in DONOS_DO_BOT if str(i).isdigit()}
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
        if usuario.id in _autorizados():
            return True
        try:
            return await self.bot.is_owner(usuario)
        except Exception:
            return False

    @commands.hybrid_command(
        name="pipp",
        description="IP público e porta da VM (só o dono do bot, via DM).",
        hidden=True,
    )
    async def pipp(self, ctx: commands.Context):
        """IP público e porta da VM (só o dono do bot, via DM)."""
        if not await self._pode(ctx.author):
            print(
                f"[pipp] negado para {ctx.author} ({ctx.author.id}). "
                "Pra liberar, ponha esse ID em SITE_DONOS_DO_BOT no .env."
            )
            if os.getenv("PIPP_AVISAR_NEGADO", "1") != "0":
                # avisa quem tentou (some sozinho). Pra ficar mudo: PIPP_AVISAR_NEGADO=0
                await ctx.send(
                    "🔒 Só o dono do bot usa esse comando.\n"
                    f"-# Seu ID: `{ctx.author.id}`. Se você é o dono, ponha ele em "
                    "`SITE_DONOS_DO_BOT` no `.env` e reinicie o bot.",
                    delete_after=20,
                )
            elif ctx.interaction is not None:  # não deixa o "pensando..." pendurado
                try:
                    await ctx.interaction.delete_original_response()
                except discord.HTTPException:
                    pass
            return

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
        if API_URL:
            emb.add_field(name="URL pública da API", value=f"`{API_URL}`", inline=False)
        if HOST in ("127.0.0.1", "localhost"):
            emb.add_field(
                name="⚠️ Atenção",
                value=(
                    f"A API do site escuta só em `{HOST}:{PORT}`, então `IP:porta` "
                    "**não** abre de fora. Use túnel/proxy (Caddy, Cloudflare Tunnel) "
                    "ou troque `SITE_API_HOST` e libere a porta no firewall da Oracle."
                ),
                inline=False,
            )
        emb.set_footer(text="IP pode mudar se a VM não tiver IP reservado.")

        try:
            await ctx.author.send(embed=emb)
        except discord.Forbidden:
            await ctx.send(
                "Não consegui te chamar na DM. Libere DMs deste servidor e tente de novo.",
                delete_after=10,
            )
            return

        # a mensagem do comando já foi apagada pelo bot, então avisa com texto que some
        if ctx.guild:
            await ctx.send("✅ Mandei na sua DM.", delete_after=6)


async def setup(bot: commands.Bot):
    await bot.add_cog(Pipp(bot))
