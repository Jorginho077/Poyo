from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ChanelKiller.py — extensão para discord.py 2.x
# Carregamento: await bot.load_extension("ChanelKiller")
# Banco SQLite criado ao lado deste arquivo: chanelkiller.sqlite3
# Prefixo "," depende de commands.Bot(command_prefix=",", ...).
# Para slash commands, sincronize os comandos da árvore do bot no seu código principal.

BANNER = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
EMOJI_CHANNEL = "📺"
EMOJI_TIME = "⏱️"
EMOJI_ACTIVE = "🔁"
EMOJI_STOP = "🛑"
EMOJI_WARN = "⚠️"

DB_PATH = Path(__file__).with_name("chanelkiller.sqlite3")
_DURATION_PART = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)


def parse_duration(value: str) -> int:
    """Converte valores como 3h 1m em segundos."""
    value = value.strip().lower()
    matches = list(_DURATION_PART.finditer(value))
    if not matches or "".join(m.group(0).replace(" ", "") for m in matches) != re.sub(r"\s+", "", value):
        raise ValueError("Use formatos como `30s`, `5m`, `3h 1m` ou `1d`.")
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    seconds = sum(int(m.group(1)) * multipliers[m.group(2).lower()] for m in matches)
    if seconds < 60:
        raise ValueError("O intervalo mínimo é de 1 minuto.")
    if seconds > 365 * 86400:
        raise ValueError("O intervalo máximo é de 365 dias.")
    return seconds


def pretty_duration(seconds: int) -> str:
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        amount, seconds = divmod(seconds, size)
        if amount:
            parts.append(f"{amount}{unit}")
    return " ".join(parts) or "0s"


@dataclass
class KillerConfig:
    owner_id: int
    guild_id: int
    channel_id: Optional[int] = None
    interval: Optional[int] = None
    active: bool = False
    next_run: float = 0.0


