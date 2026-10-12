from __future__ import annotations

import asyncio
import io
import re
import sqlite3
import time
from pathlib import Path
from urllib.request import Request, urlopen

import discord
from discord import app_commands
from discord.ext import commands, tasks

DB_PATH = Path(__file__).with_name("chanelkiller.sqlite3")
DURATION_RE = re.compile(r"(\d+)\s*([smhd])", re.I)
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

EMOJI_INICIO = "<:axolote:1556443018557661234>"
EMOJI_FINAL = "<a:emoji_481:1556442987691647068>"
MOD_BANNER_URL = (
    "https://raw.githubusercontent.com/"
    "Jorginho077/Poyo/main/assets/Tumblr-l-67143811701311.gif"
)
MOD_BANNER_ATTACHMENT = "attachment://poyo-mod-banner.gif"


def _baixar_banner_sync() -> bytes:
    pedido = Request(MOD_BANNER_URL, headers={"User-Agent": "Poyo-Discord-Bot/1.0"})
    with urlopen(pedido, timeout=20) as resposta:
        return resposta.read()


async def baixar_banner() -> discord.File | None:
    try:
        dados = await asyncio.to_thread(_baixar_banner_sync)
        return discord.File(io.BytesIO(dados), filename="poyo-mod-banner.gif")
    except (OSError, TimeoutError, ValueError):
        return None


def parse_duration(value: str) -> int:
    value = value.strip().lower()
    parts = list(DURATION_RE.finditer(value))
    if not parts or "".join(p.group(0).replace(" ", "") for p in parts) != re.sub(r"\s+", "", value):
        raise ValueError("Use formatos como 3h 1m, 30m ou 1d.")
    seconds = sum(int(p.group(1)) * UNITS[p.group(2).lower()] for p in parts)
    if not 60 <= seconds <= 365 * 86400:
        raise ValueError("O intervalo deve ser entre 1 minuto e 365 dias.")
    return seconds


def format_duration(seconds: int) -> str:
    result = []
    for suffix, unit in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        amount, seconds = divmod(seconds, unit)
        if amount:
            result.append(f"{amount}{suffix}")
    return " ".join(result) or "0s"


def make_container(
    title: str,
    description: str,
    buttons: list[discord.ui.Button] | None = None,
    timeout: float | None = 900,
    banner_url: str | None = None,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    container = discord.ui.Container()
    if banner_url:
        container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(banner_url)))
    container.add_item(
        discord.ui.TextDisplay(f"{EMOJI_INICIO}  **{title}**  {EMOJI_FINAL}\n\n{description}")
    )
    if buttons:
        container.add_item(discord.ui.ActionRow(*buttons))
    if banner_url:
        container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(banner_url)))
    view.add_item(container)
    return view


class ChannelKillerConfig:
    def __init__(self, owner_id: int, guild_id: int, channel_id=None,
                 interval=None, active=False, next_run=0.0):
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.interval = interval
        self.active = active
        self.next_run = next_run


