# cog de tickets com components v2
import os
import io
import re
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import discord

from discord.ext import commands

log = logging.getLogger("bot.tickets")

# evita pingar sem querer
NO_MENTIONS = discord.AllowedMentions.none()
STATE_FILE = Path(os.getenv("TICKETS_STATE_FILE", "tickets_state.json"))
# imagem do topo do painel e opcional
BANNER_URL = os.getenv("TICKET_BANNER_URL", "").strip()
TZ = ZoneInfo(os.getenv("TIMEZONE", "America/Sao_Paulo"))


def _env_int(name, default):
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


MAX_PER_USER = max(1, _env_int("TICKET_MAX_PER_USER", 1))
CLOSE_DELAY = 10  # segundos ate apagar o canal
# nome do cargo da equipe e do canal de log (muda pelo ambiente)
STAFF_ROLE = os.getenv('TICKET_STAFF_ROLE', 'Staff')
LOG_HINT = os.getenv('TICKET_LOG_CHANNEL', 'chat-staff')

# staff e quem tem admin ou gerenciar mensagens ou o cargo
def is_staff(member):
    perms = member.guild_permissions
    if perms.administrator or perms.manage_messages:
        return True
    return any(r.name == STAFF_ROLE for r in member.roles)


# acha canal por pedaco do nome
def find_channel(guild, *keywords):
    achados = [c for c in guild.text_channels if any(k in c.name.lower() for k in keywords)]
    return min(achados, key=lambda c: len(c.name)) if achados else None


# cada tipo de ticket com cor e texto
TYPES = {
    "suporte": {
        "emoji": "🛠️", "label": "Suporte", "color": 0x5865F2,
        "style": discord.ButtonStyle.primary,
        "hint": "Descreva o problema ou a dúvida com o máximo de detalhes.",
    },
    "denuncia": {
        "emoji": "🚨", "label": "Denúncia", "color": 0xED4245,
        "style": discord.ButtonStyle.danger,
        "hint": "Conte quem, onde e quando aconteceu. Prints ajudam muito.",
    },
    "parceria": {
        "emoji": "🤝", "label": "Parceria", "color": 0xEB459E,
        "style": discord.ButtonStyle.success,
        "hint": "Mande o link do seu servidor e o que você propõe.",
    },
    "outros": {
        "emoji": "💬", "label": "Outros", "color": 0x99AAB5,
        "style": discord.ButtonStyle.secondary,
        "hint": "Qualquer outro assunto que precise da Staff.",
    },
}


def _load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save():
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("nao consegui salvar %s: %s", STATE_FILE, e)


state = _load()
state.setdefault('tickets', {})

# o topico do canal guarda o dono do ticket
_TOPIC_RE = re.compile(r"^ticket:(\d+)(?::(\w+))?")


def _topic_info(channel) -> Optional[tuple]:
    m = _TOPIC_RE.match(getattr(channel, "topic", None) or "")
    return (int(m.group(1)), m.group(2) or "outros") if m else None


def _data_for(channel):
    # se for ticket antigo monta o dado pelo topico
    data = state["tickets"].get(str(channel.id))
    if data is None:
        owner, tipo = _topic_info(channel) or (0, "outros")
        data = {
            "owner": owner, "tipo": tipo if tipo in TYPES else "outros", "assunto": "—",
            "descricao": "", "opened": int(channel.created_at.timestamp()),
            "claimed": None, "avatar": "",
        }
        state["tickets"][str(channel.id)] = data
        _save()
    return data


def _open_tickets(guild: discord.Guild, user_id: int) -> list:
    return [
        ch for ch in guild.text_channels
        if (info := _topic_info(ch)) is not None and info[0] == user_id
    ]


# cartao simples (container puro, sem barra colorida)
class Card(discord.ui.LayoutView):
    def __init__(self, title: str, text: str = "", *extra):
        super().__init__(timeout=None)
        body = f"### {title}" + (f"\n{text}" if text else "")
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(body), *extra))


def _quote(text, limit):
    text = text.strip()[:limit] or "—"
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


