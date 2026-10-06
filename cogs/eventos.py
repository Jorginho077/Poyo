from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord.ext import commands


EMOJI_INICIO = "<:axolote:1556443018557661234>"
EMOJI_FINAL = "<a:emoji_481:1556442987691647068>"

BANNER_URL = (
    "https://cdn.discordapp.com/attachments/"
    "1556052693511053397/1556449326891401307/"
    "GIF_image_3.gif?backend=b2&ex=6ac433e4&"
    "is=6ac2e264&hm=7bd09ac806e541b3edadc60c2c796f17"
    "dfcffe12b617209c6a735eb7e741ce9&"
)

EVENTO_IMAGEM_URL = (
    "https://cdn.discordapp.com/attachments/"
    "1556052693511053397/1557047859189260308/"
    "Lucy_Axolotl_JE2.webp?backend=b2&"
    "ex=6ac66152&is=6ac50fd2&"
    "hm=f2032f422b331d2215ce7b142c15fe6d"
    "6560a8bc94e42f0d66389d75421c268a&"
)


@dataclass
class ConfiguracaoEvento:
    autor_id: int
    guild_id: int
    cargo: Optional[discord.Role] = None
    canal: Optional[discord.TextChannel] = None
    ganhadores: Optional[int] = None
    mensagem: Optional[str] = None
    painel: Optional[discord.Message] = None


@dataclass
class EstadoEvento:
    guild_id: int
    cargo: discord.Role
    limite: int
    ganhadores: set[int] = field(default_factory=set)
    trava: asyncio.Lock = field(
        default_factory=asyncio.Lock
    )
    mensagem: Optional[discord.Message] = None


class CartaoBase(discord.ui.LayoutView):
    def __init__(
        self,
        titulo: str,
        descricao: str,
    ) -> None:
        super().__init__(timeout=None)

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(BANNER_URL)
                ),
                discord.ui.TextDisplay(
                    (
                        f"{EMOJI_INICIO}  "
                        f"**{titulo}**  "
                        f"{EMOJI_FINAL}\n\n"
                        f"{descricao}"
                    )
                ),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(BANNER_URL)
                ),
            )
        )


class MensagemEventoModal(
    discord.ui.Modal,
    title="Mensagem do evento",
):
    mensagem = discord.ui.TextInput(
        label="Mensagem enviada junto do evento",
        placeholder="Digite a mensagem do evento.",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )

    def __init__(
        self,
        menu: "MenuEvento",
    ) -> None:
        super().__init__(timeout=120)
        self.menu = menu

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        self.menu.config.mensagem = (
            str(self.mensagem.value).strip()
        )

        await interaction.response.send_message(
            "Mensagem definida com sucesso.",
            ephemeral=True,
        )

        await self.menu.atualizar_painel()


class NumeroGanhadoresModal(
    discord.ui.Modal,
    title="Número de ganhadores",
):
    numero = discord.ui.TextInput(
        label="Quantas pessoas poderão ganhar?",
        placeholder="Exemplo: 3",
        min_length=1,
        max_length=3,
        required=True,
    )

    def __init__(
        self,
        menu: "MenuEvento",
    ) -> None:
        super().__init__(timeout=120)
        self.menu = menu

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        try:
            quantidade = int(
                str(self.numero.value).strip()
            )
        except ValueError:
            await interaction.response.send_message(
                "Digite apenas um número inteiro.",
                ephemeral=True,
            )
            return

        if not 1 <= quantidade <= 100:
            await interaction.response.send_message(
                "Escolha um número entre 1 e 100.",
                ephemeral=True,
            )
            return

        self.menu.config.ganhadores = quantidade

        await interaction.response.send_message(
            f"Número definido: **{quantidade}**.",
            ephemeral=True,
        )

        await self.menu.atualizar_painel()


