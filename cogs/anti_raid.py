"""Proteção anti-raid configurável por servidor.

O módulo é carregado automaticamente por ``bot.py`` porque não começa por
``_``. A proteção é deliberadamente conservadora: somente membros humanos
identificados nos dois sinais (mensagens reais com menção everyone e criação
de canais atribuída pelo Audit Log) podem receber timeout temporário.

Nenhum cargo, overwrite, canal ou permissão é removido/modificado por este
cog. A única ação automática de moderação é ``Member.edit`` com
``timed_out_until``.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks


# --- Acesso do dono do bot -------------------------------------------------
# O dono (ID abaixo) passa em todas as checagens de permissão de usuário
# destes scripts. Para incluir mais donos sem editar o código, defina a
# variável de ambiente POYO_OWNER_IDS="id1,id2". Isso NÃO dá ao Poyo
# permissões que o Discord não concedeu ao cargo dele no servidor.
def _load_owner_ids() -> frozenset[int]:
    ids = {1551958902256570409}
    for part in os.getenv("POYO_OWNER_IDS", "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return frozenset(ids)


OWNER_IDS = _load_owner_ids()


def is_bot_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


def _is_alert_capable(channel: Any) -> bool:
    """Canais onde o alerta pode ser entregue (qualquer um com send, ou fórum)."""
    return isinstance(channel, discord.ForumChannel) or hasattr(channel, "send")


async def _deliver(channel: Any, content: str, allowed_mentions=None):
    """Envia o texto em qualquer tipo de canal; em fórum, abre um post."""
    allowed_mentions = allowed_mentions or discord.AllowedMentions.none()
    if isinstance(channel, discord.ForumChannel):
        created = await channel.create_thread(
            name="🚨 Alerta anti-raid",
            content=content,
            allowed_mentions=allowed_mentions,
        )
        return created.message
    return await channel.send(content, allowed_mentions=allowed_mentions)


# ---------------------------------------------------------------------------
# Configuração e persistência

DEFAULT_THRESHOLD = 3
DEFAULT_WINDOW_SECONDS = 10
DEFAULT_QUARANTINE_SECONDS = 10 * 60
COOLDOWN_SECONDS = 30
MAX_QUARANTINE_SECONDS = 28 * 24 * 60 * 60

DB_PATH = Path(
    os.getenv(
        "ANTIRAID_DB_FILE",
        Path(__file__).resolve().parent.parent / "antiraid.sqlite3",
    )
)

_DISCORD_ERRORS = (
    discord.NotFound,
    discord.Forbidden,
    discord.HTTPException,
    discord.InvalidData,
)


@dataclass(frozen=True, slots=True)
class AntiRaidConfig:
    guild_id: int
    enabled: bool = False
    alert_channel_id: Optional[int] = None
    threshold: int = DEFAULT_THRESHOLD
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    quarantine_seconds: int = DEFAULT_QUARANTINE_SECONDS


@dataclass(slots=True)
class MentionEvent:
    created_at: float
    message_id: int
    author_id: int
    channel_id: int


@dataclass(slots=True)
class GuildEvents:
    channels: deque[tuple[float, int]] = field(default_factory=deque)
    everyone_messages: deque[MentionEvent] = field(default_factory=deque)


def _db_connection() -> sqlite3.Connection:
    """Abre uma conexão curta; cada operação fecha a própria conexão."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_database_sync() -> None:
    with _db_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS antiraid_config (
                guild_id            INTEGER PRIMARY KEY,
                enabled             INTEGER NOT NULL DEFAULT 0,
                alert_channel_id    INTEGER,
                threshold           INTEGER NOT NULL DEFAULT 3,
                window_seconds      INTEGER NOT NULL DEFAULT 10,
                quarantine_seconds  INTEGER NOT NULL DEFAULT 600,
                updated_at          REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS antiraid_timeouts (
                guild_id       INTEGER NOT NULL,
                member_id      INTEGER NOT NULL,
                original_until REAL,
                bot_until      REAL NOT NULL,
                created_at     REAL NOT NULL,
                PRIMARY KEY (guild_id, member_id)
            );
            """
        )


def _config_from_row(row: sqlite3.Row) -> AntiRaidConfig:
    return AntiRaidConfig(
        guild_id=int(row["guild_id"]),
        enabled=bool(row["enabled"]),
        alert_channel_id=(
            int(row["alert_channel_id"])
            if row["alert_channel_id"] is not None
            else None
        ),
        threshold=int(row["threshold"]),
        window_seconds=int(row["window_seconds"]),
        quarantine_seconds=int(row["quarantine_seconds"]),
    )


def _get_config_sync(guild_id: int) -> AntiRaidConfig:
    agora = time.time()
    with _db_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO antiraid_config
                (guild_id, enabled, alert_channel_id, threshold,
                 window_seconds, quarantine_seconds, updated_at)
            VALUES (?, 0, NULL, ?, ?, ?, ?)
            """,
            (
                guild_id,
                DEFAULT_THRESHOLD,
                DEFAULT_WINDOW_SECONDS,
                DEFAULT_QUARANTINE_SECONDS,
                agora,
            ),
        )
        row = connection.execute(
            "SELECT * FROM antiraid_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()

    if row is None:  # pragma: no cover - protegido pelo INSERT acima
        return AntiRaidConfig(guild_id=guild_id)
    return _config_from_row(row)


def _save_config_sync(config: AntiRaidConfig) -> None:
    with _db_connection() as connection:
        connection.execute(
            """
            INSERT INTO antiraid_config
                (guild_id, enabled, alert_channel_id, threshold,
                 window_seconds, quarantine_seconds, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                enabled = excluded.enabled,
                alert_channel_id = excluded.alert_channel_id,
                threshold = excluded.threshold,
                window_seconds = excluded.window_seconds,
                quarantine_seconds = excluded.quarantine_seconds,
                updated_at = excluded.updated_at
            """,
            (
                config.guild_id,
                int(config.enabled),
                config.alert_channel_id,
                config.threshold,
                config.window_seconds,
                config.quarantine_seconds,
                time.time(),
            ),
        )


def _get_timeout_sync(guild_id: int, member_id: int) -> Optional[sqlite3.Row]:
    with _db_connection() as connection:
        return connection.execute(
            """
            SELECT * FROM antiraid_timeouts
            WHERE guild_id = ? AND member_id = ?
            """,
            (guild_id, member_id),
        ).fetchone()


def _save_timeout_sync(
    guild_id: int,
    member_id: int,
    original_until: Optional[float],
    bot_until: float,
) -> None:
    with _db_connection() as connection:
        connection.execute(
            """
            INSERT INTO antiraid_timeouts
                (guild_id, member_id, original_until, bot_until, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, member_id) DO UPDATE SET
                original_until = excluded.original_until,
                bot_until = excluded.bot_until,
                created_at = excluded.created_at
            """,
            (guild_id, member_id, original_until, bot_until, time.time()),
        )


def _delete_timeout_sync(guild_id: int, member_id: int) -> None:
    with _db_connection() as connection:
        connection.execute(
            "DELETE FROM antiraid_timeouts WHERE guild_id = ? AND member_id = ?",
            (guild_id, member_id),
        )


def _expired_timeouts_sync(now: float) -> list[sqlite3.Row]:
    with _db_connection() as connection:
        return connection.execute(
            "SELECT * FROM antiraid_timeouts WHERE bot_until <= ?",
            (now,),
        ).fetchall()


# ---------------------------------------------------------------------------
# Utilitários seguros


def _timestamp(value: Optional[datetime]) -> Optional[float]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _datetime_from_timestamp(value: Optional[float]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}min" if not remainder else f"{minutes}min {remainder}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h" if not minutes else f"{hours}h {minutes}min"
    days, hours = divmod(hours, 24)
    return f"{days}d" if not hours else f"{days}d {hours}h"


def _real_everyone_mention(message: discord.Message) -> bool:
    """Aceita somente uma menção processada pelo Discord, não texto literal."""
    content = message.content or ""
    return bool(message.mention_everyone and "@everyone" in content)


# ---------------------------------------------------------------------------
# Painel interativo


class AntiRaidConfigModal(discord.ui.Modal, title="Configurar anti-raid"):
    limiar = discord.ui.TextInput(
        label="Limiar dos dois sinais",
        placeholder="3",
        min_length=1,
        max_length=2,
        required=True,
    )
    janela = discord.ui.TextInput(
        label="Janela em segundos",
        placeholder="10",
        min_length=1,
        max_length=3,
        required=True,
    )
    quarentena = discord.ui.TextInput(
        label="Quarentena em segundos",
        placeholder="600",
        min_length=1,
        max_length=7,
        required=True,
    )

    def __init__(self, panel: "AntiRaidPanelView", config: AntiRaidConfig):
        super().__init__(timeout=180)
        self.panel = panel
        self.limiar.default = str(config.threshold)
        self.janela.default = str(config.window_seconds)
        self.quarentena.default = str(config.quarantine_seconds)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            threshold = int(str(self.limiar.value).strip())
            window = int(str(self.janela.value).strip())
            quarantine = int(str(self.quarentena.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "Use apenas números inteiros.", ephemeral=True
            )
            return

        if not 1 <= threshold <= 50:
            await interaction.response.send_message(
                "O limiar deve ficar entre 1 e 50.", ephemeral=True
            )
            return
        if not 5 <= window <= 300:
            await interaction.response.send_message(
                "A janela deve ficar entre 5 e 300 segundos.", ephemeral=True
            )
            return
        if not 5 <= quarantine <= MAX_QUARANTINE_SECONDS:
            await interaction.response.send_message(
                "A quarentena deve ficar entre 5 segundos e 28 dias.",
                ephemeral=True,
            )
            return

        config = await self.panel.cog.get_config(self.panel.guild_id)
        config = replace(
            config,
            threshold=threshold,
            window_seconds=window,
            quarantine_seconds=quarantine,
        )
        await self.panel.cog.save_config(config)

        # A confirmação é privada; a edição do painel é best effort.
        await interaction.response.send_message(
            "Configuração salva.", ephemeral=True
        )
        await self.panel.edit_original_panel()


class AntiRaidPanelView(discord.ui.View):
    """Painel privado do administrador que abriu /antiraid painel."""

    def __init__(
        self,
        cog: "AntiRaid",
        guild_id: int,
        author_id: int,
    ) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.guild_id = guild_id
        self.author_id = author_id
        self.panel_message: Optional[discord.Message] = None

        self.toggle_button = discord.ui.Button(
            label="Ativar",
            style=discord.ButtonStyle.success,
            custom_id="antiraid:toggle",
        )
        self.configure_button = discord.ui.Button(
            label="Ajustar números",
            style=discord.ButtonStyle.primary,
            custom_id="antiraid:config",
        )
        self.refresh_button = discord.ui.Button(
            label="Atualizar",
            style=discord.ButtonStyle.secondary,
            custom_id="antiraid:refresh",
        )
        self.channel_select = discord.ui.ChannelSelect(
            placeholder="Escolha o canal de alerta",
            min_values=1,
            max_values=1,
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.news,
                discord.ChannelType.voice,
                discord.ChannelType.stage_voice,
                discord.ChannelType.forum,
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
                discord.ChannelType.news_thread,
            ],
            custom_id="antiraid:channel",
        )

        self.toggle_button.callback = self.toggle
        self.configure_button.callback = self.configure
        self.refresh_button.callback = self.refresh
        self.channel_select.callback = self.select_channel

        self.add_item(self.channel_select)
        self.add_item(self.toggle_button)
        self.add_item(self.configure_button)
        self.add_item(self.refresh_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild is None
            or interaction.guild.id != self.guild_id
            or (
                interaction.user.id != self.author_id
                and not is_bot_owner(interaction.user.id)
            )
        ):
            await interaction.response.send_message(
                "Este painel pertence a outro administrador.", ephemeral=True
            )
            return False
        return True

    async def _remember_message(self, interaction: discord.Interaction) -> None:
        if interaction.message is not None:
            self.panel_message = interaction.message

    async def edit_original_panel(self) -> None:
        if self.panel_message is None:
            return
        try:
            await self.panel_message.edit(
                content=await self.render(),
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except _DISCORD_ERRORS:
            pass

    async def render(self) -> str:
        config = await self.cog.get_config(self.guild_id)
        status = "ATIVADO" if config.enabled else "desativado"
        canal = (
            f"<#{config.alert_channel_id}>"
            if config.alert_channel_id is not None
            else "não escolhido"
        )
        self.toggle_button.label = "Desativar" if config.enabled else "Ativar"
        self.toggle_button.style = (
            discord.ButtonStyle.danger
            if config.enabled
            else discord.ButtonStyle.success
        )
        return (
            "## Anti-raid\n"
            f"Status: **{status}**\n"
            f"Canal de alerta: {canal}\n"
            f"Limiar: **{config.threshold}** canais criados **e** "
            f"**{config.threshold}** mensagens com menção real everyone\n"
            f"Janela: **{config.window_seconds}s**\n"
            f"Quarentena: **{_format_duration(config.quarantine_seconds)}**\n\n"
            "A resposta espera 5 segundos, usa somente timeout temporário "
            "e ignora owner, administradores, bots e cargos acima/iguais ao Poyo."
        )

    async def select_channel(self, interaction: discord.Interaction) -> None:
        await self._remember_message(interaction)
        selected = self.channel_select.values[0]
        guild = interaction.guild
        if guild is None or getattr(selected, "guild_id", self.guild_id) != self.guild_id:
            await interaction.response.send_message(
                "Escolha um canal deste servidor.", ephemeral=True
            )
            return
        # ChannelSelect devolve um objeto parcial (AppCommandChannel), que
        # NÃO tem .send - era isso que gerava o erro "Escolha um canal de
        # texto ou anúncios" mesmo em canal de texto. Resolvemos o canal real.
        channel = guild.get_channel_or_thread(selected.id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(selected.id)
            except _DISCORD_ERRORS:
                channel = None
        if channel is None or not _is_alert_capable(channel):
            await interaction.response.send_message(
                "Não consegui acessar esse canal. Confira se o Poyo consegue vê-lo.",
                ephemeral=True,
            )
            return

        config = await self.cog.get_config(self.guild_id)
        config = replace(config, alert_channel_id=channel.id)
        await self.cog.save_config(config)

        permissao = self.cog.alert_permissions(interaction.guild, channel)
        aviso = " Canal salvo."
        if not permissao:
            aviso = (
                " Canal salvo, mas o Poyo precisa de Ver canal e Enviar mensagens "
                "para conseguir alertar aqui."
            )
        await interaction.response.edit_message(
            content=await self.render(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if not permissao:
            # O painel já foi editado; o aviso fica no follow-up privado.
            try:
                await interaction.followup.send(aviso, ephemeral=True)
            except _DISCORD_ERRORS:
                pass

    async def toggle(self, interaction: discord.Interaction) -> None:
        await self._remember_message(interaction)
        config = await self.cog.get_config(self.guild_id)
        if not config.enabled and config.alert_channel_id is None:
            await interaction.response.send_message(
                "Escolha primeiro um canal de alerta.", ephemeral=True
            )
            return

        config = replace(config, enabled=not config.enabled)
        await self.cog.save_config(config)
        await interaction.response.edit_message(
            content=await self.render(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def configure(self, interaction: discord.Interaction) -> None:
        await self._remember_message(interaction)
        config = await self.cog.get_config(self.guild_id)
        await interaction.response.send_modal(AntiRaidConfigModal(self, config))

    async def refresh(self, interaction: discord.Interaction) -> None:
        await self._remember_message(interaction)
        await interaction.response.edit_message(
            content=await self.render(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )


# ---------------------------------------------------------------------------
# Cog


class AntiRaid(commands.Cog):
    """Detecção por servidor e comandos slash do anti-raid."""

    antiraid = app_commands.Group(
        name="antiraid",
        description="Configura a proteção contra raids",
    )
    ppentest = app_commands.Group(
        name="ppentest",
        description="Ferramentas de simulação sem punição",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._configs: dict[int, AntiRaidConfig] = {}
        self._events: dict[int, GuildEvents] = {}
        self._event_locks: dict[int, asyncio.Lock] = {}
        self._trigger_tasks: dict[int, asyncio.Task[None]] = {}
        self._cooldowns: dict[int, float] = {}
        self._timeout_lock = asyncio.Lock()
        self.restaurar_timeouts.start()

    def cog_unload(self) -> None:
        self.restaurar_timeouts.cancel()
        for task in tuple(self._trigger_tasks.values()):
            task.cancel()
        self._trigger_tasks.clear()

    # ------------------------------- configuração

    def _lock_for(self, guild_id: int) -> asyncio.Lock:
        return self._event_locks.setdefault(guild_id, asyncio.Lock())

    async def get_config(self, guild_id: int) -> AntiRaidConfig:
        config = self._configs.get(guild_id)
        if config is not None:
            return config
        config = await asyncio.to_thread(_get_config_sync, guild_id)
        self._configs[guild_id] = config
        return config

    async def save_config(self, config: AntiRaidConfig) -> None:
        await asyncio.to_thread(_save_config_sync, config)
        self._configs[config.guild_id] = config

    def alert_permissions(self, guild: discord.Guild, channel: Any) -> bool:
        me = guild.me
        if me is None or not hasattr(channel, "permissions_for"):
            return False
        try:
            permissions = channel.permissions_for(me)
            if not permissions.view_channel:
                return False
            if isinstance(channel, discord.ForumChannel):
                return bool(permissions.create_public_threads)
            if isinstance(channel, discord.Thread):
                return bool(permissions.send_messages_in_threads)
            return bool(permissions.send_messages)
        except (AttributeError, TypeError):
            return False

    def _alert_channel(
        self, guild: discord.Guild, config: AntiRaidConfig
    ) -> Optional[Any]:
        if config.alert_channel_id is None:
            return None
        channel = guild.get_channel_or_thread(config.alert_channel_id)
        if channel is None or not _is_alert_capable(channel):
            return None
        return channel

    # ------------------------------- painel e simulação

    @antiraid.command(
        name="painel",
        description="Abre o painel de ativação e configuração do anti-raid.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def painel(self, interaction: discord.Interaction) -> None:
        if not self._can_configure(interaction):
            await interaction.response.send_message(
                "Você precisa de Gerenciar servidor ou Administrador.",
                ephemeral=True,
            )
            return

        view = AntiRaidPanelView(self, interaction.guild.id, interaction.user.id)
        await interaction.response.send_message(
            content=await view.render(),
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @ppentest.command(
        name="raidcanal",
        description="Simula contagens no canal de alerta, sem punir ninguém.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def raidcanal(self, interaction: discord.Interaction) -> None:
        if not self._can_configure(interaction):
            await interaction.response.send_message(
                "Você precisa de Gerenciar servidor ou Administrador.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        config = await self.get_config(guild.id)
        channel = self._alert_channel(guild, config)
        if channel is None:
            await interaction.followup.send(
                "Escolha um canal no `/antiraid painel` antes da simulação.",
                ephemeral=True,
            )
            return
        if not self.alert_permissions(guild, channel):
            await interaction.followup.send(
                "Não consigo ver/enviar mensagens no canal escolhido.",
                ephemeral=True,
            )
            return

        total = config.threshold
        try:
            simulation = await _deliver(
                channel,
                (
                    "🧪 **Simulação anti-raid**\n"
                    f"Canais criados: **0/{total}** | "
                    f"Mensagens everyone: **0/{total}**\n"
                    "Nenhuma punição ou mudança de permissão será executada."
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except _DISCORD_ERRORS:
            await interaction.followup.send(
                "Não consegui publicar a simulação no canal escolhido.",
                ephemeral=True,
            )
            return

        for count in range(1, total + 1):
            await asyncio.sleep(0.8)
            try:
                await simulation.edit(
                    content=(
                        "🧪 **Simulação anti-raid**\n"
                        f"Canais criados: **{count}/{total}** | "
                        f"Mensagens everyone: **{count}/{total}**\n"
                        "Nenhuma punição ou mudança de permissão será executada."
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except _DISCORD_ERRORS:
                break

        try:
            await interaction.followup.send(
                f"Simulação concluída em {channel.mention}; nada foi punido ou alterado.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except _DISCORD_ERRORS:
            pass

    @staticmethod
    def _can_configure(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return False
        permissions = getattr(interaction.user, "guild_permissions", None)
        if is_bot_owner(interaction.user.id):
            return True
        return bool(
            permissions
            and (permissions.administrator or permissions.manage_guild)
        )

    # ------------------------------- eventos e detecção

    def _prune_events(self, state: GuildEvents, now: float, window: int) -> None:
        cutoff = now - window
        while state.channels and state.channels[0][0] < cutoff:
            state.channels.popleft()
        while (
            state.everyone_messages
            and state.everyone_messages[0].created_at < cutoff
        ):
            state.everyone_messages.popleft()

    async def _record_channel(self, guild: discord.Guild, channel_id: int) -> None:
        config = await self.get_config(guild.id)
        if not config.enabled:
            return

        lock = self._lock_for(guild.id)
        async with lock:
            now = time.time()
            state = self._events.setdefault(guild.id, GuildEvents())
            self._prune_events(state, now, config.window_seconds)
            state.channels.append((now, channel_id))
            await self._maybe_schedule_trigger(guild, config, now, state)

    async def _record_everyone_message(self, message: discord.Message) -> None:
        guild = message.guild
        if guild is None or message.author.bot:
            return
        if not _real_everyone_mention(message):
            return

        config = await self.get_config(guild.id)
        if not config.enabled:
            return

        lock = self._lock_for(guild.id)
        async with lock:
            now = time.time()
            state = self._events.setdefault(guild.id, GuildEvents())
            self._prune_events(state, now, config.window_seconds)
            if any(
                event.message_id == message.id
                for event in state.everyone_messages
            ):
                return
            state.everyone_messages.append(
                MentionEvent(
                    created_at=now,
                    message_id=message.id,
                    author_id=message.author.id,
                    channel_id=message.channel.id,
                )
            )
            await self._maybe_schedule_trigger(guild, config, now, state)

    async def _maybe_schedule_trigger(
        self,
        guild: discord.Guild,
        config: AntiRaidConfig,
        now: float,
        state: GuildEvents,
    ) -> None:
        if len(state.channels) < config.threshold:
            return
        if len(state.everyone_messages) < config.threshold:
            return
        if guild.id in self._trigger_tasks:
            return
        if now < self._cooldowns.get(guild.id, 0.0):
            return

        self._cooldowns[guild.id] = now + max(COOLDOWN_SECONDS, config.window_seconds)
        channel_events = tuple(state.channels)
        mention_events = tuple(state.everyone_messages)
        task = asyncio.create_task(
            self._handle_trigger(guild.id, channel_events, mention_events),
            name=f"antiraid-trigger-{guild.id}",
        )
        self._trigger_tasks[guild.id] = task
        task.add_done_callback(
            lambda finished, guild_id=guild.id: self._trigger_finished(
                guild_id, finished
            )
        )

    def _trigger_finished(
        self, guild_id: int, task: asyncio.Task[None]
    ) -> None:
        if self._trigger_tasks.get(guild_id) is task:
            self._trigger_tasks.pop(guild_id, None)
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            # A falha ou cancelamento não deve virar erro não tratado do bot.
            pass

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        guild = getattr(channel, "guild", None)
        if guild is None:
            return
        await self._record_channel(guild, channel.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._record_everyone_message(message)

    @commands.Cog.listener()
    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        # Uma edição que transforma uma mensagem em menção real também é um
        # sinal; IDs já contabilizados são deduplicados no estado da guild.
        await self._record_everyone_message(after)

    # ------------------------------- alerta e atribuição

    async def _send_alert(
        self,
        guild: discord.Guild,
        config: AntiRaidConfig,
        content: str,
    ) -> Optional[discord.Message]:
        channel = self._alert_channel(guild, config)
        if channel is None or not self.alert_permissions(guild, channel):
            return None
        try:
            return await _deliver(channel, content)
        except _DISCORD_ERRORS:
            return None

    async def _edit_alert(
        self, message: Optional[discord.Message], content: str
    ) -> None:
        if message is None:
            return
        try:
            await message.edit(
                content=content,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except _DISCORD_ERRORS:
            pass

    async def _handle_trigger(
        self,
        guild_id: int,
        channel_events: tuple[tuple[float, int], ...],
        mention_events: tuple[MentionEvent, ...],
    ) -> None:
        alert: Optional[discord.Message] = None
        try:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            config = await self.get_config(guild_id)
            if not config.enabled:
                return

            texto_base = (
                "🚨 **Possível raid detectada** — sinais suficientes encontrados. "
                "Resposta preventiva em **5s**."
            )
            alert = await self._send_alert(guild, config, texto_base)

            # O atraso existe mesmo se o canal ficar indisponível: ele mantém
            # o comportamento da contagem regressiva e evita ação instantânea.
            for remaining in range(4, -1, -1):
                await asyncio.sleep(1)
                if remaining:
                    await self._edit_alert(
                        alert,
                        (
                            "🚨 **Possível raid detectada** — resposta preventiva "
                            f"em **{remaining}s**."
                        ),
                    )
                else:
                    await self._edit_alert(
                        alert,
                        "🚨 **Raid verificada** — atribuindo sinais com segurança.",
                    )

            config = await self.get_config(guild_id)
            if not config.enabled:
                await self._edit_alert(alert, "ℹ️ Anti-raid desativado durante a contagem.")
                return

            punished, skipped, no_executor = await self._respond_to_incident(
                guild, config, channel_events, mention_events
            )
            if no_executor:
                final = (
                    "⚠️ **Raid detectada**, mas não encontrei executor com "
                    "evidência suficiente no Audit Log e nas mensagens; "
                    "nenhum timeout aplicado."
                )
            else:
                final = (
                    "🛡️ **Resposta anti-raid concluída** — "
                    f"{punished} membro(s) em timeout temporário; "
                    f"{skipped} ignorado(s) por segurança/hierarquia."
                )
            await self._edit_alert(alert, final)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - listener nunca deve cair
            print(f"Falha tolerada no anti-raid da guild {guild_id}: {error!r}")
            await self._edit_alert(
                alert,
                "⚠️ Anti-raid: não foi possível concluir a resposta; verifique os logs.",
            )

    async def _audit_channel_creators(
        self,
        guild: discord.Guild,
        channel_ids: set[int],
        since: float,
        window: int,
    ) -> dict[int, int]:
        """Mapeia canal -> executor somente por entradas recentes do Audit Log."""
        creators: dict[int, int] = {}
        try:
            async for entry in guild.audit_logs(
                limit=100,
                action=discord.AuditLogAction.channel_create,
            ):
                created_at = _timestamp(entry.created_at) or 0.0
                if created_at < since - max(5, window):
                    break
                target_id = getattr(entry.target, "id", None)
                executor_id = getattr(entry.user, "id", None)
                if target_id in channel_ids and executor_id is not None:
                    creators[int(target_id)] = int(executor_id)
        except _DISCORD_ERRORS:
            # Sem View Audit Log, a atribuição não é confiável: alertar, não punir.
            return {}
        return creators

    async def _incident_evidence(
        self,
        guild: discord.Guild,
        config: AntiRaidConfig,
        channel_events: tuple[tuple[float, int], ...],
        mention_events: tuple[MentionEvent, ...],
    ) -> tuple[dict[int, int], dict[int, int]]:
        # A contagem regressiva dura 5 s, então a janela original pode expirar.
        # Usamos o snapshot capturado no instante do gatilho, sem aceitar eventos
        # posteriores como parte da mesma ocorrência.
        channels = channel_events
        messages = mention_events

        if len(channels) < config.threshold or len(messages) < config.threshold:
            return {}, {}

        creators = await self._audit_channel_creators(
            guild,
            {channel_id for _, channel_id in channels},
            min(created_at for created_at, _ in channels),
            config.window_seconds,
        )
        channel_counts = Counter(
            creators[channel_id]
            for _, channel_id in channels
            if channel_id in creators
        )
        message_counts = Counter(event.author_id for event in messages)

        # A pessoa precisa ter escrito o limiar de menções e ter ao menos um
        # canal do mesmo intervalo atribuído a ela pelo Audit Log. Se os dois
        # sinais se misturam entre pessoas, somente alertamos.
        candidate_ids = {
            member_id
            for member_id, count in message_counts.items()
            if count >= config.threshold and channel_counts.get(member_id, 0) >= 1
        }
        return {
            member_id: message_counts[member_id] for member_id in candidate_ids
        }, {
            member_id: channel_counts[member_id] for member_id in candidate_ids
        }

    async def _member_for(
        self, guild: discord.Guild, member_id: int
    ) -> Optional[discord.Member]:
        member = guild.get_member(member_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(member_id)
        except _DISCORD_ERRORS:
            return None

    async def _respond_to_incident(
        self,
        guild: discord.Guild,
        config: AntiRaidConfig,
        channel_events: tuple[tuple[float, int], ...],
        mention_events: tuple[MentionEvent, ...],
    ) -> tuple[int, int, bool]:
        message_counts, channel_counts = await self._incident_evidence(
            guild, config, channel_events, mention_events
        )
        if not message_counts:
            return 0, 0, True

        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.moderate_members:
            return 0, len(message_counts), False

        punished = 0
        skipped = 0
        for member_id in message_counts:
            member = await self._member_for(guild, member_id)
            if member is None:
                skipped += 1
                continue
            result = await self._apply_timeout(
                guild,
                member,
                config.quarantine_seconds,
                message_counts[member_id],
                channel_counts.get(member_id, 0),
            )
            if result == "punished":
                punished += 1
            else:
                skipped += 1
        return punished, skipped, False

    async def _apply_timeout(
        self,
        guild: discord.Guild,
        member: discord.Member,
        quarantine_seconds: int,
        message_count: int,
        channel_count: int,
    ) -> str:
        """Aplica apenas timeout, preservando o timeout que já existia."""
        bot_member = guild.me
        if bot_member is None:
            return "skipped"
        if member.bot or member.id == guild.owner_id or member.id == bot_member.id:
            return "skipped"
        if member.guild_permissions.administrator:
            return "skipped"
        if member.top_role >= bot_member.top_role:
            return "skipped"
        if not bot_member.guild_permissions.moderate_members:
            return "skipped"

        agora = time.time()
        target_until = agora + min(
            max(5, quarantine_seconds), MAX_QUARANTINE_SECONDS
        )
        current_until = _timestamp(getattr(member, "timed_out_until", None))

        # Uma punição que já termina depois da nossa nunca é encurtada nem
        # substituída; isso também evita renovar o timeout em cada gatilho.
        if current_until is not None and current_until >= target_until:
            return "skipped"

        async with self._timeout_lock:
            row = await asyncio.to_thread(_get_timeout_sync, guild.id, member.id)
            original_until = current_until if current_until and current_until > agora else None
            if row is not None and float(row["bot_until"]) > agora:
                saved_original = row["original_until"]
                if saved_original is not None:
                    original_until = float(saved_original)
                if current_until is not None and current_until >= target_until:
                    return "skipped"

            try:
                await asyncio.to_thread(
                    _save_timeout_sync,
                    guild.id,
                    member.id,
                    original_until,
                    target_until,
                )
                await member.edit(
                    timed_out_until=datetime.fromtimestamp(
                        target_until, tz=timezone.utc
                    ),
                    reason=(
                        "Anti-raid: "
                        f"{channel_count} canal(is) criado(s) e "
                        f"{message_count} menção(ões) everyone."
                    ),
                )
            except sqlite3.Error:
                return "skipped"
            except _DISCORD_ERRORS:
                try:
                    await asyncio.to_thread(
                        _delete_timeout_sync, guild.id, member.id
                    )
                except sqlite3.Error:
                    pass
                return "skipped"
        return "punished"

    # ------------------------------- restauração automática

    @tasks.loop(seconds=5)
    async def restaurar_timeouts(self) -> None:
        agora = time.time()
        try:
            registros = await asyncio.to_thread(_expired_timeouts_sync, agora)
        except sqlite3.Error:
            return

        for registro in registros:
            guild = self.bot.get_guild(int(registro["guild_id"]))
            if guild is None:
                continue
            member = guild.get_member(int(registro["member_id"]))
            if member is None:
                try:
                    member = await guild.fetch_member(int(registro["member_id"]))
                except _DISCORD_ERRORS:
                    continue
            if member is None:
                continue

            current_until = _timestamp(getattr(member, "timed_out_until", None))
            bot_until = float(registro["bot_until"])
            # Se alguém estendeu manualmente o timeout depois do nosso, não
            # podemos limpá-lo: removemos somente o registro do nosso ciclo.
            if (
                current_until is not None
                and current_until > agora + 2
                and current_until > bot_until + 2
            ):
                try:
                    await asyncio.to_thread(
                        _delete_timeout_sync,
                        guild.id,
                        member.id,
                    )
                except sqlite3.Error:
                    pass
                continue

            original_until = registro["original_until"]
            restore_to = (
                _datetime_from_timestamp(float(original_until))
                if original_until is not None and float(original_until) > agora
                else None
            )
            try:
                await member.edit(
                    timed_out_until=restore_to,
                    reason="Anti-raid: fim do timeout temporário; restauração.",
                )
            except discord.NotFound:
                # Membro saiu ou o timeout já não existe; não insistir para sempre.
                pass
            except (discord.Forbidden, discord.HTTPException):
                # Permissões/rate limit podem ser temporários; tentar no próximo ciclo.
                continue
            try:
                await asyncio.to_thread(
                    _delete_timeout_sync, guild.id, member.id
                )
            except sqlite3.Error:
                pass

    @restaurar_timeouts.before_loop
    async def aguardar_bot(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    """Inicializa banco antes de registrar o Cog e os dois grupos slash."""
    await asyncio.to_thread(_ensure_database_sync)
    await bot.add_cog(AntiRaid(bot))
