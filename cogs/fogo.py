"""Sistema Fogo do Poyo.

Etapa 1: convite, aceitar/recusar e criação do Fogo.
Etapa 2: ciclo diário (00:00), botão "Acender o Fogo", sequência e Fogo apagado.
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
from . import _fogo_tempo as tempo
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

        resultado, fogo = await asyncio.to_thread(
            db.aceitar_convite,
            self.convite_id,
            interaction.channel_id,
            tempo.hoje(),
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

        if resultado == "ok" and fogo is not None:
            cog = interaction.client.get_cog("Fogo")
            if cog is not None:
                await cog.abrir_painel(fogo["id"])

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


# ------------------------------------------------------------------- painel


class PainelView(visual.CartaoFogo):
    """Painel do dia com o botão "Acender o Fogo".

    O custom_id é fixo por Fogo, então o botão sobrevive a reinícios do bot
    (a cog registra de novo os painéis abertos ao ligar).
    """

    def __init__(self, fogo: sqlite3.Row) -> None:
        fogo_id = fogo["id"]

        botao = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Acender o Fogo",
            emoji=visual.EMOJI_FOGO,
            custom_id=f"poyo:fogo:acender:{fogo_id}",
        )

        async def ao_clicar(interaction: discord.Interaction) -> None:
            await acender(interaction, fogo_id)

        botao.callback = ao_clicar
        super().__init__(
            *visual.blocos_painel(fogo),
            cor=visual.COR_FOGO,
            botoes=[botao],
        )


async def acender(interaction: discord.Interaction, fogo_id: int) -> None:
    resultado, fogo = await asyncio.to_thread(
        db.acender, fogo_id, interaction.user.id, tempo.hoje()
    )

    if resultado == "ok":
        await interaction.response.edit_message(view=PainelView(fogo))
    elif resultado == "completo":
        await interaction.response.edit_message(
            view=visual.cartao_fogo_aceso(fogo)
        )
    else:
        avisos = {
            "nao_participa": "Esse Fogo não é seu! Só a dupla pode acender.",
            "ja_acendeu": "Você já acendeu hoje! Agora é esperar o outro.",
            "dia_encerrado": "Esse painel é de um dia que já passou.",
            "inativo": "Esse Fogo já se apagou...",
        }
        await interaction.response.send_message(
            avisos.get(resultado, "Não consegui acender agora."),
            ephemeral=True,
        )


# ---------------------------------------------------------------------- cog


class Fogo(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._travas: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        await asyncio.to_thread(db.iniciar)
        self.manutencao_convites.start()
        self.ciclo_diario.start()

    async def cog_unload(self) -> None:
        self.manutencao_convites.cancel()
        self.ciclo_diario.cancel()

    def _trava(self, fogo_id: int) -> asyncio.Lock:
        return self._travas.setdefault(fogo_id, asyncio.Lock())

    async def _canal(self, canal_id: int):
        canal = self.bot.get_channel(canal_id)
        if canal is None:
            try:
                canal = await self.bot.fetch_channel(canal_id)
            except discord.HTTPException:
                return None
        return canal

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

    # ----------------------------------------------------- ciclo diário

    async def abrir_painel(self, fogo_id: int) -> None:
        """Manda o painel do dia (se o Fogo ainda não tiver um)."""
        async with self._trava(fogo_id):
            fogo = await asyncio.to_thread(db.obter_fogo, fogo_id)
            if fogo is None or not fogo["ativo"] or fogo["painel_id"]:
                return

            canal = await self._canal(fogo["canal_id"])
            if canal is None:
                return

            try:
                mensagem = await canal.send(
                    view=PainelView(fogo),
                    allowed_mentions=discord.AllowedMentions(
                        users=[
                            discord.Object(fogo["usuario_a"]),
                            discord.Object(fogo["usuario_b"]),
                        ]
                    ),
                )
            except (discord.Forbidden, discord.HTTPException) as erro:
                print(f"Fogo {fogo_id}: não consegui enviar o painel: {erro!r}")
                return

            await asyncio.to_thread(db.definir_painel, fogo_id, mensagem.id)

    async def _apagar(self, fogo: sqlite3.Row) -> None:
        """Fogo apagou: encerra o painel antigo e avisa a dupla."""
        canal = await self._canal(fogo["canal_id"])
        if canal is None:
            return

        if fogo["painel_id"]:
            try:
                await canal.get_partial_message(fogo["painel_id"]).edit(
                    view=visual.cartao_painel_encerrado(fogo)
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        try:
            await canal.send(
                view=visual.cartao_fogo_apagado(fogo),
                allowed_mentions=discord.AllowedMentions(
                    users=[
                        discord.Object(fogo["usuario_a"]),
                        discord.Object(fogo["usuario_b"]),
                    ]
                ),
            )
        except (discord.Forbidden, discord.HTTPException) as erro:
            print(f"Fogo {fogo['id']}: não consegui avisar o apagão: {erro!r}")

    @tasks.loop(seconds=20)
    async def ciclo_diario(self) -> None:
        """Vira o dia às 00:00 (horário de Brasília), sem depender de o bot
        estar ligado exatamente nessa hora: compara a data de cada Fogo."""
        hoje = tempo.hoje()

        for f in await asyncio.to_thread(db.fogos_ativos):
            try:
                resultado, fogo = await asyncio.to_thread(
                    db.virar_dia, f["id"], hoje
                )
                if resultado == "novo_dia":
                    await self.abrir_painel(f["id"])
                elif resultado == "apagou":
                    await self._apagar(fogo)
                elif f["painel_id"] is None:
                    # Painel que falhou ao enviar antes: tenta de novo.
                    await self.abrir_painel(f["id"])
            except Exception as erro:  # um Fogo com problema não trava os outros
                print(f"Fogo {f['id']}: erro no ciclo diário: {erro!r}")

    @ciclo_diario.before_loop
    async def _antes_ciclo(self) -> None:
        await self.bot.wait_until_ready()

        # Reativa os botões dos painéis que ainda estão esperando cliques.
        for f in await asyncio.to_thread(db.fogos_ativos):
            if f["painel_id"] and not db.dia_completo(f):
                self.bot.add_view(PainelView(f), message_id=f["painel_id"])

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
                estado = "ainda não começou"
            else:
                estado = visual.dias(f["sequencia"])
            hoje_ok = "✅ aceso hoje" if db.dia_completo(f) else "⏳ falta acender hoje"
            linhas.append(
                f"{visual.EMOJI_FOGO} {alvo.mention} + <@{parceiro}>{nome}\n"
                f"-# Sequência: {estado}  ·  {hoje_ok}"
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
