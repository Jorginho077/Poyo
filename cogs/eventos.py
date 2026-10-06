from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord.ext import commands


EMOJI_INICIO = "<:axolote:1556443018557661234>"
EMOJI_FINAL = "<a:emoji_481:1556442987691647068>"

EVENTOS_BANNER_URL = (
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
class Configuracao:
    autor_id: int
    guild_id: int
    cargo: Optional[discord.Role] = None
    canal: Optional[discord.TextChannel] = None
    limite: Optional[int] = None
    texto: Optional[str] = None
    painel: Optional[discord.Message] = None


@dataclass
class Evento:
    cargo: discord.Role
    limite: int
    ganhadores: set[int] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    mensagem: Optional[discord.Message] = None


class EventoCartao(discord.ui.LayoutView):
    def __init__(self, titulo: str, texto: str) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(EVENTOS_BANNER_URL)
                ),
                discord.ui.TextDisplay(
                    f"{EMOJI_INICIO}  **{titulo}**  {EMOJI_FINAL}\n\n{texto}"
                ),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(EVENTOS_BANNER_URL)
                ),
            )
        )


class TextoModal(discord.ui.Modal, title="Mensagem do evento"):
    texto = discord.ui.TextInput(
        label="Mensagem enviada junto do evento",
        placeholder="Digite a mensagem do evento.",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )

    def __init__(self, menu: "Menu") -> None:
        super().__init__(timeout=120)
        self.menu = menu

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.menu.config.texto = str(self.texto.value).strip()
        await interaction.response.send_message(
            "Mensagem definida com sucesso.",
            ephemeral=True,
        )
        await self.menu.atualizar()


