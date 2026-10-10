"""Sistema Fogo do Poyo.

Etapa 1: convite, aceitar/recusar e criação do Fogo.
Etapa 2: ciclo diário (00:00), botão "Acender o Fogo", sequência e Fogo apagado.
Etapa 3: nome do Fogo (liberado aos 10 dias, sugerido por um e aceito pelo par).
Etapa 4: Lendários do Fogo (cogs/fogo_lendarios.py); aqui só há o aviso do 500º dia.
Etapa 5: o Poyo (feliz / com frio) aparece como emoji nos títulos dos cartões;
veja cogs/fogo_emojis.py e cogs/_fogo_visual.py.
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
from . import _fogo_hora as tempo  # hoje() com o horário da virada configurável
from . import _fogo_hora as conf  # DM / servidor ligados ou não
from . import _fogo_visual as visual


# ------------------------------------------------------------------ convite


class ConviteView(visual.CartaoFogo):
    """Mensagem de convite com os botões Aceitar / Recusar.

    Usa custom_id fixo por convite, então os botões continuam funcionando
    depois que o bot reinicia (a cog registra de novo os convites pendentes).
    """

    def __init__(
        self, convite_id: int, de_id: int, para_id: int
    ) -> None:
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
        super().__init__(*blocos, botoes=[aceitar, recusar], cor=visual.COR_POYO)

    async def _so_convidado(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.para_id:
            return True
        await interaction.response.send_message(
            f"Só <@{self.para_id}> pode responder.",
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
                "Convite já respondido.", ephemeral=True
            )
            return

        await interaction.response.edit_message(**visual.edicao(novo))
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
                "Convite já respondido.", ephemeral=True
            )
            return

        await interaction.response.edit_message(**visual.edicao(novo))
        self.stop()


# ------------------------------------------------------------------- painel

AVISO_DM_FECHADA = (
    "-# 💌 Sua DM está fechada. Abra as DMs com o Poyo para receber os avisos lá."
)


def _rodape(client: discord.Client, fogo: sqlite3.Row, modo: str) -> Optional[str]:
    """Linha pequena no fim do painel.
    `modo`: 'dm', 'canal' (DM fechada), 'servidor' (painel compartilhado) ou 'legado'.
    Só o modo 'canal' tem rodapé (aviso de DM fechada); a DM não mostra mais o servidor."""
    if modo == "canal":
        return AVISO_DM_FECHADA
    return None


def _botoes_nome(fogo: sqlite3.Row) -> list[discord.ui.Item]:
    if fogo["sequencia"] >= nomes.NOME_MIN_DIAS and not fogo["nome"]:
        return [NomeBotao(fogo["id"])]
    return []


def _view_do_dia(
    client: discord.Client, fogo: sqlite3.Row, modo: str, enfeite: bool = True
) -> discord.ui.LayoutView:
    """Painel (falta alguém acender) ou card de Fogo aceso (os dois já acenderam).
    `enfeite`: mostra o GIF (só vale se a mensagem tiver o anexo)."""
    if db.dia_completo(fogo):
        return visual.cartao_fogo_aceso(
            fogo,
            botoes=_botoes_nome(fogo),
            rodape=_rodape(client, fogo, modo) if modo == "dm" else None,
            midia=visual.ENFEITE_NOME if enfeite and visual.enfeite_existe() else None,
        )
    return PainelView(fogo, _rodape(client, fogo, modo), enfeite)


def _envio(client: discord.Client, fogo: sqlite3.Row, modo: str) -> dict:
    """Argumentos de um envio novo: a view e, se for o painel, o GIF."""
    view = _view_do_dia(client, fogo, modo)
    return visual.envio(view)


def _envio_apagado(fogo: sqlite3.Row) -> dict:
    """Aviso de apagão com animação azul e Poyo com frio."""
    return visual.envio(visual.cartao_fogo_apagado(fogo))



def _paineis_unicos(paineis) -> list[tuple[sqlite3.Row, str]]:
    """(painel, modo) sem repetir mensagem. O painel do servidor (usuario_id 0)
    vem primeiro; quem está com a DM fechada aponta para essa mesma mensagem."""
    vistos: set = set()
    saida: list[tuple[sqlite3.Row, str]] = []
    for p in sorted(paineis, key=lambda x: x["usuario_id"]):
        chave = (p["canal_id"], p["mensagem_id"])
        if chave in vistos:
            continue
        vistos.add(chave)
        if p["usuario_id"] == 0:
            modo = "servidor"
        else:
            modo = "dm" if p["dm"] else "canal"
        saida.append((p, modo))
    return saida


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
                style=discord.ButtonStyle.secondary,
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

    def __init__(
        self,
        fogo: sqlite3.Row,
        rodape: Optional[str] = None,
        enfeite: bool = True,
    ) -> None:
        blocos = list(visual.blocos_painel(fogo))
        if rodape:
            blocos[-1] += "\n" + rodape
        usar_gif = enfeite and visual.enfeite_existe()
        super().__init__(
            *blocos,
            botoes=[AcenderBotao(fogo["id"]), *_botoes_nome(fogo)],
            midia=visual.ENFEITE_NOME if usar_gif else None,
        )
        self.enfeite = usar_gif


async def acender(interaction: discord.Interaction, fogo_id: int) -> None:
    resultado, fogo = await asyncio.to_thread(
        db.acender, fogo_id, interaction.user.id, tempo.hoje()
    )

    if resultado in ("ok", "completo"):
        # Descobre onde este painel mora (DM, canal de reserva ou painel
        # antigo de antes da Etapa 4.1) para escolher o rodapé certo.
        modo = "legado"
        mensagem_id = interaction.message.id if interaction.message else None
        paineis = await asyncio.to_thread(db.paineis_do_fogo, fogo_id)
        for p, modo_p in _paineis_unicos(paineis):
            if p["mensagem_id"] == mensagem_id:
                modo = modo_p
                break

        # Painéis antigos (sem o GIF anexado) são editados sem o GIF.
        msg = interaction.message
        enfeite = bool(
            msg and any(a.filename == visual.ENFEITE_NOME for a in msg.attachments)
        )
        await interaction.response.edit_message(
            **visual.edicao(_view_do_dia(interaction.client, fogo, modo, enfeite=enfeite))
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
            "nao_participa": "Esse Fogo não é seu.",
            "ja_acendeu": "Você já acendeu. Falta o outro.",
            "dia_encerrado": "Esse painel é de outro dia.",
            "inativo": "Esse Fogo já apagou.",
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
                "inativo": "Esse Fogo já apagou.",
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
                **visual.envio(cartao), allowed_mentions=mencoes
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
            await canal.send(**visual.envio(cartao), allowed_mentions=mencoes)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "Não consegui postar no canal. Use `,nomefogo` no servidor.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Sugestão enviada em {canal.mention}. "
            f"Aguarde <@{parceiro}> aceitar. 🔥",
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
            aviso = "Esse Fogo já apagou."
        elif interaction.user.id not in (fogo["usuario_a"], fogo["usuario_b"]):
            aviso = "Só a dupla pode escolher o nome."
        elif fogo["sequencia"] < nomes.NOME_MIN_DIAS:
            faltam = nomes.NOME_MIN_DIAS - fogo["sequencia"]
            aviso = (
                f"O nome libera com {nomes.NOME_MIN_DIAS} dias. "
                f"Faltam {visual.dias(faltam)}."
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
                **visual.edicao(visual.cartao_fogo_batizado(fogo))
            )
            cog = interaction.client.get_cog("Fogo")
            if cog is not None:  # o nome novo aparece nos painéis de hoje
                try:
                    await cog.atualizar_paineis(fogo)
                except Exception as erro:
                    print(f"Fogo {fogo['id']}: erro ao atualizar painéis: {erro!r}")
        elif status == "recusado":
            await interaction.response.edit_message(
                **visual.edicao(visual.cartao_nome_recusado(
                    fogo, nome_sugerido or "sugerido", interaction.user.id
                ))
            )
        elif status == "expirada":
            await interaction.response.edit_message(
                **visual.edicao(visual.cartao_proposta_encerrada(
                    "Sugestão expirada (24 horas)."
                ))
            )
        elif status == "antiga":
            await interaction.response.edit_message(
                **visual.edicao(visual.cartao_proposta_encerrada(
                    "Sugestão já respondida ou substituída."
                ))
            )
        elif status == "inativo":
            await interaction.response.edit_message(
                **visual.edicao(visual.cartao_proposta_encerrada(
                    "O Fogo apagou antes da resposta."
                ))
            )
        else:
            avisos = {
                "proprio": "Quem sugeriu não pode aceitar. Aguarde seu par.",
                "nao_participa": "Só a dupla pode responder.",
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
        await asyncio.to_thread(tempo.carregar)
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
            await ctx.send(**visual.envio(view), ephemeral=True)
        else:
            await ctx.send(**visual.envio(view), delete_after=15)

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
                **visual.edicao(visual.cartao_convite_expirado(
                    f"<@{convite['de_id']}>", f"<@{convite['para_id']}>"
                ))
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

    async def _enviar_painel_servidor(
        self, fogo: sqlite3.Row
    ) -> Optional[tuple[int, int]]:
        """Painel compartilhado no canal do Fogo (opção "servidor" ligada).
        Devolve (canal_id, mensagem_id) ou None se falhou."""
        canal = await self._canal(fogo["canal_id"])
        if canal is None:
            return None
        # Com a DM ligada o painel do servidor não marca ninguém (já há aviso na DM).
        marcar = (
            []
            if conf.dm_ativo()
            else [discord.Object(fogo["usuario_a"]), discord.Object(fogo["usuario_b"])]
        )
        try:
            mensagem = await canal.send(
                **_envio(self.bot, fogo, "servidor"),
                allowed_mentions=discord.AllowedMentions(users=marcar),
            )
        except (discord.Forbidden, discord.HTTPException) as erro:
            print(f"Fogo {fogo['id']}: não consegui enviar o painel no servidor: {erro!r}")
            return None
        await asyncio.to_thread(
            db.registrar_painel,
            fogo["id"],
            0,
            mensagem.channel.id,
            mensagem.id,
            False,
        )
        return mensagem.channel.id, mensagem.id

    async def _enviar_painel(
        self,
        fogo: sqlite3.Row,
        user_id: int,
        compartilhado: Optional[tuple[int, int]] = None,
    ) -> bool:
        """Entrega o painel de UMA pessoa: na DM, ou no canal se a DM estiver
        fechada. Devolve True se a mensagem foi entregue e registrada."""
        mensagem = None
        dm = True
        try:
            usuario = await self._usuario(user_id)
            mensagem = await usuario.send(**_envio(self.bot, fogo, "dm"))
        except (discord.Forbidden, discord.NotFound):
            dm = False  # DM fechada (ou conta sumiu): usa o canal
        except discord.HTTPException as erro:
            print(f"Fogo {fogo['id']}: erro ao enviar DM para {user_id}: {erro!r}")
            return False  # falha passageira: o ciclo tenta de novo

        if mensagem is None:
            if compartilhado is not None:
                # O painel do servidor já avisa essa pessoa: só registra.
                await asyncio.to_thread(
                    db.registrar_painel,
                    fogo["id"],
                    user_id,
                    compartilhado[0],
                    compartilhado[1],
                    False,
                )
                return True
            canal = await self._canal(fogo["canal_id"])
            if canal is None:
                return False
            try:
                mensagem = await canal.send(
                    **_envio(self.bot, fogo, "canal"),
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
        """Manda o painel do dia: na DM de cada pessoa e/ou no canal do Fogo,
        conforme o painel de config. Quem já recebeu não recebe de novo."""
        async with self._trava(fogo_id):
            fogo = await asyncio.to_thread(db.obter_fogo, fogo_id)
            if fogo is None or not fogo["ativo"] or fogo["painel_id"]:
                return

            usar_dm, usar_servidor = conf.dm_ativo(), conf.servidor_ativo()
            existentes = {
                p["usuario_id"]: p
                for p in await asyncio.to_thread(db.paineis_do_fogo, fogo_id)
            }

            compartilhado: Optional[tuple[int, int]] = None
            if 0 in existentes:
                compartilhado = (
                    existentes[0]["canal_id"],
                    existentes[0]["mensagem_id"],
                )
            elif usar_servidor:
                compartilhado = await self._enviar_painel_servidor(fogo)

            if usar_dm:
                for user_id in (fogo["usuario_a"], fogo["usuario_b"]):
                    if user_id in existentes:
                        continue
                    # Relê a cada envio: o primeiro pode ter acendido nesse meio tempo.
                    atual = await asyncio.to_thread(db.obter_fogo, fogo_id)
                    if atual is None or not atual["ativo"]:
                        return
                    await self._enviar_painel(atual, user_id, compartilhado)

            paineis = await asyncio.to_thread(db.paineis_do_fogo, fogo_id)
            ids = {p["usuario_id"] for p in paineis}
            esperados = set()
            if usar_dm:
                esperados |= {fogo["usuario_a"], fogo["usuario_b"]}
            if usar_servidor:
                esperados.add(0)
            if paineis and esperados <= ids:
                # Marca o dia como "painéis entregues" (senão o ciclo repete).
                await asyncio.to_thread(
                    db.definir_painel, fogo_id, paineis[0]["mensagem_id"]
                )

    async def _editar_painel(
        self, p: sqlite3.Row, fogo: sqlite3.Row, modo: str
    ) -> None:
        """Edita um painel. Se a mensagem for antiga (sem o GIF anexado),
        tenta de novo sem o GIF."""
        for enfeite in (True, False):
            try:
                await self._mensagem(p).edit(
                    **visual.edicao(_view_do_dia(self.bot, fogo, modo, enfeite=enfeite))
                )
                return
            except (discord.NotFound, discord.Forbidden):
                return
            except discord.HTTPException:
                continue

    async def atualizar_paineis(
        self, fogo: sqlite3.Row, excluir: Optional[int] = None
    ) -> None:
        """Deixa o painel de cada um igual ao estado atual (quem acendeu, nome).
        `excluir` é a mensagem que acabou de ser editada pelo próprio clique."""
        paineis = await asyncio.to_thread(db.paineis_do_fogo, fogo["id"])
        for p, modo in _paineis_unicos(paineis):
            if p["mensagem_id"] == excluir:
                continue
            await self._editar_painel(p, fogo, modo)

    async def _apagar(self, fogo: sqlite3.Row) -> None:
        """Fogo apagou: encerra os painéis antigos e avisa na DM de cada um
        (se ligada) e/ou no canal (se ligado, ou para quem estiver com a DM
        fechada)."""
        paineis = await asyncio.to_thread(db.paineis_do_fogo, fogo["id"])
        for p, _modo in _paineis_unicos(paineis):
            try:
                await self._mensagem(p).edit(
                    **visual.edicao(visual.cartao_painel_encerrado(fogo))
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        # Painel de antes da Etapa 4.1: era uma mensagem só, no canal.
        if not paineis and fogo["painel_id"]:
            canal = await self._canal(fogo["canal_id"])
            if canal is not None:
                try:
                    await canal.get_partial_message(fogo["painel_id"]).edit(
                        **visual.edicao(visual.cartao_painel_encerrado(fogo))
                    )
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        usar_dm, usar_servidor = conf.dm_ativo(), conf.servidor_ativo()
        sem_dm: list[int] = []
        if usar_dm:
            for user_id in (fogo["usuario_a"], fogo["usuario_b"]):
                try:
                    usuario = await self._usuario(user_id)
                    await usuario.send(**_envio_apagado(fogo))
                except (discord.Forbidden, discord.HTTPException):
                    sem_dm.append(user_id)

        if usar_servidor or sem_dm:
            canal = await self._canal(fogo["canal_id"])
            if canal is not None:
                marcar = (
                    [fogo["usuario_a"], fogo["usuario_b"]]
                    if usar_servidor and not usar_dm
                    else sem_dm
                )
                try:
                    await canal.send(
                        **_envio_apagado(fogo),
                        allowed_mentions=discord.AllowedMentions(
                            users=[discord.Object(u) for u in marcar]
                        ),
                    )
                except (discord.Forbidden, discord.HTTPException) as erro:
                    print(f"Fogo {fogo['id']}: não consegui avisar o apagão: {erro!r}")

        await asyncio.to_thread(db.limpar_paineis, fogo["id"])

    async def encerrar_removido(self, fogo: sqlite3.Row) -> None:
        """Fogo removido por um administrador: encerra os painéis, sem aviso
        de "apagou"."""
        paineis = await asyncio.to_thread(db.paineis_do_fogo, fogo["id"])
        for p, _modo in _paineis_unicos(paineis):
            try:
                await self._mensagem(p).edit(
                    **visual.edicao(visual.cartao_fogo_removido(fogo))
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        if not paineis and fogo["painel_id"]:
            canal = await self._canal(fogo["canal_id"])
            if canal is not None:
                try:
                    await canal.get_partial_message(fogo["painel_id"]).edit(
                        **visual.edicao(visual.cartao_fogo_removido(fogo))
                    )
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
        await asyncio.to_thread(db.limpar_paineis, fogo["id"])

    @tasks.loop(seconds=20)
    async def ciclo_diario(self) -> None:
        """Vira o dia na hora configurada (padrão 00:00, Brasília), sem depender de o bot
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
                "Convida alguém para um Fogo: uma sequência diária "
                "que os dois acendem juntos."
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
                "Chame outra pessoa.",
            )
            return

        if membro.bot:
            await self._aviso(
                ctx,
                "Bots não acendem Fogo",
                "Chame uma pessoa.",
            )
            return

        ativo = await asyncio.to_thread(
            db.fogo_ativo_entre, ctx.guild.id, autor.id, membro.id
        )
        if ativo is not None:
            await self._aviso(
                ctx,
                "Vocês já têm um Fogo",
                f"{autor.mention} + {membro.mention} já têm um Fogo "
                "ativo. Veja com `,fogos`.",
            )
            return

        pendente = await asyncio.to_thread(
            db.convite_pendente_entre, ctx.guild.id, autor.id, membro.id
        )
        if pendente is not None:
            await self._aviso(
                ctx,
                "Já existe um convite",
                f"Já existe um convite entre "
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
                **visual.envio(ConviteView(convite_id, autor.id, membro.id)),
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
                "Com 10 dias, deem um nome ao Fogo. "
                "Um sugere, o outro aceita."
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
                + ". Use `,fogo @membro`.",
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
                f"O nome libera com {nomes.NOME_MIN_DIAS} dias. "
                f"Faltam {visual.dias(faltam)}.",
            )
            return

        await ctx.send(
            **visual.envio(visual.cartao_convite_nome(fogo, [NomeBotao(fogo["id"])])),
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
                f"{quem} nenhum Fogo ativo. Use `,fogo @membro`.",
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
            **visual.envio(visual.CartaoFogo(
                f"## {visual.EMOJI_FOGO_FELIZ} Fogos de {alvo.display_name}",
                "\n\n".join(linhas),
                cor=visual.COR_FOGO,
            )),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fogo(bot))
