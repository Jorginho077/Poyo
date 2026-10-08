"""Sistema Fogo do Poyo.

Etapa 1: convite, aceitar/recusar e criação do Fogo.
Etapa 2: ciclo diário (00:00), botão "Acender o Fogo", sequência e Fogo apagado.
Etapa 3: nome do Fogo (liberado aos 10 dias, sugerido por um e aceito pelo par).
Etapa 4: Lendários do Fogo (cogs/fogo_lendarios.py); aqui só há o aviso do 500º dia.
Etapa 5: o Poyo (feliz / com frio) aparece ao lado do texto dos cartões; veja
cogs/fogo_emojis.py e cogs/_fogo_visual.py.
Etapa 4.1: os avisos diários (painel, Fogo aceso, Fogo apagado) vão para a DM de
cada pessoa da dupla, e não mais para o canal. Se a DM estiver fechada, aquela
pessoa recebe o painel no canal do Fogo (marcando só ela).
Veja FOGO_CHECKLIST.md na raiz do projeto para o andamento das demais etapas.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from . import _fogo_db as db
from . import _fogo_lendarios as lendarios
from . import _fogo_nome as nomes
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

AVISO_DM_FECHADA = (
    "-# 💌 Sua DM está fechada, então o painel veio para o canal. "
    "Abra suas DMs com o Poyo para receber o aviso por lá!"
)


def _rodape(client: discord.Client, fogo: sqlite3.Row, modo: str) -> Optional[str]:
    """Linha pequena no fim do painel. `modo`: 'dm', 'canal' ou 'legado'."""
    if modo == "canal":
        return AVISO_DM_FECHADA
    if modo == "dm":
        # Na DM não há contexto: diz de qual servidor é o Fogo.
        guild = client.get_guild(fogo["guild_id"])
        if guild is not None:
            return f"-# 📍 Servidor: **{discord.utils.escape_markdown(guild.name)}**"
    return None


def _botoes_nome(fogo: sqlite3.Row) -> list[discord.ui.Item]:
    if fogo["sequencia"] >= nomes.NOME_MIN_DIAS and not fogo["nome"]:
        return [NomeBotao(fogo["id"])]
    return []


def _view_do_dia(
    client: discord.Client, fogo: sqlite3.Row, modo: str
) -> discord.ui.LayoutView:
    """Painel (falta alguém acender) ou card de Fogo aceso (os dois já acenderam)."""
    if db.dia_completo(fogo):
        return visual.cartao_fogo_aceso(
            fogo,
            botoes=_botoes_nome(fogo),
            rodape=_rodape(client, fogo, modo) if modo == "dm" else None,
        )
    return PainelView(fogo, _rodape(client, fogo, modo))


class AcenderBotao(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"poyo:fogo:acender:(?P<id>[0-9]+)",
):
    """Botão "Acender o Fogo".

    Dinâmico: o mesmo botão vale em qualquer mensagem (a DM de cada um, o
    canal, painéis antigos) e sobrevive a reinícios do bot, sem precisar
    registrar mensagem por mensagem.
    """

    def __init__(self, fogo_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.danger,
                label="Acender o Fogo",
                emoji=visual.EMOJI_FOGO,
                custom_id=f"poyo:fogo:acender:{fogo_id}",
            )
        )
        self.fogo_id = fogo_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "AcenderBotao":
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await acender(interaction, self.fogo_id)


class PainelView(visual.CartaoFogo):
    """Painel do dia com o botão "Acender o Fogo"."""

    def __init__(self, fogo: sqlite3.Row, rodape: Optional[str] = None) -> None:
        blocos = list(visual.blocos_painel(fogo))
        if rodape:
            blocos.append(rodape)
        super().__init__(
            *blocos,
            cor=visual.COR_FOGO,
            botoes=[AcenderBotao(fogo["id"])],
            imagem=visual.IMG_FELIZ,
        )


async def acender(interaction: discord.Interaction, fogo_id: int) -> None:
    resultado, fogo = await asyncio.to_thread(
        db.acender, fogo_id, interaction.user.id, tempo.hoje()
    )

    if resultado in ("ok", "completo"):
        # Descobre onde este painel mora (DM, canal de reserva ou painel
        # antigo de antes da Etapa 4.1) para escolher o rodapé certo.
        modo = "legado"
        mensagem_id = interaction.message.id if interaction.message else None
        for p in await asyncio.to_thread(db.paineis_do_fogo, fogo_id):
            if p["mensagem_id"] == mensagem_id:
                modo = "dm" if p["dm"] else "canal"
                break

        await interaction.response.edit_message(
            view=_view_do_dia(interaction.client, fogo, modo)
        )

        cog = interaction.client.get_cog("Fogo")
        if cog is not None:
            try:  # o painel do par acompanha
                await cog.atualizar_paineis(fogo, excluir=mensagem_id)
            except Exception as erro:
                print(f"Fogo {fogo_id}: erro ao atualizar o painel do par: {erro!r}")

        # Etapa 4: o banco já decidiu a vaga de Lendário dentro do clique;
        # a cog dos Lendários só avisa no canal e grava a bio.
        if resultado == "completo" and fogo["sequencia"] >= lendarios.LENDARIO_DIAS:
            cog = interaction.client.get_cog("FogoLendarios")
            if cog is not None:
                try:
                    await cog.anunciar(fogo_id)
                except Exception as erro:  # nunca atrapalha o clique
                    print(f"Fogo {fogo_id}: erro ao anunciar marco: {erro!r}")
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


# -------------------------------------------------------------- nome do Fogo


def _parceiro(fogo: sqlite3.Row, user_id: int) -> int:
    return fogo["usuario_b"] if user_id == fogo["usuario_a"] else fogo["usuario_a"]


class NomeModal(discord.ui.Modal, title="Nome do Fogo"):
    nome = discord.ui.TextInput(
        label="Como vai se chamar o Fogo de vocês?",
        placeholder="Ex.: Chaminha",
        min_length=nomes.NOME_MIN,
        max_length=nomes.NOME_MAX,
    )

    def __init__(self, fogo_id: int) -> None:
        super().__init__()
        self.fogo_id = fogo_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        valido, resultado = nomes.validar_nome(self.nome.value)
        if not valido:
            await interaction.response.send_message(resultado, ephemeral=True)
            return

        status, fogo = await asyncio.to_thread(
            db.propor_nome,
            self.fogo_id,
            interaction.user.id,
            resultado,
            time.time(),
            nomes.NOME_MIN_DIAS,
        )

        if status != "ok":
            avisos = {
                "inativo": "Esse Fogo já se apagou...",
                "nao_participa": "Esse Fogo não é seu!",
                "bloqueado": "Esse Fogo ainda não chegou aos "
                f"{nomes.NOME_MIN_DIAS} dias.",
                "igual": "O Fogo já se chama assim!",
            }
            await interaction.response.send_message(
                avisos.get(status, "Não consegui salvar o nome agora."),
                ephemeral=True,
            )
            return

        versao = fogo["proposta_n"]
        parceiro = _parceiro(fogo, interaction.user.id)
        cartao = visual.cartao_proposta_nome(
            fogo,
            interaction.user.id,
            resultado,
            botoes=[
                RespostaNomeBotao(fogo["id"], versao, "aceitar"),
                RespostaNomeBotao(fogo["id"], versao, "recusar"),
            ],
        )
        mencoes = discord.AllowedMentions(users=[discord.Object(parceiro)])

        if interaction.guild is not None:
            await interaction.response.send_message(
                view=cartao, allowed_mentions=mencoes
            )
            return

        # Aberto pela DM: o par não consegue clicar na DM de quem sugeriu,
        # então a sugestão é postada no canal do Fogo.
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("Fogo")
        canal = await cog._canal(fogo["canal_id"]) if cog else None
        try:
            if canal is None:
                raise discord.HTTPException(None, "canal indisponível")
            await canal.send(view=cartao, allowed_mentions=mencoes)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "Não consegui postar a sugestão no canal do Fogo. "
                "Tente com `,nomefogo` direto no servidor.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Sugestão enviada em {canal.mention}! "
            f"Agora é só esperar <@{parceiro}> aceitar. 🔥",
            ephemeral=True,
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        print(f"Fogo: erro no modal de nome: {error!r}")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Não consegui salvar o nome agora. Tente de novo.",
                ephemeral=True,
            )


class NomeBotao(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"poyo:fogo:nome:(?P<id>[0-9]+)",
):
    """Botão "Dar nome ao Fogo". Dinâmico: funciona mesmo depois de o bot
    reiniciar, sem precisar registrar mensagem por mensagem."""

    def __init__(self, fogo_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.primary,
                label="Dar nome ao Fogo",
                emoji="🏷️",
                custom_id=f"poyo:fogo:nome:{fogo_id}",
            )
        )
        self.fogo_id = fogo_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "NomeBotao":
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        fogo = await asyncio.to_thread(db.obter_fogo, self.fogo_id)

        if fogo is None or not fogo["ativo"]:
            aviso = "Esse Fogo já se apagou..."
        elif interaction.user.id not in (fogo["usuario_a"], fogo["usuario_b"]):
            aviso = "Só a dupla do Fogo pode escolher o nome."
        elif fogo["sequencia"] < nomes.NOME_MIN_DIAS:
            faltam = nomes.NOME_MIN_DIAS - fogo["sequencia"]
            aviso = (
                f"O nome libera com {nomes.NOME_MIN_DIAS} dias de Fogo. "
                f"Faltam {visual.dias(faltam)}!"
            )
        else:
            await interaction.response.send_modal(NomeModal(self.fogo_id))
            return

        await interaction.response.send_message(aviso, ephemeral=True)


class RespostaNomeBotao(
    discord.ui.DynamicItem[discord.ui.Button],
    template=(
        r"poyo:fogo:nomeresp:(?P<id>[0-9]+):(?P<versao>[0-9]+):"
        r"(?P<acao>aceitar|recusar)"
    ),
):
    """Botões Aceitar / Recusar da sugestão de nome."""

    def __init__(self, fogo_id: int, versao: int, acao: str) -> None:
        aceitar = acao == "aceitar"
        super().__init__(
            discord.ui.Button(
                style=(
                    discord.ButtonStyle.success
                    if aceitar
                    else discord.ButtonStyle.secondary
                ),
                label="Aceitar o nome" if aceitar else "Recusar",
                emoji=visual.EMOJI_FOGO if aceitar else "🧊",
                custom_id=f"poyo:fogo:nomeresp:{fogo_id}:{versao}:{acao}",
            )
        )
        self.fogo_id = fogo_id
        self.versao = versao
        self.acao = acao

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "RespostaNomeBotao":
        return cls(int(match["id"]), int(match["versao"]), match["acao"])

    async def callback(self, interaction: discord.Interaction) -> None:
        aceitar = self.acao == "aceitar"

        # Antes de resolver, guarda o nome sugerido para a mensagem final.
        antes = await asyncio.to_thread(db.obter_fogo, self.fogo_id)
        nome_sugerido = antes["nome_proposto"] if antes else None

        status, fogo = await asyncio.to_thread(
            db.responder_nome,
            self.fogo_id,
            self.versao,
            interaction.user.id,
            aceitar,
            time.time(),
            nomes.PROPOSTA_VALIDADE_SEGUNDOS,
        )

        if status == "aceito":
            await interaction.response.edit_message(
                view=visual.cartao_fogo_batizado(fogo)
            )
            cog = interaction.client.get_cog("Fogo")
            if cog is not None:  # o nome novo aparece nos painéis de hoje
                try:
                    await cog.atualizar_paineis(fogo)
                except Exception as erro:
                    print(f"Fogo {fogo['id']}: erro ao atualizar painéis: {erro!r}")
        elif status == "recusado":
            await interaction.response.edit_message(
                view=visual.cartao_nome_recusado(
                    fogo, nome_sugerido or "sugerido", interaction.user.id
                )
            )
        elif status == "expirada":
            await interaction.response.edit_message(
                view=visual.cartao_proposta_encerrada(
                    "Essa sugestão passou de 24 horas sem resposta."
                )
            )
        elif status == "antiga":
            await interaction.response.edit_message(
                view=visual.cartao_proposta_encerrada(
                    "Essa sugestão já foi respondida ou foi substituída "
                    "por outra."
                )
            )
        elif status == "inativo":
            await interaction.response.edit_message(
                view=visual.cartao_proposta_encerrada(
                    "O Fogo se apagou antes de a sugestão ser respondida."
                )
            )
        else:
            avisos = {
                "proprio": "Quem sugeriu o nome não pode aceitar. "
                "Aguarde seu par responder!",
                "nao_participa": "Só a dupla do Fogo pode responder.",
            }
            await interaction.response.send_message(
                avisos.get(status, "Não consegui responder agora."),
                ephemeral=True,
            )


# ---------------------------------------------------------------------- cog


class Fogo(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._travas: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        await asyncio.to_thread(db.iniciar)
        self.bot.add_dynamic_items(AcenderBotao, NomeBotao, RespostaNomeBotao)
        self.manutencao_convites.start()
        self.ciclo_diario.start()

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(AcenderBotao, NomeBotao, RespostaNomeBotao)
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

    def _mensagem(self, painel: sqlite3.Row) -> discord.PartialMessage:
        """Mensagem de um painel guardado (DM ou canal)."""
        canal = self.bot.get_partial_messageable(
            painel["canal_id"],
            type=discord.ChannelType.private if painel["dm"] else None,
        )
        return canal.get_partial_message(painel["mensagem_id"])

    async def _usuario(self, user_id: int) -> discord.User:
        return self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)

    async def _enviar_painel(self, fogo: sqlite3.Row, user_id: int) -> bool:
        """Entrega o painel de UMA pessoa: na DM, ou no canal se a DM estiver
        fechada. Devolve True se a mensagem foi entregue e registrada."""
        mensagem = None
        dm = True
        try:
            usuario = await self._usuario(user_id)
            mensagem = await usuario.send(view=_view_do_dia(self.bot, fogo, "dm"))
        except (discord.Forbidden, discord.NotFound):
            dm = False  # DM fechada (ou conta sumiu): usa o canal
        except discord.HTTPException as erro:
            print(f"Fogo {fogo['id']}: erro ao enviar DM para {user_id}: {erro!r}")
            return False  # falha passageira: o ciclo tenta de novo

        if mensagem is None:
            canal = await self._canal(fogo["canal_id"])
            if canal is None:
                return False
            try:
                mensagem = await canal.send(
                    view=_view_do_dia(self.bot, fogo, "canal"),
                    allowed_mentions=discord.AllowedMentions(
                        users=[discord.Object(user_id)]
                    ),
                )
            except (discord.Forbidden, discord.HTTPException) as erro:
                print(f"Fogo {fogo['id']}: não consegui enviar o painel: {erro!r}")
                return False

        await asyncio.to_thread(
            db.registrar_painel,
            fogo["id"],
            user_id,
            mensagem.channel.id,
            mensagem.id,
            dm,
        )
        return True

    async def abrir_painel(self, fogo_id: int) -> None:
        """Manda o painel do dia para cada pessoa da dupla (DM, ou canal se a
        DM estiver fechada). Quem já recebeu não recebe de novo."""
        async with self._trava(fogo_id):
            fogo = await asyncio.to_thread(db.obter_fogo, fogo_id)
            if fogo is None or not fogo["ativo"] or fogo["painel_id"]:
                return

            ja_tem = {
                p["usuario_id"]
                for p in await asyncio.to_thread(db.paineis_do_fogo, fogo_id)
            }
            for user_id in (fogo["usuario_a"], fogo["usuario_b"]):
                if user_id in ja_tem:
                    continue
                # Relê a cada envio: o primeiro pode ter acendido nesse meio tempo.
                atual = await asyncio.to_thread(db.obter_fogo, fogo_id)
                if atual is None or not atual["ativo"]:
                    return
                await self._enviar_painel(atual, user_id)

            paineis = await asyncio.to_thread(db.paineis_do_fogo, fogo_id)
            if len(paineis) >= 2:
                # Marca o dia como "painéis entregues" (senão o ciclo repete).
                await asyncio.to_thread(
                    db.definir_painel, fogo_id, paineis[0]["mensagem_id"]
                )

    async def atualizar_paineis(
        self, fogo: sqlite3.Row, excluir: Optional[int] = None
    ) -> None:
        """Deixa o painel de cada um igual ao estado atual (quem acendeu, nome).
        `excluir` é a mensagem que acabou de ser editada pelo próprio clique."""
        for p in await asyncio.to_thread(db.paineis_do_fogo, fogo["id"]):
            if p["mensagem_id"] == excluir:
                continue
            modo = "dm" if p["dm"] else "canal"
            try:
                await self._mensagem(p).edit(
                    view=_view_do_dia(self.bot, fogo, modo)
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    async def _apagar(self, fogo: sqlite3.Row) -> None:
        """Fogo apagou: encerra os painéis antigos e avisa cada um da dupla
        na DM (no canal, só para quem estiver com a DM fechada)."""
        paineis = await asyncio.to_thread(db.paineis_do_fogo, fogo["id"])
        for p in paineis:
            try:
                await self._mensagem(p).edit(
                    view=visual.cartao_painel_encerrado(fogo)
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        # Painel de antes da Etapa 4.1: era uma mensagem só, no canal.
        if not paineis and fogo["painel_id"]:
            canal = await self._canal(fogo["canal_id"])
            if canal is not None:
                try:
                    await canal.get_partial_message(fogo["painel_id"]).edit(
                        view=visual.cartao_painel_encerrado(fogo)
                    )
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        sem_dm: list[int] = []
        for user_id in (fogo["usuario_a"], fogo["usuario_b"]):
            try:
                usuario = await self._usuario(user_id)
                await usuario.send(
                    view=visual.cartao_fogo_apagado(
                        fogo, rodape=_rodape(self.bot, fogo, "dm")
                    )
                )
            except (discord.Forbidden, discord.HTTPException):
                sem_dm.append(user_id)

        if sem_dm:
            canal = await self._canal(fogo["canal_id"])
            if canal is not None:
                try:
                    await canal.send(
                        view=visual.cartao_fogo_apagado(fogo),
                        allowed_mentions=discord.AllowedMentions(
                            users=[discord.Object(u) for u in sem_dm]
                        ),
                    )
                except (discord.Forbidden, discord.HTTPException) as erro:
                    print(f"Fogo {fogo['id']}: não consegui avisar o apagão: {erro!r}")

        await asyncio.to_thread(db.limpar_paineis, fogo["id"])

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
        # Os botões dos painéis são dinâmicos (AcenderBotao): não precisam
        # ser reativados mensagem por mensagem depois de um reinício.
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
        name="nomefogo",
        description="Sugere um nome para o seu Fogo (liberado com 10 dias).",
        extras={
            "categoria": "Fogo",
            "uso": ",nomefogo [@parceiro]",
            "descricao": (
                "Com 10 dias de Fogo vocês podem dar um nome à sequência. "
                "Um sugere e o outro aceita."
            ),
        },
    )
    @app_commands.describe(
        parceiro="Com quem é o Fogo (só precisa se você tiver vários)"
    )
    @commands.guild_only()
    async def nomefogo(
        self,
        ctx: commands.Context,
        parceiro: Optional[discord.Member] = None,
    ) -> None:
        lista = await asyncio.to_thread(
            db.fogos_do_membro, ctx.guild.id, ctx.author.id
        )
        if parceiro is not None:
            lista = [
                f
                for f in lista
                if parceiro.id in (f["usuario_a"], f["usuario_b"])
            ]

        if not lista:
            await self._aviso(
                ctx,
                "Nenhum Fogo encontrado",
                "Você não tem um Fogo ativo"
                + (f" com {parceiro.mention}" if parceiro else "")
                + ". Chame alguém com `,fogo @membro`!",
            )
            return

        if len(lista) > 1:
            await self._aviso(
                ctx,
                "Você tem vários Fogos",
                "Diga com quem é o Fogo: `,nomefogo @membro`.",
            )
            return

        fogo = lista[0]
        if fogo["sequencia"] < nomes.NOME_MIN_DIAS:
            faltam = nomes.NOME_MIN_DIAS - fogo["sequencia"]
            await self._aviso(
                ctx,
                "Ainda não liberou",
                f"O nome do Fogo libera com {nomes.NOME_MIN_DIAS} dias de "
                f"sequência. Faltam {visual.dias(faltam)}!",
            )
            return

        await ctx.send(
            view=visual.cartao_convite_nome(fogo, [NomeBotao(fogo["id"])]),
            allowed_mentions=discord.AllowedMentions.none(),
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