class LimiteModal(discord.ui.Modal, title="Número de ganhadores"):
    numero = discord.ui.TextInput(
        label="Quantas pessoas poderão ganhar?",
        placeholder="Ex.: 3",
        min_length=1,
        max_length=3,
        required=True,
    )

    def __init__(self, menu: "Menu") -> None:
        super().__init__(timeout=120)
        self.menu = menu

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            limite = int(str(self.numero.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "Digite apenas um número inteiro.",
                ephemeral=True,
            )
            return

        if not 1 <= limite <= 100:
            await interaction.response.send_message(
                "Escolha um número entre 1 e 100.",
                ephemeral=True,
            )
            return

        self.menu.config.limite = limite
        await interaction.response.send_message(
            f"Limite definido: **{limite}** ganhador(es).",
            ephemeral=True,
        )
        await self.menu.atualizar()


class Inicio(discord.ui.LayoutView):
    def __init__(self, config: Configuracao) -> None:
        super().__init__(timeout=None)
        self.config = config
        botao = discord.ui.Button(
            label="RedButton",
            style=discord.ButtonStyle.danger,
            custom_id="evento:redbutton",
        )
        botao.callback = self.abrir
        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(EVENTOS_BANNER_URL)
                ),
                discord.ui.TextDisplay(
                    f"{EMOJI_INICIO}  **Configuração do evento**  {EMOJI_FINAL}\n\n"
                    "Clique em **RedButton** para abrir as opções."
                ),
                discord.ui.ActionRow(botao),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(EVENTOS_BANNER_URL)
                ),
            )
        )

    async def abrir(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.config.autor_id:
            await interaction.response.send_message(
                "Somente quem abriu o painel pode configurá-lo.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            view=Menu(self.config)
        )


class Menu(discord.ui.LayoutView):
    def __init__(self, config: Configuracao) -> None:
        super().__init__(timeout=None)
        self.config = config
        cargo = config.cargo.mention if config.cargo else "não definido"
        canal = config.canal.mention if config.canal else "não definido"
        limite = str(config.limite) if config.limite else "não definido"
        texto = "definida" if config.texto else "não definida"

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
            "limite": discord.ui.Button(
                label="Número de Ganhadores",
                style=discord.ButtonStyle.primary,
                custom_id="evento:limite",
            ),
            "texto": discord.ui.Button(
                label="Mensagem do evento",
                style=discord.ButtonStyle.primary,
                custom_id="evento:texto",
            ),
            "enviar": discord.ui.Button(
                label="Enviar evento",
                style=discord.ButtonStyle.success,
                custom_id="evento:enviar",
            ),
        }

        botoes["voltar"].callback = self.voltar
        botoes["cargo"].callback = self.cargo
        botoes["canal"].callback = self.canal
        botoes["limite"].callback = self.limite
        botoes["texto"].callback = self.texto
        botoes["enviar"].callback = self.enviar

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(EVENTOS_BANNER_URL)
                ),
                discord.ui.TextDisplay(
                    f"{EMOJI_INICIO}  **Configurar evento**  {EMOJI_FINAL}\n\n"
                    f"Cargo: {cargo}\n"
                    f"Canal: {canal}\n"
                    f"Ganhadores: {limite}\n"
                    f"Mensagem: {texto}"
                ),
                discord.ui.ActionRow(
                    botoes["voltar"],
                    botoes["cargo"],
                    botoes["canal"],
                    botoes["limite"],
                    botoes["texto"],
                ),
                discord.ui.ActionRow(botoes["enviar"]),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(EVENTOS_BANNER_URL)
                ),
            )
        )

    async def autorizado(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.config.autor_id:
            return True
        await interaction.response.send_message(
            "Somente quem abriu o painel pode configurá-lo.",
            ephemeral=True,
        )
        return False

    async def atualizar(self) -> None:
        if self.config.painel is None:
            return
        try:
            await self.config.painel.edit(view=Menu(self.config))
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    async def esperar_mensagem(
        self,
        interaction: discord.Interaction,
        pedido: str,
    ) -> Optional[discord.Message]:
        await interaction.response.send_message(
            pedido,
            ephemeral=True,
        )
        canal = interaction.channel
        if canal is None:
            return None

        def check(mensagem: discord.Message) -> bool:
            return (
                mensagem.author.id == self.config.autor_id
                and mensagem.channel.id == canal.id
                and mensagem.guild is not None
                and mensagem.guild.id == self.config.guild_id
            )

        try:
            mensagem = await interaction.client.wait_for(
                "message",
                timeout=120,
                check=check,
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

    async def voltar(self, interaction: discord.Interaction) -> None:
        if await self.autorizado(interaction):
            await interaction.response.edit_message(
                view=Inicio(self.config)
            )

    async def cargo(self, interaction: discord.Interaction) -> None:
        if not await self.autorizado(interaction):
            return
        mensagem = await self.esperar_mensagem(
            interaction,
            "Mencione o cargo que será entregue no evento.",
        )
        if mensagem is None or not mensagem.role_mentions:
            if mensagem is not None:
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

        if bot_membro is None or not bot_membro.guild_permissions.manage_roles:
            await interaction.followup.send(
                "O Poyo precisa da permissão `Gerenciar cargos`.",
                ephemeral=True,
            )
            return

        if cargo >= bot_membro.top_role:
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
        await self.atualizar()

    async def canal(self, interaction: discord.Interaction) -> None:
        if not await self.autorizado(interaction):
            return
        mensagem = await self.esperar_mensagem(
            interaction,
            "Mencione o canal em que o evento será enviado.",
        )
        if mensagem is None or not mensagem.channel_mentions:
            if mensagem is not None:
                await interaction.followup.send(
                    "Não encontrei uma menção de canal.",
                    ephemeral=True,
                )
            return

        canal = mensagem.channel_mentions[0]
        if not isinstance(canal, discord.TextChannel):
            await interaction.followup.send(
                "Selecione um canal de texto.",
                ephemeral=True,
            )
            return

        permissao = canal.permissions_for(mensagem.guild.me)
        if not permissao.view_channel or not permissao.send_messages:
            await interaction.followup.send(
                "Não consigo ver ou enviar mensagens nesse canal.",
                ephemeral=True,
            )
            return

        self.config.canal = canal
        await interaction.followup.send(
            f"Canal definido: {canal.mention}",
            ephemeral=True,
        )
        await self.atualizar()

    async def limite(self, interaction: discord.Interaction) -> None:
        if await self.autorizado(interaction):
            await interaction.response.send_modal(
                LimiteModal(self)
            )

    async def texto(self, interaction: discord.Interaction) -> None:
        if await self.autorizado(interaction):
            await interaction.response.send_modal(
                TextoModal(self)
            )

    async def enviar(self, interaction: discord.Interaction) -> None:
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
        if self.config.limite is None:
            await interaction.response.send_message(
                "Defina o número de ganhadores.",
                ephemeral=True,
            )
            return

        bot_membro = self.config.canal.guild.me
        permissao = self.config.canal.permissions_for(bot_membro)
        if not permissao.view_channel or not permissao.send_messages:
            await interaction.response.send_message(
                "Não consigo enviar mensagens nesse canal.",
                ephemeral=True,
            )
            return

        evento = Evento(
            cargo=self.config.cargo,
            limite=self.config.limite,
        )
        view = EventoView(evento)
        texto_enviado = None

        try:
            if self.config.texto:
                texto_enviado = await self.config.canal.send(
                    self.config.texto,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            evento.mensagem = await self.config.canal.send(view=view)
            view.mensagem = evento.mensagem
        except (
            discord.Forbidden,
            discord.HTTPException,
        ):
            if texto_enviado is not None:
                try:
                    await texto_enviado.delete()
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

        await interaction.response.edit_message(
            view=EventoCartao(
                "Evento enviado",
                "O evento foi enviado no canal escolhido.",
            )
        )


class EventoView(discord.ui.LayoutView):
    def __init__(self, evento: Evento) -> None:
        super().__init__(timeout=None)
        self.evento = evento
        self.mensagem: Optional[discord.Message] = None

        botao = discord.ui.Button(
            label="Fazer carinho",
            style=discord.ButtonStyle.danger,
            custom_id=f"evento:fazer_carinho:{id(evento)}",
        )
        botao.callback = self.fazer_carinho

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(EVENTO_IMAGEM_URL)
                ),
                discord.ui.ActionRow(botao),
            )
        )

    async def fazer_carinho(
        self,
        interaction: discord.Interaction,
    ) -> None:
        evento = self.evento

        async with evento.lock:
            if interaction.user.id in evento.ganhadores:
                await interaction.response.send_message(
                    "Você já ganhou este evento.",
                    ephemeral=True,
                )
                return

            if len(evento.ganhadores) >= evento.limite:
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
            bot_membro = interaction.guild.me

            if membro is None or bot_membro is None:
                await interaction.response.send_message(
                    "Não consegui encontrar os membros do servidor.",
                    ephemeral=True,
                )
                return

            if not bot_membro.guild_permissions.manage_roles:
                await interaction.response.send_message(
                    "O Poyo não tem a permissão `Gerenciar cargos`.",
                    ephemeral=True,
                )
                return

            if evento.cargo >= bot_membro.top_role:
                await interaction.response.send_message(
                    "O cargo sorteado precisa estar abaixo do meu cargo.",
                    ephemeral=True,
                )
                return

            try:
                await membro.add_roles(
                    evento.cargo,
                    reason="Ganhador de evento.",
                )
            except (
                discord.Forbidden,
                discord.HTTPException,
            ):
                await interaction.response.send_message(
                    "Não consegui entregar o cargo. Verifique as permissões e a hierarquia do Poyo.",
                    ephemeral=True,
                )
                return

            evento.ganhadores.add(
                interaction.user.id
            )

            await interaction.response.send_message(
                f"Você ganhou e recebeu {evento.cargo.mention}!",
                ephemeral=True,
            )

            if (
                len(evento.ganhadores) >= evento.limite
                and evento.mensagem is not None
            ):
                try:
                    await evento.mensagem.delete()
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
            "descricao": "Abre o painel de configuração de eventos.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def poyoevent(
        self,
        ctx: commands.Context,
    ) -> None:
        try:
            await ctx.message.delete()
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

        config = Configuracao(
            autor_id=ctx.author.id,
            guild_id=ctx.guild.id,
        )
        config.painel = await ctx.send(
            view=Inicio(config)
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Eventos(bot))
