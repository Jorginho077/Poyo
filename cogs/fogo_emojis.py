"""Emojis do Poyo para o sistema Fogo (Etapa 5).

Ao ligar o bot, esta cog:

1. Garante os emojis do Poyo feliz e com frio como **emojis do aplicativo**
   (valem em qualquer servidor). Se ainda não existirem, envia os PNGs da
   pasta `assets/`. Eles aparecem nos textos e como imagem ao lado dos cartões.
2. Procura nos servidores onde o Poyo está os emojis `poyo_coracao`,
   `poyo_piscadinha` e `poyo_sono`, para enfeitar o resto das mensagens.

Se algum der errado (sem permissão, sem internet, arquivo faltando), o Fogo
continua funcionando com os emojis padrão (🔥, 🥶...). Nada aqui derruba o bot.

Quer usar outro emoji? Defina a variável no .env e ela vence o automático:
FOGO_EMOJI_FELIZ, FOGO_EMOJI_TRISTE, FOGO_EMOJI_CONVITE, FOGO_EMOJI_CORACAO,
FOGO_EMOJI_ESPERA (formato `<:nome:123456789>` ou `<a:nome:123456789>`).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import discord
from discord.ext import commands

from . import _fogo_visual as visual

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# Poyo feliz / com frio: (variável do .env, nome do emoji do app, arquivo,
# constante do texto, constante da imagem do cartão)
POYOS = (
    ("FOGO_EMOJI_FELIZ", "poyo_fogo_feliz", "EMOJI_FOGO_FELIZ", "IMG_FELIZ"),
    ("FOGO_EMOJI_TRISTE", "poyo_fogo_triste", "EMOJI_FOGO_TRISTE", "IMG_TRISTE"),
)

# Emojis que já existem no servidor, achados pelo nome.
DO_SERVIDOR = (
    ("FOGO_EMOJI_CONVITE", "poyo_piscadinha", "EMOJI_CONVITE"),
    ("FOGO_EMOJI_CORACAO", "poyo_coracao", "EMOJI_CORACAO"),
    ("FOGO_EMOJI_ESPERA", "poyo_sono", "EMOJI_ESPERA"),
)


def _url_do_texto(texto: str) -> str | None:
    """URL da imagem de um emoji customizado no formato <:nome:id>."""
    try:
        emoji = discord.PartialEmoji.from_str(texto)
    except Exception:
        return None
    return emoji.url if emoji.id else None


class FogoEmojis(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._tarefa: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self._tarefa = asyncio.create_task(self._preparar())

    async def cog_unload(self) -> None:
        if self._tarefa is not None:
            self._tarefa.cancel()

    async def _preparar(self) -> None:
        try:
            await self.bot.wait_until_ready()
            await self._poyos()
            self._do_servidor()
        except asyncio.CancelledError:
            raise
        except Exception as erro:  # o Fogo funciona sem os enfeites
            print(f"Fogo emojis: não consegui preparar os emojis: {erro!r}")

    async def _poyos(self) -> None:
        try:
            existentes = {
                e.name: e for e in await self.bot.fetch_application_emojis()
            }
        except (discord.HTTPException, discord.ClientException) as erro:
            print(f"Fogo emojis: não consegui listar os emojis do app: {erro!r}")
            existentes = {}

        for variavel, nome, constante, imagem in POYOS:
            manual = os.getenv(variavel)
            if manual:  # o .env manda; só descobre a imagem do cartão
                if not getattr(visual, imagem):
                    setattr(visual, imagem, _url_do_texto(manual))
                continue

            emoji = existentes.get(nome)
            arquivo = ASSETS / f"{nome}.png"
            if emoji is None and arquivo.exists():
                try:
                    emoji = await self.bot.create_application_emoji(
                        name=nome, image=arquivo.read_bytes()
                    )
                    print(f"Fogo emojis: emoji do app '{nome}' criado.")
                except (discord.HTTPException, discord.ClientException) as erro:
                    print(f"Fogo emojis: não consegui criar '{nome}': {erro!r}")

            if emoji is not None:
                setattr(visual, constante, str(emoji))
                setattr(visual, imagem, str(emoji.url))

    def _do_servidor(self) -> None:
        for variavel, nome, constante in DO_SERVIDOR:
            if os.getenv(variavel):
                continue
            emoji = discord.utils.get(self.bot.emojis, name=nome)
            if emoji is not None and emoji.available:
                setattr(visual, constante, str(emoji))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FogoEmojis(bot))