# monta o cartao do ticket
def build_ticket_view(data, ping=""):
    t = TYPES.get(data["tipo"], TYPES["outros"])
    claimed = data.get("claimed")

    header = (
        f"# {t['emoji']} {t['label']}\n"
        f"Olá, <@{data['owner']}>! Obrigado por entrar em contato.\n"
        f"Nossa equipe já foi avisada{' ' + ping if ping else ''} e vai te atender em breve."
    )
    attendant = f"🙋 <@{claimed}>" if claimed else "⏳ Aguardando atendimento"
    info = (
        f"**Categoria**  {t['emoji']} {t['label']}\n"
        f"**Aberto**  <t:{data['opened']}:R>\n"
        f"**Atendente**  {attendant}"
    )

    # foto do lado se tiver
    if data.get("avatar"):
        top = discord.ui.Section(header, accessory=discord.ui.Thumbnail(data["avatar"]))
    else:
        top = discord.ui.TextDisplay(header)

    container = discord.ui.Container(
        top,
        discord.ui.Separator(),
        discord.ui.TextDisplay(
            f"**Assunto**\n{_quote(data['assunto'], 80)}\n\n"
            f"**Detalhes**\n{_quote(data['descricao'], 1000)}"
        ),
        discord.ui.Separator(),
        discord.ui.TextDisplay(info),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large, visible=False),
        TicketButtonsRow(claimed=bool(claimed)),
        AddMemberRow(),
        discord.ui.TextDisplay("-# Use o menu acima para chamar mais alguém para este ticket."),
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


# botao de abrir ticket (fica ao lado de cada categoria)
class NewTicketButton(discord.ui.Button):
    def __init__(self, tipo: str):
        super().__init__(
            label="Abrir", style=TYPES[tipo]["style"], custom_id=f"tk:new:{tipo}"
        )
        self.tipo = tipo

    async def callback(self, interaction: discord.Interaction):
        await _start(interaction, self.tipo)


# painel: um container so, cada categoria com seu botao ao lado
def build_panel_view():
    children = []
    if BANNER_URL:
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(BANNER_URL)))
    children += [
        discord.ui.TextDisplay(
            "# 🎫 Central de Atendimento\n"
            "Precisa de ajuda, quer denunciar algo ou fechar uma parceria?\n"
            "Escolha uma categoria e abra um atendimento **privado** com a nossa equipe."
        ),
        discord.ui.Separator(),
    ]
    for tipo, t in TYPES.items():
        children.append(
            discord.ui.Section(
                f"### {t['emoji']} {t['label']}\n-# {t['hint']}",
                accessory=NewTicketButton(tipo),
            )
        )
    children += [
        discord.ui.Separator(),
        discord.ui.TextDisplay(
            "🔒  Só você e a Staff enxergam o ticket\n"
            "📄  Ao fechar, a transcrição chega na sua DM (se estiver aberta)"
        ),
        discord.ui.TextDisplay("-# Um ticket por pessoa · não abra tickets à toa"),
    ]
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children))
    return view


async def post_panel(channel: discord.TextChannel) -> discord.Message:
    return await channel.send(view=build_panel_view())


class TicketModal(discord.ui.Modal):
    assunto = discord.ui.TextInput(label="Assunto", placeholder="Resuma em poucas palavras", max_length=80)
    descricao = discord.ui.TextInput(
        label="Detalhes",
        style=discord.TextStyle.paragraph,
        placeholder="Explique com calma o que você precisa",
        max_length=1000,
        required=False,
    )

    def __init__(self, tipo: str):
        t = TYPES[tipo]
        super().__init__(title=f"{t['emoji']} {t['label']}"[:45])
        self.tipo = tipo
        self.descricao.placeholder = t["hint"][:100]

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket(interaction, self.tipo, self.assunto.value, self.descricao.value)


