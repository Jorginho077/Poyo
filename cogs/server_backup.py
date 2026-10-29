"""Backup e restauração MERGE de servidores Discord.

O formato é um ZIP versionado com JSON e não armazena bytes de anexos.
A restauração é deliberadamente não destrutiva: não remove nem edita cargos,
canais ou mensagens existentes e nunca altera o cargo @everyone.

Limitações intencionais da repostagem:
- o autor novo é o bot, não o autor original;
- IDs de mensagens e timestamps originais não são restaurados;
- anexos são repostados somente como metadados/URLs, sem bytes binários.
"""

from __future__ import annotations

import io
import inspect
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable, Optional

import discord
from discord import app_commands
from discord.ext import commands


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


SCHEMA_VERSION = 1
# Discord normalmente aceita 25 MiB por arquivo em bots sem limite maior. O
# cog usa um teto conservador, explícito, antes de tentar enviar o anexo.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_INPUT_BYTES = MAX_UPLOAD_BYTES
MAX_ZIP_MEMBERS = 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_JSON_MEMBER_BYTES = MAX_UNCOMPRESSED_BYTES
MAX_ROLES = 250
MAX_CHANNELS = 500
MAX_MESSAGES = 500_000
MAX_PERMISSIONS_PER_CHANNEL = 200
MAX_STRING_LENGTH = 16_384
MAX_MESSAGE_CONTENT = 8_000
MAX_DISCORD_MESSAGE = 2_000
MAX_NAME_LENGTH = 100
SUPPORTED_CHANNEL_TYPES = {"category", "text", "news", "voice"}
MESSAGE_FILE_RE = re.compile(r"^messages/([0-9]{1,32})\.json$")

RESTORE_LIMITATIONS = (
    "A repostagem é uma transcrição: não restaura o autor original, IDs, "
    "timestamps nem os bytes binários de mídia."
)


class BackupError(Exception):
    """Erro controlado que pode ser apresentado ao administrador."""


@dataclass
class BackupPlan:
    """Dados validados, já fora do ZIP, usados pela prévia e pela confirmação."""

    manifest: dict[str, Any]
    roles: list[dict[str, Any]]
    channels: list[dict[str, Any]]
    messages_by_channel: dict[str, list[dict[str, Any]]]
    ignored_types: list[dict[str, Any]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _channel_kind(channel: discord.abc.GuildChannel) -> Optional[str]:
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.TextChannel):
        try:
            return "news" if channel.is_news() else "text"
        except AttributeError:
            return "text"
    return None


def _is_safe_zip_name(name: str) -> bool:
    """Aceita somente nomes relativos POSIX e impede traversal/symlinks."""
    if not name or "\\" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    return all(part not in {"", "."} for part in path.parts)


