from __future__ import annotations

import asyncio
import io
import textwrap
import urllib.request
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

EMOJI_HEADER  = discord.PartialEmoji(name="poyo_tk_renomear", id=1561077420835086346)
EMOJI_SEND    = discord.PartialEmoji(name="4outros",          id=1557614626441666602)
EMOJI_YES     = discord.PartialEmoji(name="zev",              id=1522067662996242593)
EMOJI_NO      = discord.PartialEmoji(name="zev",              id=1522067748786667733)
EMOJI_CONFIRM = discord.PartialEmoji(name="poyo_tk_call",     id=1561077098397831249)

BANNER_URL       = "https://raw.githubusercontent.com/Jorginho077/Poyo/main/assets/Tumblr-l-67143811701311.gif"
BANNER_FILENAME  = "poyo-anonimo-banner.gif"
BANNER_ATTACHMENT = f"attachment://{BANNER_FILENAME}"


async def baixar_banner() -> Optional[discord.File]:
    def _get() -> bytes:
        req = urllib.request.Request(BANNER_URL, headers={"User-Agent": "PoyoBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read()
    try:
        data = await asyncio.to_thread(_get)
        return discord.File(io.BytesIO(data), filename=BANNER_FILENAME)
    except Exception:
        return None


def limitar(texto: str, limite: int = 1800) -> str:
    texto = texto.strip()
    return texto[: limite - 1] + "…" if len(texto) > limite else texto


async def _baixar_avatar(user: discord.abc.User, size: int = 128) -> Optional[bytes]:
    url = str(user.display_avatar.replace(size=size, format="png"))
    def _get() -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "PoyoBot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()
    try:
        return await asyncio.to_thread(_get)
    except Exception:
        return None


def _circle(img_bytes: bytes, size: int):
    from PIL import Image, ImageDraw
    src  = Image.open(io.BytesIO(img_bytes)).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(src, mask=mask)
    return out


async def gerar_cartao_png(
    autor: discord.abc.User,
    destinatario: discord.abc.User,
    mensagem: str,
    anonima: bool,
) -> Optional[discord.File]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    W, PAD, AV, DAV = 1000, 40, 72, 40

    def font(paths, size):
        for p in paths:
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
        return ImageFont.load_default()

    BOLD  = font(["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"], 30)
    REG   = font(["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"], 26)
    SMALL = font(["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"], 20)

    lines = []
    for para in (mensagem.splitlines() or [mensagem]):
        lines.extend(textwrap.wrap(para, width=52) or [""])
    lines = lines[:30]

    SEP_Y      = PAD + AV + 14
    TEXT_START = SEP_Y + PAD
    LINE_H     = 36
    FOOTER_H   = 44
    H          = TEXT_START + max(len(lines) * LINE_H, LINE_H) + PAD + FOOTER_H

    WHITE     = (255, 255, 255)
    BLACK     = (28,  30,  34)
    GRAY      = (110, 115, 125)
    SEP_COL   = (220, 222, 226)
    CHIP_BG   = (30,  31,  34)
    CHIP_TXT  = (255, 255, 255)
    FOOT_COL  = (170, 173, 180)
    DISC_BLUE = (88, 101, 242)

    img  = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    # avatar autor
    av_bytes = None if anonima else await _baixar_avatar(autor, 256)
    if av_bytes:
        img.paste(_circle(av_bytes, AV), (PAD, PAD), _circle(av_bytes, AV))
    else:
        layer = Image.new("RGBA", (AV, AV), (0, 0, 0, 0))
        ld    = ImageDraw.Draw(layer)
        ld.ellipse((0, 0, AV, AV), fill=DISC_BLUE)
        letter = "?" if anonima else (autor.display_name[0].upper() if autor.display_name else "?")
        ld.text((AV // 2, AV // 2), letter, fill=WHITE, font=BOLD, anchor="mm")
        img.paste(layer, (PAD, PAD), layer)

    # nome e handle
    NX = PAD + AV + 14
    draw.text((NX, PAD + 6),  "Anônimo" if anonima else autor.display_name[:36], fill=BLACK, font=BOLD)
    draw.text((NX, PAD + 42), "@anonimo" if anonima else f"@{autor.name[:40]}",  fill=GRAY,  font=SMALL)

    # bloco mencionados
    MX = W - PAD - 220
    draw.text((MX, PAD + 4), "Mencionados", fill=GRAY, font=SMALL)

    chip_text = destinatario.display_name[:20]
    chip_w    = int(draw.textlength(chip_text, font=SMALL)) + 20
    chip_y    = PAD + 30
    draw.rounded_rectangle([MX, chip_y, MX + chip_w, chip_y + 28], radius=6, fill=CHIP_BG)
    draw.text((MX + 10, chip_y + 4), chip_text, fill=CHIP_TXT, font=SMALL)

    mini_x    = MX + chip_w + 8
    dav_bytes = await _baixar_avatar(destinatario, 128)
    if dav_bytes:
        mini = _circle(dav_bytes, DAV)
        img.paste(mini, (mini_x, chip_y - 4), mini)
    else:
        ml = Image.new("RGBA", (DAV, DAV), (0, 0, 0, 0))
        ImageDraw.Draw(ml).ellipse((0, 0, DAV, DAV), fill=DISC_BLUE)
        img.paste(ml, (mini_x, chip_y - 4), ml)

    # separador
    draw.line((PAD, SEP_Y, W - PAD, SEP_Y), fill=SEP_COL, width=2)

    # texto
    ty = TEXT_START + 8
    for line in lines:
        draw.text((PAD, ty), line, fill=BLACK, font=REG)
        ty += LINE_H

    # rodapé
    FY = H - FOOTER_H
    draw.line((PAD, FY, W - PAD, FY), fill=SEP_COL, width=1)
    draw.ellipse((PAD, FY + 10, PAD + 22, FY + 28), outline=FOOT_COL, width=2)
    draw.text((PAD + 30, FY + 8), "há poucos segundos", fill=FOOT_COL, font=SMALL)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return discord.File(buf, filename="mensagem-anonima.png")


def criar_painel_view(banner_anexado: bool) -> discord.ui.LayoutView:
    view   = discord.ui.LayoutView(timeout=None)
    banner = BANNER_ATTACHMENT if banner_anexado else BANNER_URL
    view.add_item(
        discord.ui.Container(
            discord.ui.MediaGallery(discord.MediaGalleryItem(banner)),
            discord.ui.TextDisplay(f"{EMOJI_HEADER} **Mande sua mensagem anônima para qualquer um!**"),
            discord.ui.Separator(visible=False),
            discord.ui.ActionRow(EnviarMensagemButton()),
            discord.ui.MediaGallery(discord.MediaGalleryItem(banner)),
        )
    )
    return view


class EnviarMensagemButton(discord.ui.Button):
    def __init__(self):
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
        min_values=1, max_values=1,
        options=[
            discord.SelectOption(label="Sim", value="sim", emoji=EMOJI_YES,
                                 description="Seu nome e avatar não serão mostrados."),
            discord.SelectOption(label="Não", value="nao", emoji=EMOJI_NO,
                                 description="Seu nome e avatar serão mostrados."),
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
        self.anonima  = anonima
        self.autor_id = autor_id
        self.guild_id = guild_id
        self.add_item(DestinatarioSelect(self))


class DestinatarioSelect(discord.ui.UserSelect):
    def __init__(self, fluxo: EscolherDestinatarioView):
        super().__init__(
            placeholder="Pesquisar pessoa do servidor…",
            min_values=1, max_values=1,
            custom_id="poyo_anonimo:destinatario",
        )
        self.fluxo = fluxo

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.fluxo.autor_id:
            return await interaction.response.send_message(
                "Somente quem iniciou o envio pode escolher o destinatário.", ephemeral=True
            )
        dest = self.values[0]
        if dest.id == interaction.user.id:
            return await interaction.response.send_message(
                "Você não pode enviar uma mensagem para si mesmo.", ephemeral=True
            )
        if dest.bot:
            return await interaction.response.send_message(
                "Escolha uma pessoa, não um bot.", ephemeral=True
            )
        await interaction.response.send_modal(EscreverMensagemModal(self.fluxo.anonima, dest.id))


class EscreverMensagemModal(discord.ui.Modal, title="Escrever mensagem"):
    mensagem = discord.ui.TextInput(
        label="Sua mensagem",
        placeholder="Digite a mensagem que deseja enviar…",
        style=discord.TextStyle.paragraph,
        min_length=1, max_length=1500, required=True,
    )

    def __init__(self, anonima: bool, destinatario_id: int):
        super().__init__(timeout=300)
        self.anonima         = anonima
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
        self.autor_id        = autor_id
        self.destinatario_id = destinatario_id
        self.anonima         = anonima
        self.mensagem        = limitar(mensagem, 1500)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Somente quem escreveu a mensagem pode confirmá-la.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirmar envio", emoji=EMOJI_CONFIRM,
                       style=discord.ButtonStyle.secondary)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Anonimo")
        if cog is None:
            return await interaction.response.send_message(
                "Sistema indisponível.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        sucesso, resposta = await cog.publicar_mensagem(
            interaction.guild, interaction.user,
            self.destinatario_id, self.anonima, self.mensagem,
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


class PublicacaoView(discord.ui.View):
    def __init__(self, destinatario_id: int, autor_id: int, anonima: bool, contagem: int = 0):
        super().__init__(timeout=None)
        self.destinatario_id = destinatario_id
        self.autor_id        = autor_id
        self.anonima         = anonima
        self.contagem        = contagem
        self._atualizar_botoes()

    def _atualizar_botoes(self):
        self.clear_items()
        self.add_item(ResponderButton(self.destinatario_id, self.autor_id, self.anonima))
        self.add_item(ComentarButton())
        self.add_item(ContadorComentariosButton(self.contagem))

    async def incrementar_e_atualizar(self, message: discord.Message):
        self.contagem += 1
        self._atualizar_botoes()
        try:
            await message.edit(view=self)
        except discord.HTTPException:
            pass


class ResponderButton(discord.ui.Button):
    def __init__(self, destinatario_id: int, autor_id: int, anonima: bool):
        super().__init__(label="Responder", emoji=EMOJI_SEND,
                         style=discord.ButtonStyle.secondary,
                         custom_id="poyo_anonimo:responder")
        self.destinatario_id = destinatario_id
        self.autor_id        = autor_id
        self.anonima         = anonima

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.destinatario_id:
            return await interaction.response.send_message(
                "Somente o destinatário pode responder.", ephemeral=True
            )
        await interaction.response.send_modal(
            RespostaModal(self.autor_id, self.anonima, interaction.message)
        )


class ComentarButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Comentar", emoji=EMOJI_CONFIRM,
                         style=discord.ButtonStyle.secondary,
                         custom_id="poyo_anonimo:comentar")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ComentarioModal(interaction.message))


class ContadorComentariosButton(discord.ui.Button):
    def __init__(self, contagem: int):
        super().__init__(
            label=f"Comentários ({contagem})",
            emoji=EMOJI_SEND,
            style=discord.ButtonStyle.secondary,
            disabled=True,
            custom_id="poyo_anonimo:contador",
            row=1,
        )


class RespostaModal(discord.ui.Modal, title="Responder mensagem"):
    resposta = discord.ui.TextInput(
        label="Sua resposta",
        style=discord.TextStyle.paragraph,
        min_length=1, max_length=1000,
    )

    def __init__(self, autor_original_id: int, era_anonima: bool, message: discord.Message):
        super().__init__(timeout=300)
        self.autor_original_id = autor_original_id
        self.era_anonima       = era_anonima
        self.message           = message

    async def on_submit(self, interaction: discord.Interaction):
        cog   = interaction.client.get_cog("Anonimo")
        canal = cog.canais.get(interaction.guild_id) if cog else None
        if canal is None:
            return await interaction.response.send_message("Canal não configurado.", ephemeral=True)
        embed = discord.Embed(
            description=limitar(str(self.resposta.value), 1000),
            color=discord.Color.from_rgb(255, 255, 255),
        )
        embed.set_author(name=f"Resposta de {interaction.user}",
                         icon_url=interaction.user.display_avatar.url)
        try:
            await canal.send(
                content=f"<@{self.autor_original_id}>",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            view = self.message.view if hasattr(self.message, "view") else None
            if isinstance(view, PublicacaoView):
                await view.incrementar_e_atualizar(self.message)
            await interaction.response.send_message("Resposta enviada.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("Falha ao enviar.", ephemeral=True)


class ComentarioModal(discord.ui.Modal, title="Comentar mensagem"):
    comentario = discord.ui.TextInput(
        label="Comentário",
        style=discord.TextStyle.paragraph,
        min_length=1, max_length=1000,
    )

    def __init__(self, message: discord.Message):
        super().__init__(timeout=300)
        self.message = message

    async def on_submit(self, interaction: discord.Interaction):
        cog   = interaction.client.get_cog("Anonimo")
        canal = cog.canais.get(interaction.guild_id) if cog else None
        if canal is None:
            return await interaction.response.send_message("Canal não configurado.", ephemeral=True)
        embed = discord.Embed(
            description=limitar(str(self.comentario.value), 1000),
            color=discord.Color.from_rgb(255, 255, 255),
        )
        embed.set_author(name=f"Comentário de {interaction.user}",
                         icon_url=interaction.user.display_avatar.url)
        try:
            await canal.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            view = self.message.view if hasattr(self.message, "view") else None
            if isinstance(view, PublicacaoView):
                await view.incrementar_e_atualizar(self.message)
            await interaction.response.send_message("Comentário publicado.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("Falha ao publicar.", ephemeral=True)


class Anonimo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot    = bot
        self.canais: dict[int, discord.TextChannel] = {}

    @app_commands.command(name="anonimo_painel",
                          description="Publica o painel de mensagens anônimas neste canal.")
    @app_commands.describe(canal="Canal onde as mensagens serão publicadas.")
    @app_commands.guild_only()
    async def anonimo_painel(self, interaction: discord.Interaction, canal: discord.TextChannel):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(
                "Você precisa da permissão Gerenciar servidor.", ephemeral=True
            )
        if interaction.guild is None or interaction.channel is None:
            return await interaction.response.send_message("Use em um servidor.", ephemeral=True)
        if not canal.permissions_for(interaction.guild.me).send_messages:
            return await interaction.response.send_message(
                "Sem permissão para enviar mensagens no canal.", ephemeral=True
            )
        self.canais[interaction.guild.id] = canal
        banner_file = await baixar_banner()
        try:
            await interaction.channel.send(
                view=criar_painel_view(banner_anexado=banner_file is not None),
                file=banner_file,
            )
        except discord.HTTPException:
            return await interaction.response.send_message("Falha ao publicar painel.", ephemeral=True)
        await interaction.response.send_message(
            f"Painel publicado. Mensagens irão para {canal.mention}.", ephemeral=True
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
            return False, "Servidor não identificado."
        canal = self.canais.get(guild.id)
        if canal is None:
            return False, "Canal não configurado. Execute /anonimo_painel novamente."

        destinatario = guild.get_member(destinatario_id)
        if destinatario is None:
            try:
                destinatario = await guild.fetch_member(destinatario_id)
            except (discord.NotFound, discord.HTTPException):
                return False, "Destinatário não encontrado."

        arquivo = await gerar_cartao_png(autor, destinatario, mensagem, anonima)
        view    = PublicacaoView(destinatario.id, autor.id, anonima)

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
            return False, "Falha ao publicar. Verifique as permissões."
        return True, f"Mensagem enviada para {destinatario.mention}."


async def setup(bot: commands.Bot):
    await bot.add_cog(Anonimo(bot))
