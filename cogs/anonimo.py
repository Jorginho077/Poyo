from __future__ import annotations

import asyncio
import io
import json
import textwrap
import urllib.request
from pathlib import Path
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
CONFIG_PATH = Path("data") / "anonimo_canais.json"


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

        await interaction.response.send_message(
            "Deseja enviar a mensagem no privado do destinatário?",
            view=EscolherDestinoEnvioView(
                anonima=self.fluxo.anonima,
                destinatario_id=destinatario.id,
                autor_id=interaction.user.id,
            ),
            ephemeral=True,
        )


class EscolherDestinoEnvioView(discord.ui.View):
    def __init__(self, anonima: bool, destinatario_id: int, autor_id: int):
        super().__init__(timeout=180)
        self.anonima = anonima
        self.destinatario_id = destinatario_id
        self.autor_id = autor_id

    @discord.ui.select(
        placeholder="Enviar no privado?",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(
                label="Sim",
                value="sim",
                emoji=EMOJI_YES,
                description="Envia o cartão por mensagem direta (PV).",
            ),
            discord.SelectOption(
                label="Não",
                value="nao",
                emoji=EMOJI_NO,
                description="Publica o cartão no canal configurado pelo painel.",
            ),
        ],
    )
    async def escolher_destino(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.autor_id:
            return await interaction.response.send_message(
                "Somente quem iniciou o envio pode escolher o destino.", ephemeral=True
            )
        await interaction.response.send_modal(
            EscreverMensagemModal(
                self.anonima, self.destinatario_id, enviar_privado=select.values[0] == "sim"
            )
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

    def __init__(self, anonima: bool, destinatario_id: int, enviar_privado: bool = False):
        super().__init__(timeout=300)
        self.anonima = anonima
        self.destinatario_id = destinatario_id
        self.enviar_privado = enviar_privado

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Confira sua mensagem. Deseja confirmar o envio?",
            view=ConfirmarEnvioView(
                autor_id=interaction.user.id,
                destinatario_id=self.destinatario_id,
                anonima=self.anonima,
                mensagem=str(self.mensagem.value),
                enviar_privado=self.enviar_privado,
            ),
            ephemeral=True,
        )


class ConfirmarEnvioView(discord.ui.View):
    def __init__(
        self, autor_id: int, destinatario_id: int, anonima: bool, mensagem: str,
        enviar_privado: bool = False,
    ):
        super().__init__(timeout=180)
        self.autor_id = autor_id
        self.destinatario_id = destinatario_id
        self.anonima = anonima
        self.mensagem = limitar(mensagem, 1500)
        self.enviar_privado = enviar_privado

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
            enviar_privado=self.enviar_privado,
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
    """Gera um cartão branco de alta resolução inspirado na referência enviada."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError(
            "O Pillow é obrigatório para gerar o cartão. Instale com: pip install Pillow"
        ) from exc

    async def ler_avatar(usuario: discord.abc.User) -> Optional[bytes]:
        try:
            return await usuario.display_avatar.replace(size=256, format="png").read()
        except Exception:
            return None

    dados_autor = None if anonima else await ler_avatar(autor)
    dados_dest = await ler_avatar(destinatario)

    # Tudo é desenhado em 2x e reduzido com LANCZOS para bordas e texto mais nítidos.
    S = 2
    W, PAD = 1536, 64
    AVATAR, MINI = 112, 112
    HEADER_LINE_Y = 204
    MESSAGE_TOP = 244
    LINE_HEIGHT = 76
    FOOTER_HEIGHT = 82

    def p(n: int) -> int:
        return n * S

    def font(paths: tuple[str, ...], size: int):
        for path in paths:
            try:
                return ImageFont.truetype(path, p(size))
            except OSError:
                pass
        return ImageFont.load_default()

    regular = font((
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ), 52)
    bold = font((
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ), 36)
    mentioned_title_font = font((
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ), 31)
    mentioned_font = font((
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ), 30)
    footer_font = font((
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ), 14)

    WHITE = (255, 255, 255)
    PINK = (255, 183, 206)  # Rosa-bebê inspirado no emoji 🩷.
    SEPARATOR = (255, 218, 229)
    FOOTER_GRAY = WHITE

    def avatar_img(data: Optional[bytes], size: int, fallback: str):
        size_px = p(size)
        avatar = None
        if data:
            try:
                avatar = Image.open(io.BytesIO(data)).convert("RGBA")
                avatar = avatar.resize((size_px, size_px), Image.Resampling.LANCZOS)
            except Exception:
                avatar = None
        if avatar is None:
            avatar = Image.new("RGBA", (size_px, size_px), (226, 229, 234, 255))
            d = ImageDraw.Draw(avatar)
            f = font((
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            ), max(16, size // 2))
            d.text((size_px // 2, size_px // 2), (fallback or "?")[:1].upper(),
                   fill=(95, 99, 108, 255), font=f, anchor="mm")
        mask = Image.new("L", (size_px, size_px), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size_px - 1, size_px - 1), fill=255)
        avatar.putalpha(mask)
        return avatar

    # Quebra de texto pelo tamanho real das letras, não por número fixo de caracteres.
    max_text_width = p(W - PAD * 2)
    probe = Image.new("RGB", (max_text_width, 100), WHITE)
    probe_draw = ImageDraw.Draw(probe)
    linhas: list[str] = []
    for paragrafo in (limitar(mensagem, 1500).splitlines() or [mensagem]):
        if not paragrafo.strip():
            linhas.append("")
            continue
        atual = ""
        for palavra in paragrafo.split():
            tentativa = f"{atual} {palavra}".strip()
            if probe_draw.textlength(tentativa, font=regular) <= max_text_width:
                atual = tentativa
            else:
                if atual:
                    linhas.append(atual)
                atual = palavra
        if atual:
            linhas.append(atual)
    linhas = (linhas or [""])[:30]

    text_height = max(p(48), len(linhas) * p(LINE_HEIGHT))
    footer_y = p(MESSAGE_TOP) + text_height + p(35)
    height = max(p(510), footer_y + p(FOOTER_HEIGHT))
    image = Image.new("RGB", (p(W), height), PINK)
    draw = ImageDraw.Draw(image)

    # Avatar e identificação do remetente à esquerda.
    sender_avatar = avatar_img(
        dados_autor,
        AVATAR,
        "?" if anonima else (autor.display_name[:1] or "?"),
    )
    image.paste(sender_avatar, (p(PAD), p(43)), sender_avatar)

    name_x = p(PAD + AVATAR + 28)
    sender_name = "Anônimo" if anonima else autor.display_name
    sender_handle = "@anonimo" if anonima else f"@{autor.name}"
    draw.text((name_x, p(48)), sender_name[:32], fill=WHITE, font=bold)
    draw.text((name_x, p(111)), sender_handle[:42], fill=WHITE, font=regular)

    # Área do destinatário menor e deslocada para a direita, como na referência.
    recipient_avatar = avatar_img(
        dados_dest, MINI, destinatario.display_name[:1] or "?"
    )
    avatar_x = p(W - PAD - MINI)
    avatar_y = p(82)
    image.paste(recipient_avatar, (avatar_x, avatar_y), recipient_avatar)

    label = "Mencionados"
    draw.text((p(984), p(46)), label, fill=WHITE, font=mentioned_title_font)
    recipient_name = destinatario.display_name or destinatario.name
    recipient_name_x = p(984)
    recipient_name_y = p(105)
    # Mantém o nome maior, mas limita-o ao espaço antes do avatar para não sobrepor.
    recipient_max_width = avatar_x - recipient_name_x - p(18)
    recipient_name_display = recipient_name
    while recipient_name_display and draw.textlength(recipient_name_display, font=mentioned_font) > recipient_max_width:
        recipient_name_display = recipient_name_display[:-2].rstrip() + "…"
        if len(recipient_name_display) <= 2:
            break
    draw.text((recipient_name_x, recipient_name_y), recipient_name_display, fill=WHITE, font=mentioned_font)

    # Linha horizontal do cabeçalho.
    draw.line((p(PAD), p(HEADER_LINE_Y), p(W - PAD), p(HEADER_LINE_Y)),
              fill=SEPARATOR, width=p(2))

    # Mensagem, com espaço e tamanho próximos da imagem de referência.
    y = p(MESSAGE_TOP)
    for line in linhas:
        draw.text((p(PAD), y), line, fill=WHITE, font=regular)
        y += p(LINE_HEIGHT)

    # Rodapé discreto.
    draw.line((p(PAD), footer_y, p(W - PAD), footer_y), fill=SEPARATOR, width=p(2))
    dot_x, dot_y = p(PAD + 2), footer_y + p(23)
    draw.ellipse((dot_x, dot_y, dot_x + p(19), dot_y + p(19)), fill=FOOTER_GRAY)
    draw.text((p(PAD + 30), footer_y + p(20)), "há poucos segundos",
              fill=FOOTER_GRAY, font=footer_font)

    # Reduz a imagem mantendo boa qualidade e arquivo menor para o Discord.
    final_image = image.resize((W, max(1, height // S)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    final_image.save(buffer, format="PNG", optimize=True)
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
        # Não usar embeds neste sistema: respostas também são texto simples.
        resposta_texto = limitar(str(self.resposta.value), 1000)
        try:
            await canal.send(
                content=(
                    f"<@{self.autor_original_id}>\n"
                    f"**Resposta de {discord.utils.escape_markdown(interaction.user.display_name)}:**\n"
                    f"{discord.utils.escape_mentions(resposta_texto)}"
                ),
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
        self.canais: dict[int, discord.TextChannel] = {}
        self._canais_ids: dict[int, int] = {}

    def _salvar_configuracao(self) -> None:
        """Persiste os IDs dos canais para restaurar a configuração após reinícios."""
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporario = CONFIG_PATH.with_suffix(".tmp")
            temporario.write_text(
                json.dumps({str(guild_id): channel_id for guild_id, channel_id in self._canais_ids.items()}, indent=2),
                encoding="utf-8",
            )
            temporario.replace(CONFIG_PATH)
        except OSError as erro:
            print(f"[Anonimo] Não consegui salvar a configuração: {erro}")

    async def cog_load(self) -> None:
        """Registra o botão persistente e recupera os canais configurados."""
        # A view não tem timeout e o botão possui custom_id fixo, então funciona
        # também nos painéis publicados antes do último reinício.
        try:
            self.bot.add_view(criar_painel_view(banner_anexado=False))
        except (TypeError, ValueError) as erro:
            print(f"[Anonimo] Não consegui registrar a view persistente: {erro}")

        try:
            if CONFIG_PATH.exists():
                dados = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                self._canais_ids = {int(guild_id): int(channel_id) for guild_id, channel_id in dados.items()}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as erro:
            print(f"[Anonimo] Não consegui carregar a configuração: {erro}")
            self._canais_ids = {}

        for guild_id, channel_id in list(self._canais_ids.items()):
            canal = self.bot.get_channel(channel_id)
            if canal is None:
                try:
                    canal = await self.bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            if isinstance(canal, discord.TextChannel) and canal.guild.id == guild_id:
                self.canais[guild_id] = canal

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
        # Responde imediatamente ao Discord: baixar o banner pode levar vários segundos.
        # Sem defer, a interação expira após ~3 segundos e aparece "o bot não respondeu a tempo".
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.followup.send(
                "Você precisa da permissão Gerenciar servidor.", ephemeral=True
            )
        if interaction.guild is None or interaction.channel is None:
            return await interaction.followup.send(
                "Use este comando em um servidor.", ephemeral=True
            )
        bot_member = interaction.guild.me
        if bot_member is None or not canal.permissions_for(bot_member).send_messages:
            return await interaction.followup.send(
                "Não tenho permissão para enviar mensagens no canal selecionado.",
                ephemeral=True,
            )

        # Salva a configuração antes de qualquer operação de rede.
        self.canais[interaction.guild.id] = canal
        self._canais_ids[interaction.guild.id] = canal.id
        self._salvar_configuracao()

        banner_file = await baixar_banner()
        try:
            kwargs = {"view": criar_painel_view(banner_anexado=banner_file is not None)}
            if banner_file is not None:
                kwargs["file"] = banner_file
            await interaction.channel.send(**kwargs)
        except discord.HTTPException as erro:
            print(f"[Anonimo] Falha ao publicar painel: {erro}")
            return await interaction.followup.send(
                "Não consegui publicar o painel. Confira minhas permissões e tente novamente.",
                ephemeral=True,
            )
        await interaction.followup.send(
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
        enviar_privado: bool = False,
    ) -> tuple[bool, str]:
        if guild is None:
            return False, "Não consegui identificar o servidor."
        canal = self.canais.get(guild.id)
        if not enviar_privado and canal is None:
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
        try:
            if enviar_privado:
                # No PV, a publicação é o cartão PNG; não há embed nem texto adicional.
                await destinatario.send(file=arquivo, allowed_mentions=discord.AllowedMentions.none())
                return True, f"Cartão enviado no privado de {destinatario.mention}."

            if canal is None:
                return False, "Canal não configurado. Execute /anonimo_painel novamente."
            view = PublicacaoView(destinatario.id, autor.id, anonima)
            await canal.send(
                content=destinatario.mention,
                file=arquivo,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.Forbidden:
            if enviar_privado:
                return False, "Não consegui enviar o PV. A pessoa pode ter mensagens diretas bloqueadas para este servidor."
            return False, "Falha ao publicar. Verifique as permissões do bot."
        except discord.HTTPException:
            return False, "Falha ao enviar o cartão. Tente novamente."
        return True, f"Mensagem publicada em {canal.mention} para {destinatario.mention}."


async def setup(bot: commands.Bot):
    await bot.add_cog(Anonimo(bot))