def _as_int(value: Any, field: str, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BackupError(f"Campo `{field}` precisa ser um inteiro.")
    if not minimum <= value <= maximum:
        raise BackupError(f"Campo `{field}` está fora dos limites permitidos.")
    return value


def _as_string(
    value: Any,
    field: str,
    *,
    maximum: int = MAX_STRING_LENGTH,
    allow_none: bool = False,
) -> Optional[str]:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise BackupError(f"Campo `{field}` precisa ser texto.")
    if len(value) > maximum:
        raise BackupError(f"Campo `{field}` excede o tamanho permitido.")
    return value


def _serialize_overwrites(
    channel: discord.abc.GuildChannel,
) -> list[dict[str, Any]]:
    """Serializa overwrites de cargos e membros sem baixar arquivos."""
    try:
        overwrites = channel.overwrites
        if callable(overwrites):
            overwrites = overwrites()
    except (AttributeError, TypeError):
        overwrites = {}

    output: list[dict[str, Any]] = []
    for target, overwrite in overwrites.items():
        try:
            allow, deny = overwrite.pair()
            target_id = int(target.id)
            if isinstance(target, discord.Role):
                target_type = "role"
                target_name = target.name
            else:
                target_type = "member"
                target_name = getattr(target, "display_name", str(target))
            output.append(
                {
                    "target_type": target_type,
                    "target_id": str(target_id),
                    "target_name": str(target_name)[:MAX_NAME_LENGTH],
                    "allow": int(allow.value),
                    "deny": int(deny.value),
                }
            )
        except (AttributeError, TypeError, ValueError):
            # Um overwrite inconsistente não deve impedir os demais canais.
            continue
    return output


def _attachment_metadata(attachment: discord.Attachment) -> dict[str, Any]:
    try:
        spoiler = bool(attachment.is_spoiler())
    except AttributeError:
        spoiler = False
    return {
        "id": str(attachment.id),
        "filename": str(attachment.filename)[:MAX_STRING_LENGTH],
        "url": str(attachment.url)[:MAX_STRING_LENGTH],
        "proxy_url": str(getattr(attachment, "proxy_url", ""))[:MAX_STRING_LENGTH],
        "content_type": getattr(attachment, "content_type", None),
        "size": int(getattr(attachment, "size", 0) or 0),
        "height": getattr(attachment, "height", None),
        "width": getattr(attachment, "width", None),
        "spoiler": spoiler,
    }


def _preview_channel_type(kind: str) -> str:
    return {
        "category": "categoria",
        "text": "texto",
        "news": "news/anúncios",
        "voice": "voz",
    }.get(kind, kind)


def _transcript_text(message: dict[str, Any]) -> str:
    author_name = str(
        message.get("author_display_name")
        or message.get("author_name")
        or "autor desconhecido"
    )
    author_id = str(message.get("author_id") or "desconhecido")
    created_at = str(message.get("created_at") or "data desconhecida")
    content = message.get("content") or "(sem texto)"
    lines = [
        "[Transcrição]",
        f"Autor original: {author_name} (ID {author_id})",
        f"Data original: {created_at}",
        "",
        str(content),
    ]
    attachments = message.get("attachments") or []
    if attachments:
        lines.extend(
            [
                "",
                "Anexos (metadados/URLs; bytes não restaurados):",
            ]
        )
        for attachment in attachments:
            filename = str(attachment.get("filename") or "arquivo")
            url = str(attachment.get("url") or "URL indisponível")
            lines.append(f"- {filename}: {url}")
    return "\n".join(lines)


def _chunks(text: str, limit: int = MAX_DISCORD_MESSAGE) -> Iterable[str]:
    if not text:
        yield "(transcrição vazia)"
        return
    for start in range(0, len(text), limit):
        yield text[start : start + limit]


class RestoreConfirmView(discord.ui.View):
    """Confirmação efêmera vinculada ao administrador que iniciou a operação."""

    def __init__(
        self,
        cog: "ServerBackup",
        initiator_id: int,
        plan: BackupPlan,
        repostar_mensagens: bool,
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.initiator_id = initiator_id
        self.plan = plan
        self.repostar_mensagens = repostar_mensagens
        self.finished = False
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.initiator_id:
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Somente o administrador que iniciou esta restauração pode usar estes botões.",
                ephemeral=True,
            )
        return False

    async def on_timeout(self) -> None:
        self.finished = True
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        # Mensagens efêmeras podem não estar editáveis após o timeout; a falha
        # não deve produzir uma tarefa pendente ou derrubar o cog.
        try:
            if self.message is not None:
                await self.message.edit(
                    content="Prévia expirada. Execute `/backup restaurar` novamente.",
                    view=self,
                )
        except (discord.HTTPException, discord.NotFound, discord.Forbidden):
            pass

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.success)
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        self.stop()
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            if interaction.message is not None:
                await interaction.message.edit(
                    content="Restauração confirmada. Executando MERGE não destrutivo...",
                    view=self,
                )
        except (discord.HTTPException, discord.NotFound, discord.Forbidden):
            pass

        report = await self.cog._restore_plan(
            interaction.guild,
            self.plan,
            repostar_mensagens=self.repostar_mensagens,
        )
        try:
            if interaction.message is not None:
                await interaction.message.edit(
                    content=self.cog._format_restore_report(report),
                    view=None,
                )
        except (discord.HTTPException, discord.NotFound, discord.Forbidden):
            await interaction.followup.send(
                self.cog._format_restore_report(report),
                ephemeral=True,
            )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.danger)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        self.stop()
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        try:
            if interaction.message is not None:
                await interaction.message.edit(
                    content="Restauração cancelada. Nenhuma alteração foi feita.",
                    view=None,
                )
        except (discord.HTTPException, discord.NotFound, discord.Forbidden):
            await interaction.followup.send(
                "Restauração cancelada. Nenhuma alteração foi feita.",
                ephemeral=True,
            )