class ChannelKillerPanel(discord.ui.LayoutView):
    def __init__(self, cog: "ChannelKiller", config: ChannelKillerConfig, banner_url: str | None = MOD_BANNER_ATTACHMENT):
        super().__init__(timeout=900)
        self.cog = cog
        self.config = config
        self.banner_url = banner_url

        canal = discord.ui.Button(label="Canal", style=discord.ButtonStyle.secondary)
        tempo = discord.ui.Button(label="Tempo", style=discord.ButtonStyle.secondary)
        ativar = discord.ui.Button(label="Ativar", style=discord.ButtonStyle.success)
        desativar = discord.ui.Button(label="Desativar", style=discord.ButtonStyle.danger)
        canal.callback = self.set_channel
        tempo.callback = self.set_time
        ativar.callback = self.activate
        desativar.callback = self.deactivate

        container = discord.ui.Container()
        if self.banner_url:
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(self.banner_url)))
        container.add_item(discord.ui.TextDisplay(self.panel_text()))
        container.add_item(discord.ui.ActionRow(canal, tempo, ativar, desativar))
        if self.banner_url:
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(self.banner_url)))
        self.add_item(container)

    def panel_text(self) -> str:
        channel = f"<#{self.config.channel_id}>" if self.config.channel_id else "Não selecionado"
        interval = format_duration(self.config.interval) if self.config.interval else "Não definido"
        status = "Ativo" if self.config.active else "Inativo"
        return (
            f"{EMOJI_INICIO}  **CHANNEL KILLER**  {EMOJI_FINAL}\n\n"
            f"Canal: {channel}\n"
            f"Intervalo: {interval}\n"
            f"Status: {status}\n\n"
            "A recriação apaga as mensagens antigas e altera o ID do canal. "
            "Links, threads e integrações podem ser afetados."
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.config.owner_id:
            await interaction.response.send_message(
                "Somente quem abriu este painel pode usá-lo.", ephemeral=True
            )
            return False
        if interaction.guild_id != self.config.guild_id:
            await interaction.response.send_message(
                "Este painel pertence a outro servidor.", ephemeral=True
            )
            return False
        return True

    async def set_channel(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Mencione o canal de texto desejado no chat em até 60 segundos.", ephemeral=True
        )

        def check(message: discord.Message) -> bool:
            return (
                message.author.id == self.config.owner_id
                and message.guild is not None
                and message.guild.id == self.config.guild_id
                and message.channel.id == interaction.channel_id
            )

        try:
            message = await self.cog.bot.wait_for("message", check=check, timeout=60)
        except asyncio.TimeoutError:
            await interaction.followup.send("Tempo esgotado. Clique em Canal novamente.", ephemeral=True)
            return

        channel = message.channel_mentions[0] if message.channel_mentions else None
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass

        if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.config.guild_id:
            await interaction.followup.send("Mencione um canal de texto válido.", ephemeral=True)
            return

        self.config.channel_id = channel.id
        await self.cog.save_config(self.config)
        await self.cog.atualizar_painel_mensagem(interaction.message, self.config)
        await interaction.followup.send(f"Canal definido: {channel.mention}.", ephemeral=True)

    async def set_time(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ChannelKillerDurationModal(self.cog, self.config, interaction.message))

    async def activate(self, interaction: discord.Interaction) -> None:
        if not self.config.channel_id:
            await interaction.response.send_message("Escolha um canal primeiro.", ephemeral=True)
            return
        if not self.config.interval:
            await interaction.response.send_message("Defina o intervalo primeiro.", ephemeral=True)
            return

        guild = interaction.guild
        channel = guild.get_channel(self.config.channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("O canal configurado não existe mais.", ephemeral=True)
            return
        if guild.me is None or not channel.permissions_for(guild.me).manage_channels:
            await interaction.response.send_message("Preciso da permissão **Gerenciar canais**.", ephemeral=True)
            return

        confirm_view = ChannelKillerConfirmView(self.cog, self.config, interaction.message)
        await interaction.response.send_message(
            f"Confirme para recriar {channel.mention} a cada **{format_duration(self.config.interval)}**. "
            "As mensagens antigas serão perdidas e o ID do canal mudará.",
            view=confirm_view,
            ephemeral=True,
        )

    async def deactivate(self, interaction: discord.Interaction) -> None:
        self.config.active = False
        self.config.next_run = 0
        await self.cog.save_config(self.config)
        await self.cog.atualizar_painel_mensagem(interaction.message, self.config)
        await interaction.response.send_message("Rotina desativada. A configuração foi mantida.", ephemeral=True)


class ChannelKillerDurationModal(discord.ui.Modal, title="Definir intervalo"):
    interval = discord.ui.TextInput(
        label="Intervalo", placeholder="Ex.: 3h 1m ou 1d", required=True, max_length=40
    )

    def __init__(self, cog: "ChannelKiller", config: ChannelKillerConfig, panel_message=None):
        super().__init__()
        self.cog = cog
        self.config = config
        self.panel_message = panel_message

    async def on_submit(self, interaction: discord.Interaction):
        try:
            seconds = parse_duration(str(self.interval))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.config.interval = seconds
        await self.cog.save_config(self.config)
        await self.cog.atualizar_painel_mensagem(self.panel_message, self.config)
        await interaction.response.send_message(f"Intervalo definido: **{format_duration(seconds)}**.", ephemeral=True)


class ChannelKillerConfirmView(discord.ui.View):
    def __init__(self, cog: "ChannelKiller", config: ChannelKillerConfig, panel_message=None):
        super().__init__(timeout=60)
        self.cog = cog
        self.config = config
        self.panel_message = panel_message
        self.confirm_button = discord.ui.Button(label="Confirmar", style=discord.ButtonStyle.danger)
        self.cancel_button = discord.ui.Button(label="Cancelar", style=discord.ButtonStyle.secondary)
        self.confirm_button.callback = self.confirm
        self.cancel_button.callback = self.cancel
        self.add_item(self.confirm_button)
        self.add_item(self.cancel_button)

    async def confirm(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.config.owner_id:
            await interaction.response.send_message("Somente quem abriu o painel pode confirmar.", ephemeral=True)
            return
        self.config.active = True
        self.config.next_run = time.time() + self.config.interval
        await self.cog.save_config(self.config)
        await interaction.response.send_message(
            f"Channel Killer ativado. A primeira recriação ocorrerá em **{format_duration(self.config.interval)}**.",
            ephemeral=True,
        )
        await self.cog.atualizar_painel_mensagem(self.panel_message, self.config)

    async def cancel(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.config.owner_id:
            await interaction.response.send_message("Somente quem abriu o painel pode cancelar.", ephemeral=True)
            return
        await interaction.response.send_message("Operação cancelada. A rotina não foi ativada.", ephemeral=True)


class ChannelKiller(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.configs: dict[tuple[int, int], ChannelKillerConfig] = {}
        self.db = sqlite3.connect(DB_PATH)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS channel_killer (
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                channel_id INTEGER,
                interval_seconds INTEGER,
                active INTEGER NOT NULL DEFAULT 0,
                next_run REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, owner_id)
            )"""
        )
        self.db.commit()
        self.load_configs()
        self.worker.start()

    def load_configs(self):
        rows = self.db.execute(
            "SELECT guild_id, owner_id, channel_id, interval_seconds, active, next_run FROM channel_killer"
        ).fetchall()
        for guild_id, owner_id, channel_id, interval, active, next_run in rows:
            self.configs[(guild_id, owner_id)] = ChannelKillerConfig(
                owner_id, guild_id, channel_id, interval, bool(active), next_run or 0
            )

    async def save_config(self, config: ChannelKillerConfig):
        self.configs[(config.guild_id, config.owner_id)] = config
        self.db.execute(
            """INSERT INTO channel_killer
            (guild_id, owner_id, channel_id, interval_seconds, active, next_run)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, owner_id) DO UPDATE SET
                channel_id=excluded.channel_id,
                interval_seconds=excluded.interval_seconds,
                active=excluded.active,
                next_run=excluded.next_run""",
            (config.guild_id, config.owner_id, config.channel_id,
             config.interval, int(config.active), config.next_run),
        )
        self.db.commit()

    def cog_unload(self):
        self.worker.cancel()
        self.db.close()

    async def atualizar_painel_mensagem(self, mensagem, config: ChannelKillerConfig) -> None:
        if mensagem is None:
            return
        arquivo = await baixar_banner()
        try:
            view = ChannelKillerPanel(self, config, MOD_BANNER_ATTACHMENT if arquivo is not None else None)
            if arquivo is not None:
                await mensagem.edit(view=view, attachments=[arquivo])
            else:
                await mensagem.edit(view=view, attachments=[])
        except (discord.HTTPException, discord.NotFound):
            pass

    async def open_panel(self, target):
        if isinstance(target, discord.Interaction):
            guild, user = target.guild, target.user
        else:
            guild, user = target.guild, target.author

        if guild is None:
            message = "Use este comando em um servidor."
            if isinstance(target, discord.Interaction):
                arquivo = await baixar_banner()
                view = make_container("Servidor necessário", message, banner_url=MOD_BANNER_ATTACHMENT if arquivo else None)
                await target.response.send_message(view=view, file=arquivo, ephemeral=True)
            else:
                arquivo = await baixar_banner()
                view = make_container("Servidor necessário", message, banner_url=MOD_BANNER_ATTACHMENT if arquivo else None)
                await target.reply(view=view, file=arquivo, mention_author=False)
            return

        key = (guild.id, user.id)
        config = self.configs.get(key) or ChannelKillerConfig(user.id, guild.id)
        await self.save_config(config)
        arquivo = await baixar_banner()
        view = ChannelKillerPanel(self, config, MOD_BANNER_ATTACHMENT if arquivo is not None else None)
        if isinstance(target, discord.Interaction):
            await target.response.send_message(view=view, file=arquivo, ephemeral=True)
        else:
            await target.reply(view=view, file=arquivo, mention_author=False)

    @app_commands.command(name="channelkiller", description="Abre o painel do Channel Killer.")
    async def channelkiller_slash(self, interaction: discord.Interaction):
        await self.open_panel(interaction)

    @commands.command(name="channelkiller")
    @commands.guild_only()
    async def channelkiller_prefix(self, ctx: commands.Context):
        await self.open_panel(ctx)

    @tasks.loop(seconds=15)
    async def worker(self):
        now = time.time()
        for config in list(self.configs.values()):
            if not config.active or not config.interval or config.next_run > now:
                continue

            guild = self.bot.get_guild(config.guild_id)
            channel = guild.get_channel(config.channel_id) if guild and config.channel_id else None
            if not isinstance(channel, discord.TextChannel):
                config.active = False
                await self.save_config(config)
                continue

            try:
                if guild.me is None or not guild.me.guild_permissions.manage_channels:
                    config.active = False
                    await self.save_config(config)
                    continue

                replacement = await channel.clone(reason="ChannelKiller: recriação periódica configurada")
                await replacement.edit(
                    category=channel.category,
                    position=channel.position,
                    reason="ChannelKiller: preservar categoria e posição",
                )
                config.channel_id = replacement.id
                config.next_run = time.time() + config.interval
                await self.save_config(config)
                await channel.delete(reason="ChannelKiller: remover canal antigo e mensagens")
            except (discord.Forbidden, discord.HTTPException):
                config.active = False
                await self.save_config(config)

    @worker.before_loop
    async def before_worker(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelKiller(bot))
