from __future__ import annotations

import asyncio
import io
import textwrap
import urllib.request
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# Emojis personalizados pedidos
EMOJI_HEADER = discord.PartialEmoji(name="poyo_tk_renomear", id=1561077420835086346)
EMOJI_SEND = discord.PartialEmoji(name="4outros", id=1557614626441666602)
EMOJI_YES = discord.PartialEmoji(name="zev", id=1522067662996242593)
EMOJI_NO = discord.PartialEmoji(name="zev", id=1522067748786667733)
EMOJI_CONFIRM = discord.PartialEmoji(name="poyo_tk_call", id=1561077098397831249)

# GIF rosa que já existe na pasta assets do repositório.
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


async def gerar_cartao_png(
    autor: discord.abc.User,
    destinatario: discord.abc.User,
    mensagem: str,
    anonima: bool,
) -> discord.File:
    """Gera um cartão PNG branco estilo Tellonym, com os dois avatares."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError(
            "O Pillow é obrigatório para gerar o cartão de imagem. "
            "Instale com: pip install Pillow"
        ) from exc

    async def ler_avatar(usuario: discord.abc.User) -> Optional[bytes]:
        try:
            return await usuario.display_avatar.replace(size=128, format="png").read()
        except Exception:
            return None

    avatar_autor = None if anonima else await ler_avatar(autor)
    avatar_destinatario = await ler_avatar(destinatario)

    largura, margem = 1000, 42
    fonte_regular = fonte_bold = None
    for caminho in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        try:
            fonte_regular = ImageFont.truetype(caminho, 30)
            break
        except OSError:
            continue
    for caminho in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        try:
            fonte_bold = ImageFont.truetype(caminho, 34)
            break
        except OSError:
            continue
    fonte_regular = fonte_regular or ImageFont.load_default()
    fonte_bold = fonte_bold or ImageFont.load_default()

    linhas: list[str] = []
    for paragrafo in mensagem.splitlines() or [mensagem]:
        linhas.extend(textwrap.wrap(paragrafo, width=48) or [""])
    linhas = linhas[:25]
    altura = max(330, 280 + len(linhas) * 45)

    imagem = Image.new("RGB", (largura, altura), (255, 255, 255))
    desenho = ImageDraw.Draw(imagem)
    preto = (28, 30, 34)
    cinza = (130, 134, 141)
    claro = (232, 234, 237)

    def desenhar_avatar(dados: Optional[bytes], x: int, y: int, tamanho: int = 72):
        if not dados:
            desenho.ellipse((x, y, x + tamanho, y + tamanho), fill=(90, 95, 105))
            desenho.text((x + 20, y + 15), "?", fill="white", font=fonte_bold)
            return
        try:
            avatar = Image.open(io.BytesIO(dados)).convert("RGBA").resize((tamanho, tamanho))
            mascara = Image.new("L", (tamanho, tamanho), 0)
            ImageDraw.Draw(mascara).ellipse((0, 0, tamanho, tamanho), fill=255)
            imagem.paste(avatar, (x, y), mascara)
        except Exception:
            desenho.ellipse((x, y, x + tamanho, y + tamanho), fill=(90, 95, 105))

    # Avatar e nome do remetente, ou avatar genérico/nome anônimo.
    desenhar_avatar(avatar_autor, margem, 30, 72)
    nome = "Anônimo" if anonima else str(autor)
    handle = "@anonimo" if anonima else f"@{autor.name}"
    desenho.text((margem + 90, 30), nome[:32], fill=preto, font=fonte_bold)
    desenho.text((margem + 90, 74), handle[:42], fill=cinza, font=fonte_regular)

    # Destinatário no lado direito, como no cartão de referência.
    desenho.text((largura - 360, 35), "Mencionados", fill=preto, font=fonte_bold)
    desenho.text((largura - 360, 78), str(destinatario)[:24], fill=preto, font=fonte_regular)
    desenhar_avatar(avatar_destinatario, largura - margem - 66, 72, 54)

    desenho.line((margem, 132, largura - margem, 132), fill=claro, width=2)
    y = 160
    for linha in linhas:
        desenho.text((margem, y), linha, fill=preto, font=fonte_regular)
        y += 45

    rodape_y = altura - 55
    desenho.line((margem, rodape_y, largura - margem, rodape_y), fill=claro, width=2)
    desenho.ellipse((margem, altura - 39, margem + 18, altura - 21), fill=(205, 211, 218))
    desenho.text((margem + 28, altura - 43), "há poucos segundos", fill=cinza, font=ImageFont.load_default())

    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename="mensagem-anonima.png")

class PublicacaoView(discord.ui.View):
    """Botões em duas linhas: Responder | Comentar; depois Comentários (N) centralizado."""

    def __init__(
        self,
        destinatario_id: int,
        autor_id: int,
        anonima: bool,
        comentarios: Optional[list[dict]] = None,
    ):
        super().__init__(timeout=None)
        self.destinatario_id = destinatario_id
        self.autor_id = autor_id
        self.anonima = anonima
        self.comentarios = comentarios if comentarios is not None else []

        self.add_item(ResponderButton(self))
        self.add_item(ComentarButton(self))
        self.add_item(VerComentariosButton(self))


class ResponderButton(discord.ui.Button):
    def __init__(self, parent: PublicacaoView):
        super().__init__(
            label="Responder",
            emoji=EMOJI_SEND,
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.destinatario_id:
            return await interaction.response.send_message(
                "Somente o destinatário pode responder a esta mensagem.", ephemeral=True
            )
        await interaction.response.send_modal(
            RespostaModal(self.parent_view.autor_id, self.parent_view.anonima)
        )


class ComentarButton(discord.ui.Button):
    def __init__(self, parent: PublicacaoView):
        super().__init__(
            label="Comentar",
            emoji=EMOJI_CONFIRM,
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            ComentarioModal(self.parent_view, interaction.user.id)
        )


class VerComentariosButton(discord.ui.Button):
    def __init__(self, parent: PublicacaoView):
        super().__init__(
            label=f"Comentários ({len(parent.comentarios)})",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction):
        if not self.parent_view.comentarios:
            return await interaction.response.send_message(
                "Ainda não há comentários nesta mensagem.", ephemeral=True
            )

        linhas = []
        for item in self.parent_view.comentarios[-10:]:
            linhas.append(f"**{discord.utils.escape_markdown(item['nome'])}:** {discord.utils.escape_mentions(item['texto'])}")
        await interaction.response.send_message(
            "\n".join(linhas),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


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
        placeholder="Escreva seu comentário…",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=1000,
    )

    def __init__(self, publicacao_view: PublicacaoView, comentarista_id: int):
        super().__init__(timeout=300)
        self.publicacao_view = publicacao_view
        self.comentarista_id = comentarista_id

    async def on_submit(self, interaction: discord.Interaction):
        texto = limitar(str(self.comentario.value), 1000)
        nome = interaction.user.display_name

        # Guarda o comentário na própria view da publicação e atualiza o contador.
        self.publicacao_view.comentarios.append({
            "id": self.comentarista_id,
            "nome": nome,
            "texto": texto,
        })
        botao_comentarios = next(
            item for item in self.publicacao_view.children
            if isinstance(item, VerComentariosButton)
        )
        botao_comentarios.label = f"Comentários ({len(self.publicacao_view.comentarios)})"

        try:
            if interaction.message:
                await interaction.message.edit(view=self.publicacao_view)
        except discord.HTTPException:
            pass

        await interaction.response.send_message(
            f"Comentário adicionado. Total: {len(self.publicacao_view.comentarios)}.",
            ephemeral=True,
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

        try:
            arquivo = await gerar_cartao_png(autor, destinatario, mensagem, anonima)
        except RuntimeError as erro:
            return False, str(erro)
        view = PublicacaoView(destinatario.id, autor.id, anonima)
        try:
            await canal.send(
                content=destinatario.mention,
                file=arquivo,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException:
            return False, "Falha ao publicar. Verifique as permissões do bot."
        return True, f"Mensagem enviada para {destinatario.mention}."


async def setup(bot: commands.Bot):
    await bot.add_cog(Anonimo(bot))
