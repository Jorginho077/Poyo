from __future__ import annotations

import asyncio
import io
import textwrap
import urllib.request
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# Emojis
EMOJI_HEADER = discord.PartialEmoji(name="poyo_tk_renomear", id=1561077420835086346)
EMOJI_SEND = discord.PartialEmoji(name="4outros", id=1557614626441666602)
EMOJI_YES = discord.PartialEmoji(name="zev", id=1522067662996242593)
EMOJI_NO = discord.PartialEmoji(name="zev", id=1522067748786667733)
EMOJI_CONFIRM = discord.PartialEmoji(name="poyo_tk_call", id=1561077098397831249)

# GIF
BANNER_URL = (
    "https://raw.githubusercontent.com/Jorginho077/Poyo/main/"
    "assets/Tumblr-l-67143811701311.gif"
)
BANNER_FILENAME = "poyo-anonimo-banner.gif"
BANNER_ATTACHMENT = f"attachment://{BANNER_FILENAME}"


async def baixar_banner() -> Optional[discord.File]:
    """Baixa o banner animado para anexá-lo ao painel."""
    def baixar() -> bytes:
        request = urllib.request.Request(
            BANNER_URL, headers={"User-Agent": "PoyoBot/1.0"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read()

    try:
        data = await asyncio.to_thread(baixar)
        return discord.File(io.BytesIO(data), filename=BANNER_FILENAME)
    except Exception:
        return None


def criar_painel_view(banner_anexado: bool) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    banner = BANNER_ATTACHMENT if banner_anexado else BANNER_URL
    view.add_item(
        discord.ui.Container(
            discord.ui.MediaGallery(discord.MediaGalleryItem(banner)),
            discord.ui.TextDisplay(
                f"{EMOJI_HEADER} **Mande sua mensagem anonima para qual quer um!**"
            ),
            discord.ui.Separator(visible=False),
            discord.ui.ActionRow(EnviarMensagemButton()),
            discord.ui.MediaGallery(discord.MediaGalleryItem(banner)),
        )
    )
    return view


def limitar(texto: str, limite: int = 1800) -> str:
    texto = texto.strip()
    return texto[: limite - 1] + "…" if len(texto) > limite else texto


class EnviarMensagemButton(discord.ui.Button):
    def __init__(self):
        # O Discord não oferece estilo preto puro; secondary é o estilo escuro disponível.
        super().__init__(
            label="mande sua mensagem",
            emoji=EMOJI_SEND,
            style=discord.ButtonStyle.secondary,
            custom_id="poyo_anonimo:iniciar",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "A mensagem deve ser anônima?",
            view=EscolherAnonimatoView(),
            ephemeral=True,
        )


class EscolherAnonimatoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.select(
        placeholder="A mensagem deve ser anônima?",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(
                label="Sim",
                value="sim",
                emoji=EMOJI_YES,
                description="Seu nome e avatar não serão mostrados.",
            ),
            discord.SelectOption(
                label="Não",
                value="nao",
                emoji=EMOJI_NO,
                description="Seu nome e avatar serão mostrados.",
            ),
        ],
    )
    async def escolher(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.send_message(
            "Escolha a pessoa que receberá a mensagem:",
            view=EscolherDestinatarioView(
                anonima=select.values[0] == "sim",
                autor_id=interaction.user.id,
                guild_id=interaction.guild_id,
            ),
            ephemeral=True,
        )


class EscolherDestinatarioView(discord.ui.View):
    def __init__(self, anonima: bool, autor_id: int, guild_id: Optional[int]):
        super().__init__(timeout=180)
        self.anonima = anonima
        self.autor_id = autor_id
        self.guild_id = guild_id
        self.add_item(DestinatarioSelect(self))


class DestinatarioSelect(discord.ui.UserSelect):
    def __init__(self, fluxo: EscolherDestinatarioView):
        super().__init__(
            placeholder="Pesquisar pessoa do servidor…",
            min_values=1,
            max_values=1,
            custom_id="poyo_anonimo:destinatario",
        )
        self.fluxo = fluxo

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.fluxo.autor_id:
            return await interaction.response.send_message(
                "Somente quem iniciou o envio pode escolher o destinatário.",
                ephemeral=True,
            )

        destinatario = self.values[0]
        if destinatario.id == interaction.user.id:
            return await interaction.response.send_message(
                "Você não pode enviar uma mensagem para si mesmo.", ephemeral=True
            )
        if destinatario.bot:
            return await interaction.response.send_message(
                "Escolha uma pessoa, não um bot.", ephemeral=True
            )

        await interaction.response.send_modal(
            EscreverMensagemModal(self.fluxo.anonima, destinatario.id)
        )


class EscreverMensagemModal(discord.ui.Modal, title="Escrever mensagem"):
    mensagem = discord.ui.TextInput(
        label="Sua mensagem",
        placeholder="Digite a mensagem que deseja enviar…",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=1500,
        required=True,
    )

    def __init__(self, anonima: bool, destinatario_id: int):
        super().__init__(timeout=300)
        self.anonima = anonima
        self.destinatario_id = destinatario_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Confira sua mensagem. Deseja confirmar o envio?",
            view=ConfirmarEnvioView(
                autor_id=interaction.user.id,
                destinatario_id=self.destinatario_id,
                anonima=self.anonima,
                mensagem=str(self.mensagem.value),
            ),
            ephemeral=True,
        )


class ConfirmarEnvioView(discord.ui.View):
    def __init__(self, autor_id: int, destinatario_id: int, anonima: bool, mensagem: str):
        super().__init__(timeout=180)
        self.autor_id = autor_id
        self.destinatario_id = destinatario_id
        self.anonima = anonima
        self.mensagem = limitar(mensagem, 1500)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Somente quem escreveu a mensagem pode confirmá-la.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Confirmar envio",
        emoji=EMOJI_CONFIRM,
        style=discord.ButtonStyle.secondary,
    )
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Anonimo")
        if cog is None:
            return await interaction.response.send_message(
                "O sistema de mensagens anônimas não está disponível.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        sucesso, resposta = await cog.publicar_mensagem(
            interaction.guild,
            interaction.user,
            self.destinatario_id,
            self.anonima,
            self.mensagem,
        )
        for item in self.children:
            item.disabled = True
        try:
            await interaction.edit_original_response(view=self)
        except discord.HTTPException:
            pass
        await interaction.followup.send(resposta, ephemeral=True)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Envio cancelado.", view=self)