class ChannelKillerView(discord.ui.View):
    def __init__(self, cog: "ChannelKiller", config: KillerConfig):
        super().__init__(timeout=900)
        self.cog = cog
        self.config = config

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.config.owner_id:
            await interaction.response.send_message(
                "⛔ Só quem executou o comando pode usar este painel.", ephemeral=True
            )
            return False
        if interaction.guild_id != self.config.guild_id:
            await interaction.response.send_message("Este painel pertence a outro servidor.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Canal", emoji=EMOJI_CHANNEL, style=discord.ButtonStyle.secondary)
    async def choose_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Mencione o canal de texto desejado (ex.: `#geral`). "
            "Envie a menção neste canal em até 60 segundos.",
            ephemeral=True,
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
            await interaction.followup.send("⌛ Tempo esgotado. Clique em **Canal** novamente.", ephemeral=True)
            return

        channel = message.channel_mentions[0] if message.channel_mentions else None
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.config.guild_id:
            await interaction.followup.send("❌ Não encontrei um canal de texto válido na menção.", ephemeral=True)
            return

        self.config.channel_id = channel.id
        await self.cog.save_config(self.config)
        await interaction.followup.send(f"✅ Canal definido: {channel.mention}", ephemeral=True)

    @discord.ui.button(label="Tempo", emoji=EMOJI_TIME, style=discord.ButtonStyle.secondary)
    async def choose_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DurationModal(self.cog, self.config))

    @discord.ui.button(label="Ativar", emoji=EMOJI_ACTIVE, style=discord.ButtonStyle.success)
    async def activate(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.config.channel_id is None:
            await interaction.response.send_message("⚠️ Escolha um canal primeiro.", ephemeral=True)
            return
        if self.config.interval is None:
            await interaction.response.send_message("⚠️ Defina o intervalo primeiro.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(self.config.channel_id) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ O canal configurado não existe mais.", ephemeral=True)
            return
        if not channel.permissions_for(interaction.guild.me).manage_channels:
            await interaction.response.send_message(
                "❌ Preciso da permissão **Gerenciar canais** no canal escolhido.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"{EMOJI_WARN} **Confirme a ativação**\n"
            f"Vou recriar {channel.mention} a cada **{pretty_duration(self.config.interval)}**. "
            "Isso exclui as mensagens antigas; elas não podem ser recuperadas. "
            "O ID do canal muda e links, threads e integrações podem ser afetados.\n\nDeseja continuar?",
            view=ConfirmActivationView(self.cog, self.config),
            ephemeral=True,
        )

    @discord.ui.button(label="Desativar", emoji=EMOJI_STOP, style=discord.ButtonStyle.danger)
    async def deactivate(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.config.active = False
        self.config.next_run = 0
        await self.cog.save_config(self.config)
        await interaction.response.send_message("🛑 Rotina desativada. A configuração foi mantida.", ephemeral=True)

    async def panel_embed(self) -> discord.Embed:
        channel_text = f"<#{self.config.channel_id}>" if self.config.channel_id else "Não selecionado"
        time_text = pretty_duration(self.config.interval) if self.config.interval else "Não definido"
        status = "🟢 Ativo" if self.config.active else "⚪ Inativo"
        embed = discord.Embed(
            title="☠️・CHANNEL KILLER",
            description=(
                f"{BANNER}\nConfigure a recriação periódica de um canal.\n{BANNER}\n\n"
                f"{EMOJI_CHANNEL} **Canal:** {channel_text}\n"
                f"{EMOJI_TIME} **Intervalo:** {time_text}\n"
                f"{EMOJI_ACTIVE} **Status:** {status}\n\n"
                f"{EMOJI_WARN} Mensagens antigas serão apagadas ao recriar o canal."
            ),
            color=discord.Color.dark_red(),
        )
        embed.set_footer(text="ChanelKiller • configuração salva automaticamente")
        return embed


class DurationModal(discord.ui.Modal, title="Definir intervalo"):
    interval = discord.ui.TextInput(
        label="Intervalo", placeholder="Ex.: 3h 1m ou 1d", required=True, max_length=40
    )

    def __init__(self, cog: "ChannelKiller", config: KillerConfig):
        super().__init__()
        self.cog = cog
        self.config = config

    async def on_submit(self, interaction: discord.Interaction):
        try:
            seconds = parse_duration(str(self.interval))
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        self.config.interval = seconds
        await self.cog.save_config(self.config)
        await interaction.response.send_message(
            f"✅ Intervalo definido para **{pretty_duration(seconds)}** e salvo.", ephemeral=True
        )


class ConfirmActivationView(discord.ui.View):
    def __init__(self, cog: "ChannelKiller", config: KillerConfig):
        super().__init__(timeout=60)
        self.cog = cog
        self.config = config

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.config.owner_id:
            await interaction.response.send_message("⛔ Só quem iniciou o painel pode confirmar.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmar ativação", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.config.active = True
        self.config.next_run = time.time() + self.config.interval
        await self.cog.save_config(self.config)
        await interaction.response.send_message(
            f"🔁 Ativado e salvo no SQLite. A primeira recriação ocorrerá em "
            f"**{pretty_duration(self.config.interval)}**; continuará após reiniciar o bot.",
            ephemeral=True,
        )
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Operação cancelada.", ephemeral=True)
        self.stop()


class ChannelKiller(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.configs: dict[tuple[int, int], KillerConfig] = {}
        self.db = sqlite3.connect(DB_PATH)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS channel_killer (
                guild_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                channel_id INTEGER,
                interval_seconds INTEGER,
                active INTEGER NOT NULL DEFAULT 0,
                next_run REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, owner_id)
            )
        """)
        self.db.commit()
        self._load_configs()
        self.worker.start()

    def _load_configs(self):
        rows = self.db.execute(
            "SELECT guild_id, owner_id, channel_id, interval_seconds, active, next_run FROM channel_killer"
        ).fetchall()
        for guild_id, owner_id, channel_id, interval, active, next_run in rows:
            config = KillerConfig(
                owner_id=owner_id, guild_id=guild_id, channel_id=channel_id,
                interval=interval, active=bool(active), next_run=next_run or 0.0
            )
            self.configs[(guild_id, owner_id)] = config

    async def save_config(self, config: KillerConfig):
        self.configs[(config.guild_id, config.owner_id)] = config
        self.db.execute("""
            INSERT INTO channel_killer
                (guild_id, owner_id, channel_id, interval_seconds, active, next_run)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, owner_id) DO UPDATE SET
                channel_id=excluded.channel_id,
                interval_seconds=excluded.interval_seconds,
                active=excluded.active,
                next_run=excluded.next_run
        """, (
            config.guild_id, config.owner_id, config.channel_id,
            config.interval, int(config.active), config.next_run
        ))
        self.db.commit()

    def cog_unload(self):
        self.worker.cancel()
        self.db.close()

    async def open_panel(self, ctx_or_interaction):
        if isinstance(ctx_or_interaction, discord.Interaction):
            interaction = ctx_or_interaction
            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
                return
            key = (guild.id, interaction.user.id)
            config = self.configs.get(key) or KillerConfig(interaction.user.id, guild.id)
            await self.save_config(config)
            view = ChannelKillerView(self, config)
            await interaction.response.send_message(embed=await view.panel_embed(), view=view, ephemeral=True)
        else:
            ctx = ctx_or_interaction
            if ctx.guild is None:
                await ctx.reply("Use este comando em um servidor.", mention_author=False)
                return
            key = (ctx.guild.id, ctx.author.id)
            config = self.configs.get(key) or KillerConfig(ctx.author.id, ctx.guild.id)
            await self.save_config(config)
            view = ChannelKillerView(self, config)
            await ctx.reply(embed=await view.panel_embed(), view=view, mention_author=False)

    @app_commands.command(name="channelkiller", description="Abre o painel de configuração do Channel Killer.")
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
                me = guild.me
                if me is None or not me.guild_permissions.manage_channels:
                    config.active = False
                    await self.save_config(config)
                    continue

                # Clona antes de apagar: categoria, overwrites/permissões, tópico,
                # nome, slowmode e outras propriedades suportadas pelo Discord são copiados.
                clone = await channel.clone(
                    reason="ChanelKiller: recriação periódica configurada pelo administrador"
                )
                await clone.edit(
                    position=channel.position,
                    category=channel.category,
                    reason="ChanelKiller: preservar categoria e posição",
                )

                # Atualiza e salva o novo ID antes da exclusão para reduzir risco de
                # perder a referência caso o processo caia entre as duas operações.
                old_id = channel.id
                config.channel_id = clone.id
                config.next_run = time.time() + config.interval
                await self.save_config(config)

                try:
                    await channel.delete(reason="ChanelKiller: remover canal antigo e suas mensagens")
                except Exception:
                    # Se não conseguir apagar o antigo, para a rotina para evitar duplicatas.
                    config.active = False
                    await self.save_config(config)
                    raise

            except (discord.Forbidden, discord.HTTPException):
                config.active = False
                await self.save_config(config)

    @worker.before_loop
    async def before_worker(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelKiller(bot))