class ServerBackup(commands.Cog):
    """Comandos slash `/backup criar` e `/backup restaurar`."""

    backup = app_commands.Group(
        name="backup",
        description="Exporta ou restaura a estrutura do servidor com segurança.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def _member_is_administrator(interaction: discord.Interaction) -> bool:
        if is_bot_owner(interaction.user.id):
            return True
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(permissions and permissions.administrator)

    @staticmethod
    def _bot_missing_permissions(
        guild: discord.Guild,
        *,
        restoring: bool,
        reposting: bool = False,
    ) -> list[str]:
        me = guild.me
        if me is None and guild._state.user is not None:
            me = guild.get_member(guild._state.user.id)
        if me is None:
            return ["identificar minhas permissões no servidor"]
        permissions = me.guild_permissions
        if permissions.administrator:
            return []
        required = {
            "gerenciar canais": permissions.manage_channels,
            "gerenciar cargos": permissions.manage_roles,
        } if restoring else {
            "ver canais": permissions.view_channel,
            "ler histórico de mensagens": permissions.read_message_history,
        }
        if reposting:
            required["enviar mensagens"] = permissions.send_messages
        return [label for label, present in required.items() if not present]

    async def _send_error(self, interaction: discord.Interaction, text: str) -> None:
        try:
            await interaction.followup.send(f"❌ {text}", ephemeral=True)
        except discord.HTTPException:
            pass

    @backup.command(
        name="criar",
        description="Cria um ZIP versionado da estrutura e, opcionalmente, das mensagens.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        incluir_mensagens="Inclui todo o histórico acessível de texto/news e metadados de anexos.",
    )
    async def criar(
        self,
        interaction: discord.Interaction,
        incluir_mensagens: bool = False,
    ) -> None:
        # O defer ocorre antes de validar ou percorrer o histórico, pois o
        # trabalho pode exceder o limite de resposta inicial do Discord.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await self._send_error(interaction, "Este comando só pode ser usado em um servidor.")
            return
        if not self._member_is_administrator(interaction):
            await self._send_error(interaction, "É necessário ter a permissão Administrator.")
            return
        missing = self._bot_missing_permissions(guild, restoring=False)
        if missing:
            await self._send_error(
                interaction,
                "Não tenho: " + ", ".join(missing) + ".",
            )
            return

        try:
            archive, stats = await self._build_backup(
                guild,
                incluir_mensagens=incluir_mensagens,
            )
            if len(archive) > MAX_UPLOAD_BYTES:
                raise BackupError(
                    f"O ZIP tem {len(archive) / 1024 / 1024:.2f} MiB e excede o limite "
                    f"conservador de {MAX_UPLOAD_BYTES / 1024 / 1024:.0f} MiB do Discord. "
                    "Tente criar o backup sem mensagens."
                )
            timestamp = _utc_now().strftime("%Y%m%d-%H%M%S")
            filename = f"backup-{guild.id}-{timestamp}.zip"
            summary = (
                f"Backup criado: `{filename}` ({len(archive) / 1024 / 1024:.2f} MiB).\n"
                f"Cargos: {stats['roles']}; canais suportados: {stats['channels']}; "
                f"mensagens: {stats['messages']}."
            )
            if stats["warnings"]:
                summary += "\nAvisos: " + " | ".join(stats["warnings"][:5])
            await interaction.followup.send(
                summary,
                file=discord.File(io.BytesIO(archive), filename=filename),
                ephemeral=True,
            )
        except BackupError as exc:
            await self._send_error(interaction, str(exc))
        except discord.Forbidden:
            await self._send_error(
                interaction,
                "O Discord negou o acesso a algum canal/histórico. O backup não foi enviado.",
            )
        except discord.HTTPException as exc:
            if exc.status in {413, 400}:
                await self._send_error(
                    interaction,
                    "O Discord recusou o anexo; ele pode exceder o limite de tamanho permitido.",
                )
            else:
                await self._send_error(interaction, "Falha HTTP ao gerar ou enviar o backup.")
        except Exception as exc:  # pragma: no cover - último isolamento do comando
            print(f"Erro inesperado em /backup criar: {exc!r}")
            await self._send_error(interaction, "Falha inesperada ao gerar o backup.")

    @backup.command(
        name="restaurar",
        description="Valida um ZIP e mostra uma prévia antes do MERGE não destrutivo.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        arquivo="ZIP de backup criado por este cog.",
        repostar_mensagens="Reposta transcrições no servidor; falso por padrão e exige opt-in.",
    )
    async def restaurar(
        self,
        interaction: discord.Interaction,
        arquivo: discord.Attachment,
        repostar_mensagens: bool = False,
    ) -> None:
        # Também deferimos antes de baixar/validar o anexo.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await self._send_error(interaction, "Este comando só pode ser usado em um servidor.")
            return
        if not self._member_is_administrator(interaction):
            await self._send_error(interaction, "É necessário ter a permissão Administrator.")
            return
        missing = self._bot_missing_permissions(
            guild,
            restoring=True,
            reposting=repostar_mensagens,
        )
        if missing:
            await self._send_error(
                interaction,
                "Para restaurar preciso de: " + ", ".join(missing) + ".",
            )
            return
        if not arquivo.filename.lower().endswith(".zip"):
            await self._send_error(interaction, "Anexe um arquivo `.zip`.")
            return
        if arquivo.size is not None and arquivo.size > MAX_INPUT_BYTES:
            await self._send_error(
                interaction,
                f"O ZIP excede o limite de entrada de {MAX_INPUT_BYTES / 1024 / 1024:.0f} MiB.",
            )
            return

        try:
            raw = await arquivo.read()
            if len(raw) > MAX_INPUT_BYTES:
                raise BackupError(
                    f"O ZIP excede o limite de entrada de {MAX_INPUT_BYTES / 1024 / 1024:.0f} MiB."
                )
            plan = self._validate_archive(raw)
            preview = self._format_preview(guild, plan, repostar_mensagens)
            view = RestoreConfirmView(
                self,
                interaction.user.id,
                plan,
                repostar_mensagens,
            )
            message = await interaction.followup.send(
                preview,
                view=view,
                ephemeral=True,
                wait=True,
            )
            view.message = message
        except BackupError as exc:
            await self._send_error(interaction, str(exc))
        except (discord.Forbidden, discord.NotFound):
            await self._send_error(interaction, "Não consegui baixar o arquivo anexado.")
        except discord.HTTPException:
            await self._send_error(interaction, "Falha HTTP ao validar o anexo.")
        except Exception as exc:  # pragma: no cover - último isolamento do comando
            print(f"Erro inesperado em /backup restaurar: {exc!r}")
            await self._send_error(interaction, "Falha inesperada ao validar o backup.")

    async def _build_backup(
        self,
        guild: discord.Guild,
        *,
        incluir_mensagens: bool,
    ) -> tuple[bytes, dict[str, Any]]:
        roles: list[dict[str, Any]] = []
        for role in guild.roles:
            if role.is_default() or role.managed:
                continue
            roles.append(
                {
                    "legacy_id": str(role.id),
                    "name": role.name,
                    "color": int(role.color.value),
                    "hoist": bool(role.hoist),
                    "mentionable": bool(role.mentionable),
                    "position": int(role.position),
                    "permissions": int(role.permissions.value),
                }
            )

        channels: list[dict[str, Any]] = []
        ignored_types: list[dict[str, Any]] = []
        for channel in sorted(guild.channels, key=lambda item: (item.position, item.id)):
            kind = _channel_kind(channel)
            if kind is None:
                ignored_types.append(
                    {
                        "id": str(channel.id),
                        "name": channel.name,
                        "type": str(channel.type),
                    }
                )
                continue
            parent = getattr(channel, "category", None)
            record: dict[str, Any] = {
                "legacy_id": str(channel.id),
                "name": channel.name,
                "type": kind,
                "parent_legacy_id": str(parent.id) if parent else None,
                "position": int(channel.position),
                "permissions": _serialize_overwrites(channel),
            }
            if kind in {"text", "news"}:
                record.update(
                    {
                        "topic": getattr(channel, "topic", None),
                        "nsfw": bool(getattr(channel, "nsfw", False)),
                        "slowmode_delay": int(getattr(channel, "slowmode_delay", 0)),
                        "default_auto_archive_duration": getattr(
                            channel, "default_auto_archive_duration", None
                        ),
                    }
                )
            elif kind == "voice":
                record.update(
                    {
                        "bitrate": int(getattr(channel, "bitrate", 0)),
                        "user_limit": int(getattr(channel, "user_limit", 0)),
                        "rtc_region": getattr(channel, "rtc_region", None),
                        "video_quality_mode": getattr(
                            getattr(channel, "video_quality_mode", None), "value", None
                        ),
                    }
                )
            channels.append(record)

        messages_by_channel: dict[str, list[dict[str, Any]]] = {}
        warnings: list[str] = []
        total_messages = 0
        if incluir_mensagens:
            for channel in guild.channels:
                kind = _channel_kind(channel)
                if kind not in {"text", "news"}:
                    continue
                records: list[dict[str, Any]] = []
                accessible = True
                try:
                    async for message in channel.history(limit=None, oldest_first=True):
                        total_messages += 1
                        if total_messages > MAX_MESSAGES:
                            raise BackupError(
                                "O histórico acessível excede o limite de segurança de "
                                f"{MAX_MESSAGES:,} mensagens; nenhum ZIP parcial foi enviado."
                            )
                        author = message.author
                        records.append(
                            {
                                "id": str(message.id),
                                "author_id": str(getattr(author, "id", "desconhecido")),
                                "author_name": str(getattr(author, "name", "desconhecido")),
                                "author_display_name": str(
                                    getattr(author, "display_name", getattr(author, "name", "desconhecido"))
                                ),
                                "created_at": _iso(message.created_at),
                                "edited_at": _iso(message.edited_at),
                                "content": message.content or "",
                                "attachments": [
                                    _attachment_metadata(item)
                                    for item in message.attachments
                                ],
                            }
                        )
                except BackupError:
                    raise
                except (discord.Forbidden, discord.HTTPException):
                    accessible = False
                    warnings.append(f"histórico inacessível: #{channel.name}")
                if accessible:
                    messages_by_channel[str(channel.id)] = records

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "format": "poyo-server-backup",
            "guild_id": str(guild.id),
            "guild_name": guild.name,
            "created_at": _iso(_utc_now()),
            "includes_messages": bool(incluir_mensagens),
            "limitations": [
                "attachments_metadata_only",
                "attachment_bytes_not_preserved",
                "managed_roles_excluded",
            ],
            "ignored_channel_types": ignored_types,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", _json_bytes(manifest))
            archive.writestr(
                "roles.json",
                _json_bytes({"schema_version": SCHEMA_VERSION, "roles": roles}),
            )
            archive.writestr(
                "channels.json",
                _json_bytes(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "channels": channels,
                        "ignored_types": ignored_types,
                    }
                ),
            )
            for legacy_id, messages in messages_by_channel.items():
                archive.writestr(
                    f"messages/{legacy_id}.json",
                    _json_bytes(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "channel_legacy_id": legacy_id,
                            "messages": messages,
                        }
                    ),
                )
        result = buffer.getvalue()
        if len(result) > MAX_UPLOAD_BYTES:
            raise BackupError(
                f"O ZIP gerado excede o limite de {MAX_UPLOAD_BYTES / 1024 / 1024:.0f} MiB "
                "do Discord; reduza o escopo ou desative `incluir_mensagens`."
            )
        return result, {
            "roles": len(roles),
            "channels": len(channels),
            "messages": total_messages,
            "warnings": warnings + ([f"tipos ignorados: {len(ignored_types)}"] if ignored_types else []),
        }

    @staticmethod
    def _read_json_member(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
        try:
            info = archive.getinfo(name)
        except KeyError as exc:
            raise BackupError(f"ZIP sem o arquivo obrigatório `{name}`.") from exc
        if info.file_size > MAX_JSON_MEMBER_BYTES:
            raise BackupError(f"O membro `{name}` é grande demais.")
        try:
            raw = archive.read(info)
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError(f"JSON inválido em `{name}`.") from exc
        if not isinstance(value, dict):
            raise BackupError(f"`{name}` precisa conter um objeto JSON.")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise BackupError(f"Versão de schema não suportada em `{name}`.")
        return value

    def _validate_archive(self, raw: bytes) -> BackupPlan:
        if len(raw) > MAX_INPUT_BYTES:
            raise BackupError("O arquivo excede o limite de entrada permitido.")
        if not zipfile.is_zipfile(io.BytesIO(raw)):
            raise BackupError("O anexo não é um ZIP válido.")

        try:
            archive = zipfile.ZipFile(io.BytesIO(raw), "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise BackupError("Não foi possível abrir o ZIP.") from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_MEMBERS:
                raise BackupError("O ZIP contém membros demais e foi recusado.")
            total_uncompressed = 0
            names: set[str] = set()
            for info in infos:
                if info.is_dir() or not _is_safe_zip_name(info.filename):
                    raise BackupError("O ZIP contém caminho inseguro ou diretório inesperado.")
                if info.filename in names:
                    raise BackupError("O ZIP contém nomes de membros duplicados.")
                names.add(info.filename)
                if info.flag_bits & 0x1:
                    raise BackupError("ZIPs criptografados não são aceitos.")
                if info.file_size > MAX_JSON_MEMBER_BYTES:
                    raise BackupError("Um membro do ZIP excede o limite de segurança.")
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                    raise BackupError("O conteúdo descompactado excede o limite de segurança.")

            if "manifest.json" not in names or "roles.json" not in names or "channels.json" not in names:
                raise BackupError("O ZIP não contém manifest.json, roles.json e channels.json.")
            manifest = self._read_json_member(archive, "manifest.json")
            if manifest.get("format") != "poyo-server-backup":
                raise BackupError("Formato de backup desconhecido.")
            _as_string(manifest.get("guild_id"), "manifest.guild_id", maximum=32)
            _as_string(manifest.get("guild_name"), "manifest.guild_name", maximum=MAX_NAME_LENGTH)

            roles_data = self._read_json_member(archive, "roles.json")
            roles = roles_data.get("roles")
            if not isinstance(roles, list) or len(roles) > MAX_ROLES:
                raise BackupError("Quantidade de cargos inválida ou acima do limite.")
            role_ids: set[str] = set()
            for index, role in enumerate(roles):
                if not isinstance(role, dict):
                    raise BackupError(f"Cargo #{index + 1} inválido.")
                legacy_id = _as_string(role.get("legacy_id"), f"roles[{index}].legacy_id", maximum=32)
                assert legacy_id is not None
                if legacy_id in role_ids:
                    raise BackupError("Há IDs legados de cargos duplicados.")
                role_ids.add(legacy_id)
                _as_string(role.get("name"), f"roles[{index}].name", maximum=MAX_NAME_LENGTH)
                _as_int(role.get("color"), f"roles[{index}].color", maximum=0xFFFFFF)
                _as_int(role.get("position"), f"roles[{index}].position", maximum=MAX_ROLES)
                _as_int(role.get("permissions"), f"roles[{index}].permissions")
                if not isinstance(role.get("hoist"), bool) or not isinstance(role.get("mentionable"), bool):
                    raise BackupError(f"Flags inválidas em roles[{index}].")

            channels_data = self._read_json_member(archive, "channels.json")
            channels = channels_data.get("channels")
            if not isinstance(channels, list) or len(channels) > MAX_CHANNELS:
                raise BackupError("Quantidade de canais inválida ou acima do limite.")
            channel_ids: set[str] = set()
            ignored_types: list[dict[str, Any]] = []
            for index, channel in enumerate(channels):
                if not isinstance(channel, dict):
                    raise BackupError(f"Canal #{index + 1} inválido.")
                legacy_id = _as_string(
                    channel.get("legacy_id"),
                    f"channels[{index}].legacy_id",
                    maximum=32,
                )
                assert legacy_id is not None
                if legacy_id in channel_ids:
                    raise BackupError("Há IDs legados de canais duplicados.")
                channel_ids.add(legacy_id)
                name = _as_string(channel.get("name"), f"channels[{index}].name", maximum=MAX_NAME_LENGTH)
                kind = _as_string(channel.get("type"), f"channels[{index}].type", maximum=32)
                if kind not in SUPPORTED_CHANNEL_TYPES:
                    ignored_types.append(
                        {"legacy_id": legacy_id, "name": name or "", "type": kind or "desconhecido"}
                    )
                parent = channel.get("parent_legacy_id")
                if parent is not None:
                    _as_string(parent, f"channels[{index}].parent_legacy_id", maximum=32)
                _as_int(channel.get("position"), f"channels[{index}].position", maximum=MAX_CHANNELS)
                permissions = channel.get("permissions", [])
                if not isinstance(permissions, list) or len(permissions) > MAX_PERMISSIONS_PER_CHANNEL:
                    raise BackupError(f"Permissões inválidas em channels[{index}].")
                for permission in permissions:
                    if not isinstance(permission, dict):
                        raise BackupError("Overwrite inválido no backup.")
                    target_type = permission.get("target_type")
                    if target_type not in {"role", "member"}:
                        raise BackupError("Tipo de alvo de permissão inválido.")
                    _as_string(permission.get("target_id"), "permissions.target_id", maximum=32)
                    _as_int(permission.get("allow"), "permissions.allow")
                    _as_int(permission.get("deny"), "permissions.deny")

            messages_by_channel: dict[str, list[dict[str, Any]]] = {}
            message_count = 0
            for name in names:
                match = MESSAGE_FILE_RE.fullmatch(name)
                if not match:
                    if name not in {"manifest.json", "roles.json", "channels.json"}:
                        raise BackupError(f"Membro inesperado no ZIP: `{name}`.")
                    continue
                message_data = self._read_json_member(archive, name)
                legacy_id = match.group(1)
                if message_data.get("channel_legacy_id") != legacy_id:
                    raise BackupError(f"ID de canal inconsistente em `{name}`.")
                messages = message_data.get("messages")
                if not isinstance(messages, list):
                    raise BackupError(f"Mensagens inválidas em `{name}`.")
                channel_messages: list[dict[str, Any]] = []
                for message in messages:
                    message_count += 1
                    if message_count > MAX_MESSAGES:
                        raise BackupError("O ZIP excede o limite de mensagens permitido.")
                    if not isinstance(message, dict):
                        raise BackupError("Registro de mensagem inválido.")
                    _as_string(message.get("id"), "message.id", maximum=32)
                    _as_string(message.get("author_id"), "message.author_id", maximum=32)
                    _as_string(message.get("author_name"), "message.author_name", maximum=MAX_NAME_LENGTH)
                    _as_string(
                        message.get("author_display_name"),
                        "message.author_display_name",
                        maximum=MAX_NAME_LENGTH,
                    )
                    _as_string(message.get("created_at"), "message.created_at", maximum=64)
                    _as_string(message.get("edited_at"), "message.edited_at", maximum=64, allow_none=True)
                    _as_string(message.get("content"), "message.content", maximum=MAX_MESSAGE_CONTENT)
                    attachments = message.get("attachments", [])
                    if not isinstance(attachments, list) or len(attachments) > 20:
                        raise BackupError("Lista de anexos inválida ou grande demais.")
                    for attachment in attachments:
                        if not isinstance(attachment, dict):
                            raise BackupError("Metadado de anexo inválido.")
                        _as_string(attachment.get("filename"), "attachment.filename", maximum=MAX_STRING_LENGTH)
                        url = _as_string(attachment.get("url"), "attachment.url", maximum=MAX_STRING_LENGTH)
                        if url and not url.startswith(("http://", "https://")):
                            raise BackupError("URL de anexo com esquema não permitido.")
                    channel_messages.append(message)
                messages_by_channel[legacy_id] = channel_messages

            unknown_message_channels = set(messages_by_channel) - channel_ids
            if unknown_message_channels:
                raise BackupError("O ZIP contém histórico para canal ausente no inventário.")
            return BackupPlan(
                manifest=manifest,
                roles=roles,
                channels=channels,
                messages_by_channel=messages_by_channel,
                ignored_types=ignored_types,
            )

    @staticmethod
    def _format_preview(
        guild: discord.Guild,
        plan: BackupPlan,
        repostar_mensagens: bool,
    ) -> str:
        supported = [item for item in plan.channels if item.get("type") in SUPPORTED_CHANNEL_TYPES]
        counts: dict[str, int] = {}
        for item in supported:
            kind = str(item["type"])
            counts[kind] = counts.get(kind, 0) + 1
        total_messages = sum(len(items) for items in plan.messages_by_channel.values())
        role_names = {role.name for role in guild.roles if not role.managed and not role.is_default()}
        channel_names = {channel.name for channel in guild.channels}
        likely_roles = sum(1 for role in plan.roles if role.get("name") not in role_names)
        likely_channels = sum(1 for channel in supported if channel.get("name") not in channel_names)
        ignored = len(plan.ignored_types)
        lines = [
            "## Prévia do MERGE não destrutivo",
            f"Backup: `{plan.manifest.get('guild_name', 'desconhecido')}` → servidor `{guild.name}`.",
            f"Cargos: {len(plan.roles)} no arquivo; até {likely_roles} ausentes serão criados.",
            (
                f"Canais: {len(supported)} suportados "
                f"(categorias {counts.get('category', 0)}, texto {counts.get('text', 0)}, "
                f"news {counts.get('news', 0)}, voz {counts.get('voice', 0)}); "
                f"até {likely_channels} parecem ausentes."
            ),
            f"Tipos ignorados: {ignored}.",
            (
                f"Mensagens no arquivo: {total_messages}; "
                + (
                    "serão repostadas como transcrições (opt-in confirmado)."
                    if repostar_mensagens
                    else "não serão repostadas; nenhuma mensagem será alterada."
                )
            ),
            "Nada existente será apagado ou editado; @everyone e permissões gerais não serão alterados.",
            RESTORE_LIMITATIONS,
            "Confirme somente se o servidor de destino estiver correto.",
        ]
        return "\n".join(lines)[:MAX_DISCORD_MESSAGE]

    @staticmethod
    def _find_role(guild: discord.Guild, name: str) -> Optional[discord.Role]:
        for role in guild.roles:
            if not role.is_default() and not role.managed and role.name == name:
                return role
        return None

    @staticmethod
    def _find_channel(
        guild: discord.Guild,
        record: dict[str, Any],
        parent: Optional[discord.CategoryChannel],
    ) -> Optional[discord.abc.GuildChannel]:
        wanted_kind = record.get("type")
        for channel in guild.channels:
            if channel.name != record.get("name"):
                continue
            if _channel_kind(channel) != wanted_kind:
                continue
            current_parent = getattr(channel, "category", None)
            if (current_parent.id if current_parent else None) != (parent.id if parent else None):
                continue
            return channel
        return None

    @staticmethod
    def _resolve_overwrites(
        guild: discord.Guild,
        raw_overwrites: Any,
        role_map: dict[str, discord.Role],
        report: dict[str, Any],
    ) -> dict[Any, discord.PermissionOverwrite]:
        resolved: dict[Any, discord.PermissionOverwrite] = {}
        if not isinstance(raw_overwrites, list):
            return resolved
        for item in raw_overwrites:
            try:
                target_type = item.get("target_type")
                target_id = str(item.get("target_id"))
                if target_type == "role":
                    # O overwrite de @everyone é deliberadamente ignorado:
                    # o cargo padrão e suas permissões gerais nunca são tocados.
                    if target_id == str(guild.default_role.id):
                        report["skipped_permissions"].append("@everyone")
                        continue
                    target = role_map.get(target_id)
                    if target is None:
                        report["skipped_permissions"].append(f"cargo {target_id}")
                        continue
                elif target_type == "member":
                    target = guild.get_member(int(target_id))
                    if target is None:
                        report["skipped_permissions"].append(f"membro {target_id}")
                        continue
                else:
                    continue
                allow = discord.Permissions(int(item["allow"]))
                deny = discord.Permissions(int(item["deny"]))
                resolved[target] = discord.PermissionOverwrite.from_pair(allow, deny)
            except (ValueError, TypeError, KeyError, AttributeError):
                report["skipped_permissions"].append("overwrite inválido")
        return resolved

    async def _restore_plan(
        self,
        guild: Optional[discord.Guild],
        plan: BackupPlan,
        *,
        repostar_mensagens: bool,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {
            "created_roles": [],
            "reused_roles": [],
            "failed_roles": [],
            "created_channels": [],
            "reused_channels": [],
            "failed_channels": [],
            "ignored_types": list(plan.ignored_types),
            "skipped_permissions": [],
            "legacy_to_new": {"roles": {}, "channels": {}},
            "reposted": 0,
            "failed_messages": 0,
            "warnings": [],
        }
        if guild is None:
            report["warnings"].append("servidor indisponível")
            return report

        role_map: dict[str, discord.Role] = {}
        for data in plan.roles:
            legacy_id = str(data["legacy_id"])
            name = str(data["name"])
            existing = self._find_role(guild, name)
            if existing is not None:
                role_map[legacy_id] = existing
                report["legacy_to_new"]["roles"][legacy_id] = str(existing.id)
                report["reused_roles"].append(name)
                continue
            try:
                role = await guild.create_role(
                    name=name,
                    permissions=discord.Permissions(int(data["permissions"])),
                    colour=discord.Colour(int(data["color"]) & 0xFFFFFF),
                    hoist=bool(data["hoist"]),
                    mentionable=bool(data["mentionable"]),
                    reason="Backup MERGE não destrutivo",
                )
                role_map[legacy_id] = role
                report["legacy_to_new"]["roles"][legacy_id] = str(role.id)
                report["created_roles"].append(name)
                # Não movimentamos cargos: isso poderia deslocar cargos
                # existentes ou superar a hierarquia do bot.
                if int(data.get("position", 0)) > 0:
                    report["warnings"].append(f"posição não aplicada ao cargo {name}")
            except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
                report["failed_roles"].append(f"{name}: {type(exc).__name__}")
            except Exception as exc:  # isola um registro inesperado
                report["failed_roles"].append(f"{name}: {type(exc).__name__}")

        category_map: dict[str, discord.CategoryChannel] = {}
        categories = [item for item in plan.channels if item.get("type") == "category"]
        for data in sorted(categories, key=lambda item: (int(item.get("position", 0)), str(item["legacy_id"]))):
            legacy_id = str(data["legacy_id"])
            existing = next(
                (
                    channel
                    for channel in guild.categories
                    if channel.name == data["name"]
                ),
                None,
            )
            if existing is not None:
                category_map[legacy_id] = existing
                report["legacy_to_new"]["channels"][legacy_id] = str(existing.id)
                report["reused_channels"].append(f"categoria {data['name']}")
                continue
            try:
                overwrites = self._resolve_overwrites(
                    guild,
                    data.get("permissions", []),
                    role_map,
                    report,
                )
                category = await guild.create_category(
                    str(data["name"]),
                    overwrites=overwrites or None,
                    reason="Backup MERGE não destrutivo",
                )
                category_map[legacy_id] = category
                report["legacy_to_new"]["channels"][legacy_id] = str(category.id)
                report["created_channels"].append(f"categoria {data['name']}")
            except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
                report["failed_channels"].append(f"categoria {data['name']}: {type(exc).__name__}")
            except Exception as exc:
                report["failed_channels"].append(f"categoria {data['name']}: {type(exc).__name__}")

        channel_map: dict[str, discord.abc.GuildChannel] = {
            key: value for key, value in category_map.items()
        }
        regular_channels = [item for item in plan.channels if item.get("type") in {"text", "news", "voice"}]
        regular_channels.sort(key=lambda item: (str(item.get("parent_legacy_id") or ""), int(item.get("position", 0))))
        for data in regular_channels:
            legacy_id = str(data["legacy_id"])
            parent = category_map.get(str(data.get("parent_legacy_id"))) if data.get("parent_legacy_id") else None
            existing = self._find_channel(guild, data, parent)
            if existing is not None:
                channel_map[legacy_id] = existing
                report["legacy_to_new"]["channels"][legacy_id] = str(existing.id)
                report["reused_channels"].append(f"{data['type']} {data['name']}")
                continue
            try:
                overwrites = self._resolve_overwrites(
                    guild,
                    data.get("permissions", []),
                    role_map,
                    report,
                )
                kind = str(data["type"])
                common: dict[str, Any] = {
                    "name": str(data["name"]),
                    "overwrites": overwrites or None,
                    "reason": "Backup MERGE não destrutivo",
                }
                if parent is not None:
                    common["category"] = parent
                if kind in {"text", "news"}:
                    common.update(
                        {
                            "topic": data.get("topic"),
                            "nsfw": bool(data.get("nsfw", False)),
                            "slowmode_delay": min(max(int(data.get("slowmode_delay", 0)), 0), 21600),
                        }
                    )
                    if kind == "news":
                        # discord.py expõe `news=True` nas versões que
                        # conseguem criar canais de anúncios. Mantemos um
                        # fallback seguro para texto se a API não o expuser.
                        try:
                            channel = await guild.create_text_channel(**common, news=True)
                        except TypeError:
                            channel = await guild.create_text_channel(**common)
                            report["warnings"].append(
                                f"canal news {data['name']} criado como texto pela API disponível"
                            )
                    else:
                        channel = await guild.create_text_channel(**common)
                else:
                    voice_kwargs = dict(common)
                    bitrate = int(data.get("bitrate", 0) or 0)
                    user_limit = int(data.get("user_limit", 0) or 0)
                    if 8_000 <= bitrate <= 384_000:
                        voice_kwargs["bitrate"] = bitrate
                    if 0 <= user_limit <= 99:
                        voice_kwargs["user_limit"] = user_limit
                    channel = await guild.create_voice_channel(**voice_kwargs)
                channel_map[legacy_id] = channel
                report["legacy_to_new"]["channels"][legacy_id] = str(channel.id)
                report["created_channels"].append(f"{kind} {data['name']}")
            except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
                report["failed_channels"].append(f"{data['type']} {data['name']}: {type(exc).__name__}")
            except Exception as exc:
                report["failed_channels"].append(f"{data['type']} {data['name']}: {type(exc).__name__}")

        # Mensagens são opt-in explícito e só são enviadas aos canais mapeados.
        if repostar_mensagens:
            for legacy_id, messages in plan.messages_by_channel.items():
                target = channel_map.get(legacy_id)
                if not isinstance(target, (discord.TextChannel, discord.Thread)):
                    if messages:
                        report["failed_messages"] += len(messages)
                    continue
                for message in messages:
                    try:
                        for chunk in _chunks(_transcript_text(message)):
                            await target.send(
                                chunk,
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                        report["reposted"] += 1
                    except (discord.Forbidden, discord.HTTPException, ValueError, TypeError):
                        report["failed_messages"] += 1
                    except Exception:
                        report["failed_messages"] += 1
        elif plan.messages_by_channel:
            report["warnings"].append("histórico não repostado: opt-in não solicitado")

        return report

    @staticmethod
    def _format_restore_report(report: dict[str, Any]) -> str:
        lines = [
            "## MERGE concluído com segurança",
            f"Cargos criados: {len(report['created_roles'])}; já existentes preservados: {len(report['reused_roles'])}; falhas: {len(report['failed_roles'])}.",
            f"Canais criados: {len(report['created_channels'])}; já existentes preservados: {len(report['reused_channels'])}; falhas: {len(report['failed_channels'])}.",
            f"Mensagens repostadas: {report['reposted']}; falhas de repostagem: {report['failed_messages']}.",
            f"Tipos ignorados: {len(report['ignored_types'])}; permissões não aplicadas: {len(report['skipped_permissions'])}.",
            (
                "Equivalências de IDs preservadas: "
                f"{len(report['legacy_to_new']['roles'])} cargos e "
                f"{len(report['legacy_to_new']['channels'])} canais."
            ),
            "Nenhum cargo/canal/mensagem existente foi apagado; @everyone e permissões gerais não foram alterados.",
            RESTORE_LIMITATIONS,
        ]
        details: list[str] = []
        for key, label in (
            ("failed_roles", "falhas de cargos"),
            ("failed_channels", "falhas de canais"),
            ("warnings", "avisos"),
        ):
            values = report.get(key) or []
            if values:
                details.append(f"{label}: " + "; ".join(str(item) for item in values[:5]))
        if details:
            lines.append("\n".join(details))
        return "\n".join(lines)[:MAX_DISCORD_MESSAGE]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerBackup(bot))