def gerar_cartao_png(
    autor: discord.abc.User,
    destinatario: discord.abc.User,
    mensagem: str,
    anonima: bool,
) -> Optional[discord.File]:
    """Gera um cartão branco com o texto. Pillow é opcional."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    width, margin = 1000, 42
    regular_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    bold_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]

    def load_font(paths, size):
        for path in paths:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    regular = load_font(regular_paths, 30)
    bold = load_font(bold_paths, 34)
    lines = []
    for paragraph in mensagem.splitlines() or [mensagem]:
        lines.extend(textwrap.wrap(paragraph, width=48) or [""])
    lines = lines[:25]
    height = max(330, 230 + len(lines) * 45)

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    black, gray = (28, 30, 34), (100, 105, 115)

    draw.text((margin, 35), "Anônimo" if anonima else str(autor)[:40], fill=black, font=bold)
    draw.text((margin, 78), "@anonimo" if anonima else f"@{autor.name}"[:48], fill=gray, font=regular)
    draw.text((width - 360, 42), "Mencionados", fill=gray, font=regular)
    draw.text((width - 360, 82), str(destinatario)[:30], fill=black, font=regular)
    draw.line((margin, 132, width - margin, 132), fill=(230, 232, 235), width=2)

    y = 165
    for line in lines:
        draw.text((margin, y), line, fill=black, font=regular)
        y += 45

    draw.line((margin, height - 55, width - margin, height - 55), fill=(235, 236, 238), width=2)
    draw.text((margin, height - 43), "há poucos segundos", fill=(165, 168, 173), font=ImageFont.load_default())

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename="mensagem-anonima.png")


class PublicacaoView(discord.ui.View):
    def __init__(self, destinatario_id: int, autor_id: int, anonima: bool):
        super().__init__(timeout=None)
        self.destinatario_id = destinatario_id
        self.autor_id = autor_id
        self.anonima = anonima

    @discord.ui.button(
        label="Responder",
        emoji=EMOJI_SEND,
        style=discord.ButtonStyle.secondary,
    )
    async def responder(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.destinatario_id:
            return await interaction.response.send_message(
                "Somente o destinatário pode responder a esta mensagem.", ephemeral=True
            )
        await interaction.response.send_modal(RespostaModal(self.autor_id, self.anonima))

    @discord.ui.button(
        label="Comentar",
        emoji=EMOJI_CONFIRM,
        style=discord.ButtonStyle.secondary,
    )
    async def comentar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ComentarioModal())


class RespostaModal(discord.ui.Modal, title="Responder mensagem"):
    resposta = discord.ui.TextInput(
        label="Sua resposta",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=1000,
    )

    def __init__(self, autor_original_id: int, era_anonima: bool):
        super().__init__(timeout=300)
        self.autor_original_id = autor_original_id
        self.era_anonima = era_anonima

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Anonimo")
        canal = cog.canais.get(interaction.guild_id) if cog else None
        if canal is None:
            return await interaction.response.send_message(
                "O canal de mensagens anônimas não está configurado.", ephemeral=True
            )
        embed = discord.Embed(
            description=limitar(str(self.resposta.value), 1000),
            color=discord.Color.from_rgb(255, 255, 255),
        )
        embed.set_author(
            name=f"Resposta de {interaction.user}",
            icon_url=interaction.user.display_avatar.url,
        )
        try:
            await canal.send(
                content=f"<@{self.autor_original_id}>",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            await interaction.response.send_message("Resposta enviada.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message(
                "Não consegui enviar a resposta.", ephemeral=True
            )


class ComentarioModal(discord.ui.Modal, title="Comentar mensagem"):
    comentario = discord.ui.TextInput(
        label="Comentário",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Anonimo")
        canal = cog.canais.get(interaction.guild_id) if cog else None
        if canal is None:
            return await interaction.response.send_message(
                "O canal de mensagens anônimas não está configurado.", ephemeral=True
            )
        embed = discord.Embed(
            description=limitar(str(self.comentario.value), 1000),
            color=discord.Color.from_rgb(255, 255, 255),
        )
        embed.set_author(
            name=f"Comentário de {interaction.user}",
            icon_url=interaction.user.display_avatar.url,
        )
        try:
            await canal.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            await interaction.response.send_message("Comentário publicado.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message(
                "Não consegui publicar o comentário.", ephemeral=True
            )


class Anonimo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Configuração em memória: após reiniciar, execute o comando do painel novamente.
        self.canais: dict[int, discord.TextChannel] = {}

    @app_commands.command(
        name="anonimo_painel",
        description="Publica o painel de mensagens anônimas neste canal.",
    )
    @app_commands.describe(canal="Canal onde as mensagens serão publicadas.")
    @app_commands.guild_only()
    async def anonimo_painel(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
    ):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(
                "Você precisa da permissão Gerenciar servidor.", ephemeral=True
            )
        if interaction.guild is None or interaction.channel is None:
            return await interaction.response.send_message(
                "Use este comando em um servidor.", ephemeral=True
            )
        bot_member = interaction.guild.me
        if bot_member is None or not canal.permissions_for(bot_member).send_messages:
            return await interaction.response.send_message(
                "Não tenho permissão para enviar mensagens no canal selecionado.",
                ephemeral=True,
            )

        self.canais[interaction.guild.id] = canal
        banner_file = await baixar_banner()
        try:
            await interaction.channel.send(
                view=criar_painel_view(banner_anexado=banner_file is not None),
                file=banner_file,
            )
        except discord.HTTPException:
            return await interaction.response.send_message(
                "Não consegui publicar o painel. Confira minhas permissões.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            f"Painel publicado neste canal. As mensagens serão enviadas em {canal.mention}.",
            ephemeral=True,
        )

    async def publicar_mensagem(
        self,
        guild: Optional[discord.Guild],
        autor: discord.abc.User,
        destinatario_id: int,
        anonima: bool,
        mensagem: str,
    ) -> tuple[bool, str]:
        if guild is None:
            return False, "Não consegui identificar o servidor."
        canal = self.canais.get(guild.id)
        if canal is None:
            return False, "Canal não configurado. Execute /anonimo_painel novamente."

        destinatario = guild.get_member(destinatario_id)
        if destinatario is None:
            try:
                destinatario = await guild.fetch_member(destinatario_id)
            except (discord.NotFound, discord.HTTPException):
                return False, "Não encontrei o destinatário no servidor."

        arquivo = gerar_cartao_png(autor, destinatario, mensagem, anonima)
        view = PublicacaoView(destinatario.id, autor.id, anonima)
        try:
            if arquivo:
                await canal.send(
                    content=destinatario.mention,
                    file=arquivo,
                    view=view,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            else:
                embed = discord.Embed(
                    description=limitar(mensagem, 1500),
                    color=discord.Color.from_rgb(255, 255, 255),
                )
                embed.set_author(
                    name="Anônimo" if anonima else str(autor),
                    icon_url=None if anonima else autor.display_avatar.url,
                )
                embed.add_field(name="Mencionados", value=destinatario.mention, inline=False)
                await canal.send(
                    content=destinatario.mention,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
        except discord.HTTPException:
            return False, "Falha ao publicar. Verifique as permissões do bot."
        return True, f"Mensagem enviada para {destinatario.mention}."


async def setup(bot: commands.Bot):
    await bot.add_cog(Anonimo(bot))