async def _start(interaction, tipo):
    # checa o limite antes de abrir a janela
    existing = _open_tickets(interaction.guild, interaction.user.id)
    if len(existing) >= MAX_PER_USER:
        await interaction.response.send_message(
            view=Card(
                "Você já tem um ticket aberto",
                f"Continue por aqui: {existing[0].mention}",
            ),
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(TicketModal(tipo))


async def create_ticket(interaction: discord.Interaction, tipo: str, assunto: str, descricao: str):
    await interaction.response.defer(ephemeral=True)
    guild, user = interaction.guild, interaction.user
    t = TYPES[tipo]

    # clicou duas vezes rapido
    if len(_open_tickets(guild, user.id)) >= MAX_PER_USER:
        await interaction.followup.send(view=Card("Você já tem um ticket aberto"), ephemeral=True)
        return

    staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE)
    # so o dono e a staff veem
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True, manage_messages=True
        ),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, manage_messages=True,
        )

    # nome do canal sem coisa estranha
    slug = re.sub(r"[^a-z0-9-]", "", user.name.lower())[:20] or "membro"
    try:
        channel = await guild.create_text_channel(
            f"{t['emoji']}・{tipo}-{slug}"[:100],
            category=interaction.channel.category if interaction.channel else None,
            topic=f"ticket:{user.id}:{tipo}",
            overwrites=overwrites,
            reason=f"Ticket de {user} ({tipo})",
        )
    except discord.HTTPException as e:
        log.warning("nao consegui criar o ticket: %s", e)
        await interaction.followup.send(
            view=Card("Não consegui abrir o ticket", "Avise a Staff, por favor."), ephemeral=True
        )
        return

    data = {
        "owner": user.id, "tipo": tipo, "assunto": assunto.strip(), "descricao": descricao.strip(),
        "opened": int(datetime.now().timestamp()), "claimed": None,
        "avatar": user.display_avatar.url,
    }
    # salva os dados do ticket
    state["tickets"][str(channel.id)] = data
    _save()

    await channel.send(
        view=build_ticket_view(data, ping=staff_role.mention if staff_role else ""),
        allowed_mentions=discord.AllowedMentions(
            users=[user], roles=[staff_role] if staff_role else False
        ),
    )
    await interaction.followup.send(
        view=Card(
            "Ticket criado! ✅",
            "Já pode falar com a nossa equipe.",
            discord.ui.ActionRow(discord.ui.Button(label="Ir para o ticket", url=channel.jump_url)),
        ),
        ephemeral=True,
    )


def _can_manage(member, data):
    return member.id == data["owner"] or is_staff(member)


class TicketButtonsRow(discord.ui.ActionRow):
    def __init__(self, claimed: bool = False):
        super().__init__()
        self.claim.disabled = claimed
        if claimed:
            self.claim.label = "Assumido"

    @discord.ui.button(label="Assumir ticket", emoji="🙋", style=discord.ButtonStyle.success, custom_id="tk:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        # so staff assume
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                view=Card("Só a Staff", "Apenas a equipe pode assumir tickets."), ephemeral=True
            )
            return
        data = _data_for(interaction.channel)
        if data.get("claimed"):
            await interaction.response.send_message(
                view=Card("Já assumido", f"Quem cuida deste ticket: <@{data['claimed']}>"),
                ephemeral=True,
            )
            return
        data["claimed"] = interaction.user.id
        _save()
        await interaction.response.edit_message(view=build_ticket_view(data))
        await interaction.followup.send(
            view=Card("🙋 Ticket assumido", f"{interaction.user.mention} vai cuidar do seu atendimento."),
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(data["owner"])]),
        )

    @discord.ui.button(label="Fechar", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="tk:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = _data_for(interaction.channel)
        if not _can_manage(interaction.user, data):
            await interaction.response.send_message(
                view=Card("Sem permissão", "Só a Staff ou quem abriu pode fechar."), ephemeral=True
            )
            return
        await interaction.response.send_message(view=ConfirmCloseView(), ephemeral=True)


class AddMemberRow(discord.ui.ActionRow):
    @discord.ui.select(
        cls=discord.ui.UserSelect,
        custom_id="tk:add",
        placeholder="➕ Adicionar alguém ao ticket",
        min_values=1,
        max_values=1,
    )
    async def add(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        data = _data_for(interaction.channel)
        if not _can_manage(interaction.user, data):
            await interaction.response.send_message(
                view=Card("Sem permissão", "Só a Staff ou quem abriu pode chamar alguém."),
                ephemeral=True,
            )
            return
        member = select.values[0]
        if member.bot:
            await interaction.response.send_message(
                view=Card("Isso é um bot 🤖", "Escolha uma pessoa."), ephemeral=True
            )
            return
        await interaction.channel.set_permissions(
            member, view_channel=True, send_messages=True, read_message_history=True, attach_files=True
        )
        # recria o cartao pra limpar o menu
        await interaction.response.edit_message(view=build_ticket_view(data))
        await interaction.followup.send(
            view=Card("➕ Membro adicionado", f"{member.mention} agora enxerga este ticket."),
            allowed_mentions=discord.AllowedMentions(users=[member]),
        )


class ConfirmCloseRow(discord.ui.ActionRow):
    @discord.ui.button(label="Fechar ticket", emoji="🔒", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=Card("Fechando...", "Gerando a transcrição."))
        await close_ticket(interaction.channel, interaction.user)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=Card("Tudo certo", "O ticket continua aberto."))


class ConfirmCloseView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "### Fechar este ticket?\n"
                    "O canal será apagado e a transcrição da conversa será enviada."
                ),
                ConfirmCloseRow(),
            )
        )


