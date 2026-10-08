"""Sistema Fogo do Poyo.

ETAPA 1 (esta): convite, aceitar/recusar e criação do Fogo.
Veja FOGO_CHECKLIST.md na raiz do projeto para o andamento das demais etapas.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from . import _fogo_db as db
from . import _fogo_visual as visual


# ------------------------------------------------------------------ convite


class ConviteView(discord.ui.LayoutView):
    """Mensagem de convite com os botões Aceitar / Recusar.

    Usa custom_id fixo por convite, então os botões continuam funcionando
    depois que o bot reinicia (a cog registra de novo os convites pendentes).
    """

    def __init__(
        self, convite_id: int, de_id: int, para_id: int
    ) -> None:
        super().__init__(timeout=None)
        self.convite_id = convite_id
        self.de_id = de_id
        self.para_id = para_id

        aceitar = discord.ui.Button(
            style=discord.ButtonStyle.success,
            label="Aceitar o Fogo",
            emoji=visual.EMOJI_FOGO,
            custom_id=f"poyo:fogo:convite:{convite_id}:aceitar",
        )
        recusar = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Recusar",
            emoji="🧊",
            custom_id=f"poyo:fogo:convite:{convite_id}:recusar",
        )
        aceitar.callback = self._aceitar
        recusar.callback = self._recusar

        blocos = visual.texto_convite(f"<@{de_id}>", f"<@{para_id}>")
        itens: list[discord.ui.Item] = []
        for indice, bloco in enumerate(blocos):
            if indice:
                itens.append(discord.ui.Separator())
            itens.append(discord.ui.TextDisplay(bloco))
        itens.append(discord.ui.Separator())
        itens.append(discord.ui.ActionRow(aceitar, recusar))

        self.add_item(
            discord.ui.Container(*itens, accent_colour=visual.COR_FOGO)
        )

    async def _so_convidado(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.para_id:
            return True
        await interaction.response.send_message(
            f"Só <@{self.para_id}> pode responder a esse convite.",
            ephemeral=True,
        )
        return False

    async def _aceitar(self, interaction: discord.Interaction) -> None:
        if not await self._so_convidado(interaction):
            return

        resultado, _fogo = await asyncio.to_thread(
            db.aceitar_convite, self.convite_id, interaction.channel_id
        )
        de, para = f"<@{self.de_id}>", f"<@{self.para_id}>"

        if resultado == "ok":
            novo = visual.cartao_fogo_criado(de, para)
        elif resultado == "expirado":
            novo = visual.cartao_convite_expirado(de, para)
        elif resultado == "ja_existe":
            novo = visual.cartao_ja_existe(de, para)
        else:  # ja_resolvido
            await interaction.response.send_message(
                "Esse convite já foi respondido.", ephemeral=True
            )
            return

        await interaction.response.edit_message(view=novo)
        self.stop()

    async def _recusar(self, interaction: discord.Interaction) -> None:
        if not await self._so_convidado(interaction):
            return

        resultado = await asyncio.to_thread(
            db.recusar_convite, self.convite_id
        )
        de, para = f"<@{self.de_id}>", f"<@{self.para_id}>"

        if resultado == "ok":
            novo = visual.cartao_convite_recusado(de, para)
        elif resultado == "expirado":
            novo = visual.cartao_convite_expirado(de, para)
        else:
            await interaction.response.send_message(
                "Esse convite já foi respondido.", ephemeral=True
            )
            return

        await interaction.response.edit_message(view=novo)
        self.stop()


# ---------------------------------------------------------------------- cog


class Fogo(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await asyncio.to_thread(db.iniciar)
        self.manutencao_convites.start()

    async def cog_unload(self) -> None:
        self.manutencao_convites.cancel()

    # ----------------------------------------------------------- utilidades

    async def _aviso(
        self, ctx: commands.Context, titulo: str, texto: str
    ) -> None:
        """Aviso curto: efêmero no slash, some sozinho no comando de prefixo."""
        view = visual.cartao_aviso(titulo, texto)
        if ctx.interaction is not None:
            await ctx.send(view=view, ephemeral=True)
        else:
            await ctx.send(view=view, delete_after=15)

    async def _marcar_mensagem_expirada(self, convite: sqlite3.Row) -> None:
        if convite["mensagem_id"] is None:
            return
        canal = self.bot.get_channel(convite["canal_id"])
        if canal is None:
            try:
                canal = await self.bot.fetch_channel(convite["canal_id"])
            except discord.HTTPException:
                return
        try:
            await canal.get_partial_message(convite["mensagem_id"]).edit(
                view=visual.cartao_convite_expirado(
                    f"<@{convite['de_id']}>", f"<@{convite['para_id']}>"
                )
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    # --------------------------------------------- manutenção dos convites

    @tasks.loop(minutes=5)
    async def manutencao_convites(self) -> None:
        """Expira convites vencidos e (re)registra os botões dos pendentes."""
        pendentes = await asyncio.to_thread(db.convites_pendentes)
        agora = time.time()

        for c in pendentes:
            if c["expira_em"] <= agora or c["mensagem_id"] is None:
                await asyncio.to_thread(db.marcar_expirado, c["id"])
                await self._marcar_mensagem_expirada(c)
                continue

            # add_view repetido para a mesma mensagem apenas atualiza o registro.
            self.bot.add_view(
                ConviteView(c["id"], c["de_id"], c["para_id"]),
                message_id=c["mensagem_id"],
            )

    @manutencao_convites.before_loop
    async def _antes_manutencao(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------- comandos

    @commands.hybrid_command(
        name="fogo",
        description="Convida um membro para acender um Fogo juntos.",
        extras={
            "categoria": "Fogo",
            "uso": ",fogo @membro",
            "descricao": (
                "Convida alguém para um Fogo, uma sequência de dias que "
                "vocês dois reacendem juntos todo dia às 00:00."
            ),
        },
    )
    @app_commands.describe(membro="Quem você quer chamar para o Fogo")
    @commands.guild_only()
    async def fogo(
        self, ctx: commands.Context, membro: discord.Member
    ) -> None:
        autor = ctx.author

        if membro.id == autor.id:
            await self._aviso(
                ctx,
                "Fogo é coisa de dois",
                "Você precisa chamar outra pessoa para acender o Fogo.",
            )
            return

        if membro.bot:
            await self._aviso(
                ctx,
                "Bots não acendem Fogo",
                "Nem o Poyo consegue reacender fogo com um bot. "
                "Chame uma pessoa!",
            )
            return

        ativo = await asyncio.to_thread(
            db.fogo_ativo_entre, ctx.guild.id, autor.id, membro.id
        )
        if ativo is not None:
            await self._aviso(
                ctx,
                "Vocês já têm um Fogo",
                f"{autor.mention} + {membro.mention} já estão com um Fogo "
                "ativo. Use `,fogos` para ver como ele está.",
            )
            return

        pendente = await asyncio.to_thread(
            db.convite_pendente_entre, ctx.guild.id, autor.id, membro.id
        )
        if pendente is not None:
            await self._aviso(
                ctx,
                "Já existe um convite",
                f"Já tem um convite aguardando resposta entre "
                f"{autor.mention} e {membro.mention}.",
            )
            return

        convite_id = await asyncio.to_thread(
            db.criar_convite,
            ctx.guild.id,
            ctx.channel.id,
            autor.id,
            membro.id,
        )

        try:
            mensagem = await ctx.send(
                view=ConviteView(convite_id, autor.id, membro.id),
                allowed_mentions=discord.AllowedMentions(users=[membro]),
            )
        except (discord.Forbidden, discord.HTTPException):
            await asyncio.to_thread(db.cancelar_convite, convite_id)
            raise

        await asyncio.to_thread(
            db.definir_mensagem_convite, convite_id, mensagem.id
        )

    @commands.hybrid_command(
        name="fogos",
        description="Mostra os Fogos ativos de você ou de outro membro.",
        extras={
            "categoria": "Fogo",
            "uso": ",fogos [@membro]",
            "descricao": "Lista os Fogos ativos e a sequência de cada um.",
        },
    )
    @app_commands.describe(membro="Membro para consultar (padrão: você)")
    @commands.guild_only()
    async def fogos(
        self,
        ctx: commands.Context,
        membro: Optional[discord.Member] = None,
    ) -> None:
        alvo = membro or ctx.author
        lista = await asyncio.to_thread(
            db.fogos_do_membro, ctx.guild.id, alvo.id
        )

        if not lista:
            quem = "Você ainda não tem" if alvo.id == ctx.author.id else (
                f"{alvo.mention} ainda não tem"
            )
            await self._aviso(
                ctx,
                "Nenhum Fogo por aqui",
                f"{quem} nenhum Fogo ativo. "
                "Que tal chamar alguém com `,fogo @membro`?",
            )
            return

        linhas: list[str] = []
        for f in lista[:15]:
            parceiro = f["usuario_b"] if f["usuario_a"] == alvo.id else f["usuario_a"]
            nome = f"  ·  **{f['nome']}**" if f["nome"] else ""
            if f["sequencia"] == 0:
                estado = "aguardando a primeira chama"
            else:
                estado = visual.dias(f["sequencia"])
            linhas.append(
                f"{visual.EMOJI_FOGO} {alvo.mention} + <@{parceiro}>{nome}\n"
                f"-# Sequência: {estado}"
            )

        if len(lista) > 15:
            linhas.append(f"-# ... e mais {len(lista) - 15} Fogo(s).")

        await ctx.send(
            view=visual.CartaoFogo(
                f"## {visual.EMOJI_FOGO_FELIZ} Fogos de {alvo.display_name}",
                "\n\n".join(linhas),
                cor=visual.COR_FOGO,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fogo(bot))