class ConfiguracaoView(discord.ui.LayoutView):
    def __init__(
        self,
        config: ConfiguracaoEvento,
    ) -> None:
        super().__init__(timeout=None)
        self.config = config

        botao = discord.ui.Button(
            label="RedButton",
            style=discord.ButtonStyle.danger,
            custom_id="evento:abrir_configuracao",
        )

        botao.callback = self.abrir_configuracao

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(BANNER_URL)
                ),
                discord.ui.TextDisplay(
                    (
                        f"{EMOJI_INICIO}  "
                        "**Configuração do evento**  "
                        f"{EMOJI_FINAL}\n\n"
                        "Clique em **RedButton** "
                        "para abrir as opções."
                    )
                ),
                discord.ui.ActionRow(
                    botao,
                ),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(BANNER_URL)
                ),
            )
        )

    async def abrir_configuracao(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.user.id != self.config.autor_id:
            await interaction.response.send_message(
                "Somente quem abriu este painel pode configurá-lo.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            view=MenuEvento(self.config)
        )


class MenuEvento(discord.ui.LayoutView):
    def __init__(
        self,
        config: ConfiguracaoEvento,
    ) -> None:
        super().__init__(timeout=None)
        self.config = config

        cargo = (
            self.config.cargo.mention
            if self.config.cargo
            else "não definido"
        )

        canal = (
            self.config.canal.mention
            if self.config.canal
            else "não definido"
        )

        ganhadores = (
            str(self.config.ganhadores)
            if self.config.ganhadores is not None
            else "não definido"
        )

        mensagem = (
            "definida"
            if self.config.mensagem
            else "não definida"
        )

        botoes = {
            "voltar": discord.ui.Button(
                label="Voltar",
                style=discord.ButtonStyle.secondary,
                custom_id="evento:voltar",
            ),
            "cargo": discord.ui.Button(
                label="Cargo Sorteado",
                style=discord.ButtonStyle.primary,
                custom_id="evento:cargo",
            ),
            "canal": discord.ui.Button(
                label="Selecionar canal",
                style=discord.ButtonStyle.primary,
                custom_id="evento:canal",
            ),
            "numero": discord.ui.Button(
                label="Número de Ganhadores",
                style=discord.ButtonStyle.primary,
                custom_id="evento:numero",
            ),
            "mensagem": discord.ui.Button(
                label="Mensagem do evento",
                style=discord.ButtonStyle.primary,
                custom_id="evento:mensagem",
            ),
            "enviar": discord.ui.Button(
                label="Enviar evento",
                style=discord.ButtonStyle.success,
                custom_id="evento:enviar",
            ),
        }

        botoes["voltar"].callback = self.voltar
        botoes["cargo"].callback = self.selecionar_cargo
        botoes["canal"].callback = self.selecionar_canal
        botoes["numero"].callback = self.numero_ganhadores
        botoes["mensagem"].callback = self.mensagem_evento
        botoes["enviar"].callback = self.enviar_evento

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(BANNER_URL)
                ),
                discord.ui.TextDisplay(
                    (
                        f"{EMOJI_INICIO}  "
                        "**Configurar evento**  "
                        f"{EMOJI_FINAL}\n\n"
                        f"Cargo: {cargo}\n"
                        f"Canal: {canal}\n"
                        f"Ganhadores: {ganhadores}\n"
                        f"Mensagem: {mensagem}"
                    )
                ),
                discord.ui.ActionRow(
                    botoes["voltar"],
                    botoes["cargo"],
                    botoes["canal"],
                    botoes["numero"],
                    botoes["mensagem"],
                ),
                discord.ui.ActionRow(
                    botoes["enviar"],
                ),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(BANNER_URL)
                ),
            )
        )

    async def autorizado(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id == self.config.autor_id:
            return True

        await interaction.response.send_message(
            "Somente quem abriu este painel pode configurá-lo.",
            ephemeral=True,
        )

        return False

    async def atualizar_painel(self) -> None:
        if self.config.painel is None:
            return

        try:
            await self.config.painel.edit(
                view=MenuEvento(self.config)
            )
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    async def esperar_mensagem(
        self,
        interaction: discord.Interaction,
        texto: str,
    ) -> Optional[discord.Message]:
        await interaction.response.send_message(
            texto,
            ephemeral=True,
        )

        canal = interaction.channel

        if canal is None:
            return None

        def verificar(
            mensagem: discord.Message,
        ) -> bool:
            return (
                mensagem.author.id
                == self.config.autor_id
                and mensagem.channel.id == canal.id
                and mensagem.guild is not None
                and mensagem.guild.id
                == self.config.guild_id
            )

        try:
            mensagem = await interaction.client.wait_for(
                "message",
                timeout=120,
                check=verificar,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Tempo esgotado. Tente novamente.",
                ephemeral=True,
            )
            return None

        try:
            await mensagem.delete()
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

        return mensagem

    async def voltar(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self.autorizado(interaction):
            return

        await interaction.response.edit_message(
            view=ConfiguracaoView(self.config)
        )

    async def selecionar_cargo(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self.autorizado(interaction):
            return

        mensagem = await self.esperar_mensagem(
            interaction,
            "Mencione o cargo que será entregue.",
        )

        if mensagem is None:
            return

        if not mensagem.role_mentions:
            await interaction.followup.send(
                "Não encontrei uma menção de cargo.",
                ephemeral=True,
            )
            return

        cargo = mensagem.role_mentions[0]
        bot_membro = mensagem.guild.me

        if cargo.is_default() or cargo.managed:
            await interaction.followup.send(
                "Esse cargo não pode ser entregue.",
                ephemeral=True,
            )
            return

        if (
            bot_membro is None
            or cargo >= bot_membro.top_role
        ):
            await interaction.followup.send(
                "Meu cargo precisa estar acima do cargo sorteado.",
                ephemeral=True,
            )
            return

        self.config.cargo = cargo

        await interaction.followup.send(
            f"Cargo definido: {cargo.mention}",
            ephemeral=True,
        )

        await self.atualizar_painel()

    async def selecionar_canal(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self.autorizado(interaction):
            return

        mensagem = await self.esperar_mensagem(
            interaction,
            "Mencione o canal do evento.",
        )

        if mensagem is None:
            return

        if not mensagem.channel_mentions:
            await interaction.followup.send(
                "Não encontrei uma menção de canal.",
                ephemeral=True,
            )
            return

        canal = mensagem.channel_mentions[0]

        if not isinstance(
            canal,
            discord.TextChannel,
        ):
            await interaction.followup.send(
                "Selecione um canal de texto.",
                ephemeral=True,
            )
            return

        permissao = canal.permissions_for(
            mensagem.guild.me
        )

        if (
            not permissao.view_channel
            or not permissao.send_messages
        ):
            await interaction.followup.send(
                "Não consigo enviar mensagens nesse canal.",
                ephemeral=True,
            )
            return

        self.config.canal = canal

        await interaction.followup.send(
            f"Canal definido: {canal.mention}",
            ephemeral=True,
        )

        await self.atualizar_painel()

    async def numero_ganhadores(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self.autorizado(interaction):
            return

        await interaction.response.send_modal(
            NumeroGanhadoresModal(self)
        )

    async def mensagem_evento(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self.autorizado(interaction):
            return

        await interaction.response.send_modal(
            MensagemEventoModal(self)
        )

    async def enviar_evento(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self.autorizado(interaction):
            return

        if self.config.cargo is None:
            await interaction.response.send_message(
                "Defina o cargo sorteado.",
                ephemeral=True,
            )
            return

        if self.config.canal is None:
            await interaction.response.send_message(
                "Selecione o canal.",
                ephemeral=True,
            )
            return

        if self.config.ganhadores is None:
            await interaction.response.send_message(
                "Defina o número de ganhadores.",
                ephemeral=True,
            )
            return

        permissao = self.config.canal.permissions_for(
            self.config.canal.guild.me
        )

        if (
            not permissao.view_channel
            or not permissao.send_messages
        ):
            await interaction.response.send_message(
                "Não consigo enviar mensagens nesse canal.",
                ephemeral=True,
            )
            return

        estado = EstadoEvento(
            guild_id=self.config.guild_id,
            cargo=self.config.cargo,
            limite=self.config.ganhadores,
        )

        view = EventoView(estado)
        mensagem_texto = None

        try:
            if self.config.mensagem:
                mensagem_texto = (
                    await self.config.canal.send(
                        content=self.config.mensagem,
                        allowed_mentions=(
                            discord.AllowedMentions.none()
                        ),
                    )
                )

            mensagem_evento = (
                await self.config.canal.send(
                    view=view
                )
            )

        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            if mensagem_texto is not None:
                try:
                    await mensagem_texto.delete()
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    pass

            await interaction.response.send_message(
                "Não consegui enviar o evento.",
                ephemeral=True,
            )
            return

        estado.mensagem = mensagem_evento
        view.mensagem = mensagem_evento

        await interaction.response.edit_message(
            view=CartaoBase(
                "Evento enviado",
                "O evento foi enviado no canal escolhido.",
            )
        )


class EventoView(discord.ui.LayoutView):
    def __init__(
        self,
        estado: EstadoEvento,
    ) -> None:
        super().__init__(timeout=None)

        self.estado = estado
        self.mensagem: Optional[discord.Message] = None

        botao = discord.ui.Button(
            label="Participar",
            style=discord.ButtonStyle.danger,
            custom_id=f"evento:participar:{id(estado)}",
        )

        botao.callback = self.participar

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(
                        EVENTO_IMAGEM_URL
                    )
                ),
                discord.ui.ActionRow(
                    botao,
                ),
            )
        )

    async def participar(
        self,
        interaction: discord.Interaction,
    ) -> None:
        estado = self.estado

        async with estado.trava:
            if interaction.user.id in estado.ganhadores:
                await interaction.response.send_message(
                    "Você já ganhou este evento.",
                    ephemeral=True,
                )
                return

            if len(estado.ganhadores) >= estado.limite:
                await interaction.response.send_message(
                    "O limite de ganhadores já foi atingido.",
                    ephemeral=True,
                )
                return

            if interaction.guild is None:
                await interaction.response.send_message(
                    "Este evento só funciona no servidor.",
                    ephemeral=True,
                )
                return

            membro = interaction.guild.get_member(
                interaction.user.id
            )

            if membro is None:
                await interaction.response.send_message(
                    "Não encontrei você no servidor.",
                    ephemeral=True,
                )
                return

            try:
                await membro.add_roles(
                    estado.cargo,
                    reason="Ganhador de evento.",
                )
            except (
                discord.Forbidden,
                discord.HTTPException,
            ):
                await interaction.response.send_message(
                    "Não consegui entregar o cargo.",
                    ephemeral=True,
                )
                return

            estado.ganhadores.add(
                interaction.user.id
            )

            await interaction.response.send_message(
                (
                    "Você ganhou e recebeu "
                    f"{estado.cargo.mention}!"
                ),
                ephemeral=True,
            )

            if (
                len(estado.ganhadores)
                >= estado.limite
                and estado.mensagem is not None
            ):
                try:
                    await estado.mensagem.delete()
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    pass


class Eventos(commands.Cog):
    @commands.command(
        name="poyoevent",
        extras={
            "categoria": "Eventos",
            "uso": ",poyoevent",
            "descricao": (
                "Abre o painel de configuração de eventos."
            ),
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        administrator=True,
    )
    async def poyoevent(
        self,
        ctx: commands.Context,
    ) -> None:
        config = ConfiguracaoEvento(
            autor_id=ctx.author.id,
            guild_id=ctx.guild.id,
        )

        try:
            await ctx.message.delete()
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

        mensagem = await ctx.send(
            view=ConfiguracaoView(config)
        )

        config.painel = mensagem


async def setup(
    bot: commands.Bot,
) -> None:
    await bot.add_cog(
        Eventos(bot)
    )
