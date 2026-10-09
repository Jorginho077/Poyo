"""Lendários do Fogo (Etapa 4).

As duas primeiras duplas a chegarem a 500 dias de Fogo viram Lendários e
ficam registradas na bio do Poyo. O banco decide a vaga (cogs/_fogo_db.py,
dentro do clique que completa o dia); esta cog avisa no canal, grava a bio e
repete o que falhou.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from . import _fogo_db as db
from . import _fogo_lendarios as lend
from . import _fogo_visual as visual


class FogoLendarios(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._travas: dict[int, asyncio.Lock] = {}
        self._trava_bio = asyncio.Lock()

    async def cog_load(self) -> None:
        # Cria as tabelas mesmo se esta cog carregar antes da cog Fogo.
        await asyncio.to_thread(db.iniciar)
        self.manutencao_lendarios.start()

    async def cog_unload(self) -> None:
        self.manutencao_lendarios.cancel()

    def _trava(self, fogo_id: int) -> asyncio.Lock:
        return self._travas.setdefault(fogo_id, asyncio.Lock())

    # ------------------------------------------------------------ nomes

    async def _username(self, user_id: int) -> str:
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                return str(user_id)
        return user.name

    async def _garantir_nomes(self, marco) -> tuple[str, str]:
        """@usernames dos dois, gravados uma vez para não mudarem sozinhos."""
        if marco["nome_a"] and marco["nome_b"]:
            return marco["nome_a"], marco["nome_b"]
        a = await self._username(marco["usuario_a"])
        b = await self._username(marco["usuario_b"])
        await asyncio.to_thread(db.gravar_nomes_marco, marco["fogo_id"], a, b)
        return a, b

    # ------------------------------------------------------------ aviso

    async def anunciar(self, fogo_id: int) -> None:
        """Avisa no canal (uma vez) e, se tem vaga, grava na bio."""
        async with self._trava(fogo_id):
            marco = await asyncio.to_thread(db.obter_marco, fogo_id)
            if marco is None:
                return

            if not marco["anunciado"]:
                await self._garantir_nomes(marco)
                if marco["posicao"] is not None:
                    ocupadas = len(await asyncio.to_thread(db.lendarios))
                    cartao = lend.cartao_lendario(marco, ocupadas)
                else:
                    ja = await asyncio.to_thread(
                        db.dupla_e_lendaria,
                        marco["usuario_a"],
                        marco["usuario_b"],
                    )
                    cartao = lend.cartao_marco_sem_vaga(marco, ja)

                if await self._enviar(marco, cartao):
                    await asyncio.to_thread(db.marcar_anunciado, fogo_id)
                else:
                    await asyncio.to_thread(
                        db.falha_anuncio,
                        fogo_id,
                        lend.ANUNCIO_MAX_TENTATIVAS,
                    )

        if marco["posicao"] is not None and not marco["bio_ok"]:
            await self.sincronizar_bio()

    async def _enviar(self, marco, cartao) -> bool:
        canal = self.bot.get_channel(marco["canal_id"])
        if canal is None:
            try:
                canal = await self.bot.fetch_channel(marco["canal_id"])
            except discord.HTTPException:
                return False
        try:
            await canal.send(
                view=cartao,
                allowed_mentions=discord.AllowedMentions(
                    users=[
                        discord.Object(marco["usuario_a"]),
                        discord.Object(marco["usuario_b"]),
                    ]
                ),
            )
        except (discord.Forbidden, discord.HTTPException) as erro:
            print(f"Lendários: não consegui avisar no canal: {erro!r}")
            return False
        return True

    # -------------------------------------------------------------- bio

    async def sincronizar_bio(self) -> tuple[bool, str]:
        """Reescreve a bio do Poyo com os Lendários, sem apagar a abertura.

        Retorna (deu_certo, texto_ou_erro).
        """
        async with self._trava_bio:
            lista = await asyncio.to_thread(db.lendarios)
            if not lista:
                return False, "Ainda não há Lendários."

            duplas: list[tuple[str, str, int]] = []
            for m in lista:
                a, b = await self._garantir_nomes(m)
                duplas.append((a, b, m["dias"]))

            try:
                info = await self.bot.application_info()
                atual = info.description or ""

                # Cópia de segurança do texto original (uma vez, antes de o
                # bloco dos Lendários existir), caso precise voltar atrás.
                if lend.BIO_TITULO not in atual and atual.strip():
                    guardado = await asyncio.to_thread(
                        db.config_obter, "bio_original"
                    )
                    if guardado != atual:
                        await asyncio.to_thread(
                            db.config_gravar, "bio_original", atual
                        )

                abertura = lend.abertura_da_bio(
                    atual, os.getenv("FOGO_BIO_ABERTURA")
                )
                texto = lend.montar_bio(abertura, duplas)

                if texto != atual:
                    await info.edit(
                        description=texto,
                        reason="Lendários do Fogo",
                    )
            except (discord.HTTPException, discord.Forbidden) as erro:
                print(f"Lendários: não consegui atualizar a bio: {erro!r}")
                return False, f"Erro do Discord: {erro}"
            except Exception as erro:  # ex.: versão antiga do discord.py
                print(f"Lendários: erro inesperado na bio: {erro!r}")
                return False, f"Erro inesperado: {erro!r}"

            await asyncio.to_thread(db.marcar_bio_ok)
            return True, texto

    # ------------------------------------------------------ manutenção

    @tasks.loop(minutes=5)
    async def manutencao_lendarios(self) -> None:
        """Repete avisos e gravações de bio que falharam antes."""
        try:
            for m in await asyncio.to_thread(db.marcos_sem_anuncio):
                await self.anunciar(m["fogo_id"])
            if await asyncio.to_thread(db.marcos_sem_bio):
                await self.sincronizar_bio()
        except Exception as erro:
            print(f"Lendários: erro na manutenção: {erro!r}")

    @manutencao_lendarios.before_loop
    async def _antes_manutencao(self) -> None:
        await self.bot.wait_until_ready()

    # --------------------------------------------------------- comandos

    @commands.hybrid_command(
        name="lendarios",
        description="Mostra os Lendários do Fogo (as duplas dos 500 dias).",
        extras={
            "categoria": "Fogo",
            "uso": ",lendarios",
            "descricao": (
                "As 2 primeiras duplas a chegar a 500 dias entram na bio do Poyo."
            ),
        },
    )
    @commands.guild_only()
    async def lendarios(self, ctx: commands.Context) -> None:
        lista = await asyncio.to_thread(db.lendarios)

        melhor = None
        if len(lista) < lend.LENDARIO_VAGAS:
            ativos = await asyncio.to_thread(db.fogos_ativos)
            ja = {(m["usuario_a"], m["usuario_b"]) for m in lista}
            candidatos = [
                f
                for f in ativos
                if f["sequencia"] > 0
                and (f["usuario_a"], f["usuario_b"]) not in ja
            ]
            if candidatos:
                melhor = max(candidatos, key=lambda f: f["sequencia"])

        await ctx.send(
            view=lend.cartao_hall(lista, melhor),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.hybrid_command(
        name="fogobio",
        description="(Admin) Regrava a bio do Poyo com os Lendários do Fogo.",
        extras={
            "categoria": "Fogo",
            "uso": ",fogobio",
            "descricao": "Para administradores: força a atualização da bio.",
        },
    )
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def fogobio(self, ctx: commands.Context) -> None:
        ok, detalhe = await self.sincronizar_bio()
        if ok:
            cartao = visual.cartao_aviso(
                "Bio atualizada",
                f"Nova bio:\n>>> {detalhe}",
            )
        else:
            cartao = visual.cartao_aviso("Não deu certo", detalhe)
        if ctx.interaction is not None:
            await ctx.send(view=cartao, ephemeral=True)
        else:
            await ctx.send(view=cartao)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FogoLendarios(bot))