async def build_transcript(channel, data):
    stamp = lambda dt: dt.astimezone(TZ).strftime("%d/%m/%Y %H:%M")
    t = TYPES.get(data["tipo"], TYPES["outros"])
    lines = [
        f"Transcrição de #{channel.name}",
        f"Categoria: {t['label']}",
        f"Assunto: {data['assunto']}",
        f"Detalhes: {data['descricao'] or '—'}",
        "=" * 50,
    ]
    async for msg in channel.history(limit=1000, oldest_first=True):
        text = msg.content
        if msg.attachments:
            text += "".join(f"\n   [anexo] {a.url}" for a in msg.attachments)
        if not text.strip():
            continue  # cartao do bot nao tem texto
        lines.append(f"[{stamp(msg.created_at)}] {msg.author.display_name}: {text}")
    return "\n".join(lines)


async def close_ticket(channel, closer):
    data = _data_for(channel)
    # evita fechar duas vezes
    if data.get('closing'):
        return
    data["closing"] = True

    transcript = await build_transcript(channel, data)
    safe = re.sub(r"[^a-z0-9-]", "", channel.name.lower()) or f"ticket-{channel.id}"
    filename = f"transcricao-{safe}.txt"
    mk_file = lambda: discord.File(io.BytesIO(transcript.encode("utf-8")), filename=filename)

    log_channel = find_channel(channel.guild, LOG_HINT)
    if log_channel is not None:
        try:
            await log_channel.send(
                f"📄 Transcrição de **{channel.name}** · aberto por <@{data['owner']}> · "
                f"fechado por {closer.mention}",
                file=mk_file(),
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException as e:
            log.warning("nao consegui mandar a transcricao pro log: %s", e)

    owner = channel.guild.get_member(data["owner"])
    if owner is not None:
        try:
            await owner.send(
                f"📄 Aqui está a transcrição do seu ticket em **{channel.guild.name}**.",
                file=mk_file(),
            )
        except discord.HTTPException:
            pass  # dm fechada

    try:
        await channel.send(
            view=Card(
                "🔒 Ticket fechado",
                f"Fechado por {closer.mention}. Este canal será apagado em **{CLOSE_DELAY} segundos**.",
            ),
            allowed_mentions=NO_MENTIONS,
        )
    except discord.HTTPException:
        pass

    await asyncio.sleep(CLOSE_DELAY)
    state["tickets"].pop(str(channel.id), None)
    _save()
    try:
        await channel.delete(reason=f"Ticket fechado por {closer}")
    except discord.HTTPException as e:
        log.warning("nao consegui apagar o ticket %s: %s", channel.name, e)


# botoes continuam depois de reiniciar
def register_views(client):
    client.add_view(build_panel_view())
    client.add_view(
        build_ticket_view({"owner": 0, "tipo": "outros", "assunto": "", "descricao": "", "opened": 0})
    )


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        register_views(self.bot)

    @commands.command(
        name="painelticket",
        aliases=("ticketpainel",),
        extras={
            "categoria": "Utilidades",
            "uso": ",painelticket",
            "descricao": "Posta o painel de tickets no canal atual.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_channels=True, send_messages=True)
    async def painelticket(self, ctx):
        await post_panel(ctx.channel)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Tickets(bot))
