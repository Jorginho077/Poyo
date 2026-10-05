# cog de tickets com components v2 - totalmente configuravel por servidor
import os
import io
import re
import copy
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
CONFIG_FILE = Path(os.getenv("TICKETS_CONFIG_FILE", "tickets_config.json"))
TZ = ZoneInfo(os.getenv("TIMEZONE", "America/Sao_Paulo"))
# banner padrao do painel (vai junto como anexo)
BANNER_FILE = Path(__file__).resolve().parent.parent / "assets" / "banner.png"
BANNER_NAME = "banner.png"


def _env_int(name, default):
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


# ---------------------------------------------------------------- arquivos

def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        # nao sobrescreve um arquivo quebrado: guarda uma copia
        bak = path.with_name(path.name + ".bak")
        log.error("%s ilegivel (%s); copia guardada em %s", path, e, bak)
        try:
            path.replace(bak)
        except OSError:
            pass
        return {}


def _write_json(path: Path, data: dict):
    # escreve num arquivo temporario e troca, pra nao corromper se cair no meio
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.warning("nao consegui salvar %s: %s", path, e)


# ---------------------------------------------------------------- padroes

BUTTON_STYLES = {
    "azul": discord.ButtonStyle.primary,
    "cinza": discord.ButtonStyle.secondary,
    "verde": discord.ButtonStyle.success,
    "vermelho": discord.ButtonStyle.danger,
}

COLOR_NAMES = {
    "azul": 0x5865F2, "vermelho": 0xED4245, "verde": 0x57F287, "rosa": 0xEB459E,
    "amarelo": 0xFEE75C, "laranja": 0xE67E22, "roxo": 0x9B59B6, "cinza": 0x99AAB5,
    "preto": 0x000000, "branco": 0xFFFFFF,
}

# campos de uma categoria (o que faltar no arquivo cai aqui)
CAT_BLANK = {
    "emoji": "💬", "label": "Categoria", "hint": "", "botao": "cinza",
    "cor": None, "staff": None, "categoria": None, "boasvindas": None,
}

CAT_DEFAULTS = {
    "suporte": {
        **CAT_BLANK, "emoji": "🛠️", "label": "Suporte", "botao": "azul",
        "hint": "Descreva o problema ou a dúvida com o máximo de detalhes.",
    },
    "denuncia": {
        **CAT_BLANK, "emoji": "🚨", "label": "Denúncia", "botao": "vermelho",
        "hint": "Conte quem, onde e quando aconteceu. Prints ajudam muito.",
    },
    "parceria": {
        **CAT_BLANK, "emoji": "🤝", "label": "Parceria", "botao": "verde",
        "hint": "Mande o link do seu servidor e o que você propõe.",
    },
    "outros": {
        **CAT_BLANK, "emoji": "💬", "label": "Outros", "botao": "cinza",
        "hint": "Qualquer outro assunto que precise da Staff.",
    },
}

DEFAULTS = {
    "painel": {
        "titulo": "🎫 Central de Atendimento",
        "descricao": (
            "Precisa de ajuda, quer denunciar algo ou fechar uma parceria?\n"
            "Escolha uma categoria e abra um atendimento **privado** com a nossa equipe."
        ),
        "info": (
            "-# 🔒  Só você e a Staff enxergam o ticket\n"
            "-# 📄  Ao fechar, a transcrição chega na sua DM (se estiver aberta)"
        ),
        "rodape": "Um ticket por pessoa · não abra tickets à toa",
        # None = banner padrao do bot · "" = sem banner · link = imagem propria
        "banner": os.getenv("TICKET_BANNER_URL", "").strip() or None,
        "cor": None,
        "modo": "botoes",          # botoes | menu
        "botao": "",               # vazio = nome da categoria (no menu: texto do seletor)
    },
    "ticket": {
        "boas_vindas": (
            "Olá, {usuario}! Obrigado por entrar em contato.\n"
            "Nossa equipe já foi avisada{staff} e vai te atender em breve."
        ),
        "rodape": "Use o menu acima para chamar mais alguém para este ticket.",
        "nome_canal": "{emoji}・{tipo}-{usuario}",
        "cor": None,
    },
    "modal": {"assunto": "Assunto", "detalhes": "Detalhes"},
    "staff": os.getenv("TICKET_STAFF_ROLE", "Staff"),   # id do cargo ou nome
    "log": os.getenv("TICKET_LOG_CHANNEL", "chat-staff"),  # id do canal ou pedaco do nome
    "categoria": None,         # categoria do discord onde os tickets nascem
    "limite": max(1, _env_int("TICKET_MAX_PER_USER", 1)),
    "delay": 10,               # segundos ate apagar o canal
    "transcricao_dm": True,
    "transcricao_log": True,
    "categorias": CAT_DEFAULTS,
    "paineis": [],             # [[canal, mensagem], ...] pra atualizar sozinho
}

MAX_CATEGORIAS = 10

# ---------------------------------------------------------------- config

_cfg_store = _read_json(CONFIG_FILE)
_cfg_store.setdefault("guilds", {})


def _save_cfg():
    _write_json(CONFIG_FILE, _cfg_store)


def _merge(base: dict, over: dict):
    for k, v in over.items():
        if k == "categorias":
            base[k] = copy.deepcopy(v)  # categorias sao trocadas por inteiro
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)


def get_cfg(guild_id) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    _merge(cfg, _cfg_store["guilds"].get(str(guild_id), {}))
    cfg["categorias"] = {k: {**CAT_BLANK, **c} for k, c in cfg["categorias"].items()}
    return cfg


def _over(guild_id) -> dict:
    return _cfg_store["guilds"].setdefault(str(guild_id), {})


def set_path(guild_id, path, value):
    d = _over(guild_id)
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value
    _save_cfg()


def _cats_over(guild_id) -> dict:
    over = _over(guild_id)
    if "categorias" not in over:
        over["categorias"] = copy.deepcopy(CAT_DEFAULTS)
    return over["categorias"]


def cat_of(cfg, tipo) -> dict:
    # categoria apagada: tickets antigos continuam funcionando
    return cfg["categorias"].get(tipo) or {**CAT_BLANK, "label": str(tipo).title()}


def _ui_emoji(e: str):
    # so devolve emoji valido pro Discord (custom ou unicode); texto comum vira None
    e = (e or "").strip()
    if re.fullmatch(r"<a?:\w+:\d{13,20}>", e):
        return discord.PartialEmoji.from_str(e)
    return e if e and not re.search(r"[A-Za-z0-9]", e) and len(e) <= 16 else None


def render(text: str, **vars) -> str:
    # troca {chave} sem quebrar com chaves soltas
    return re.sub(r"\{(\w+)\}", lambda m: str(vars.get(m.group(1), m.group(0))), text or "")


# ---------------------------------------------------------------- staff

def _role(guild, ref):
    if isinstance(ref, int):
        return guild.get_role(ref)
    if isinstance(ref, str) and ref:
        return discord.utils.get(guild.roles, name=ref)
    return None


def staff_roles(guild, cfg, tipo=None) -> list:
    refs = [cfg.get("staff")]
    if tipo:
        refs.append(cat_of(cfg, tipo).get("staff"))
    roles = []
    for ref in refs:
        r = _role(guild, ref)
        if r is not None and r not in roles:
            roles.append(r)
    return roles


# staff e quem tem admin ou gerenciar mensagens ou o cargo
def is_staff(member, cfg, tipo=None):
    perms = member.guild_permissions
    if perms.administrator or perms.manage_messages:
        return True
    ids = {r.id for r in staff_roles(member.guild, cfg, tipo)}
    return any(r.id in ids for r in member.roles)


# acha canal por id ou por pedaco do nome
def find_channel(guild, ref):
    if isinstance(ref, int):
        ch = guild.get_channel(ref)
        return ch if isinstance(ch, discord.TextChannel) else None
    if not ref:
        return None
    key = str(ref).lower()
    achados = [c for c in guild.text_channels if key in c.name.lower()]
    return min(achados, key=lambda c: len(c.name)) if achados else None


# ---------------------------------------------------------------- estado dos tickets

def _load() -> dict:
    return _read_json(STATE_FILE)


def _save():
    _write_json(STATE_FILE, state)


state = _load()
state.setdefault("tickets", {})

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
            "owner": owner, "tipo": tipo, "assunto": "—",
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


# ---------------------------------------------------------------- visual

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
def build_ticket_view(data, cfg, ping=""):
    t = cat_of(cfg, data["tipo"])
    claimed = data.get("claimed")

    welcome = t.get("boasvindas") or cfg["ticket"]["boas_vindas"]
    welcome = render(
        welcome,
        usuario=f"<@{data['owner']}>", staff=f" {ping}" if ping else "",
        categoria=t["label"], emoji=t["emoji"],
    )
    header = f"# {t['emoji']} {t['label']}\n{welcome}"
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

    children = [
        top,
        discord.ui.Separator(),
        discord.ui.TextDisplay(
            f"**{cfg['modal']['assunto']}**\n{_quote(data['assunto'], 80)}\n\n"
            f"**{cfg['modal']['detalhes']}**\n{_quote(data['descricao'], 1000)}"
        ),
        discord.ui.Separator(),
        discord.ui.TextDisplay(info),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large, visible=False),
        TicketButtonsRow(claimed=bool(claimed)),
        AddMemberRow(),
    ]
    if cfg["ticket"].get("rodape"):
        children.append(discord.ui.TextDisplay(f"-# {cfg['ticket']['rodape']}"))

    accent = t.get("cor") or cfg["ticket"].get("cor")
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_colour=accent))
    return view


# botao de abrir ticket. o id leva o nome da categoria, entao funciona
# em qualquer painel e continua valendo depois de reiniciar
class NewTicketButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"tk:new:(?P<tipo>[a-z0-9_]+)",
):
    def __init__(self, tipo: str, label: str = "Abrir", style: str = "cinza", emoji=None):
        super().__init__(
            discord.ui.Button(
                label=label[:80] or "Abrir",
                style=BUTTON_STYLES.get(style, discord.ButtonStyle.secondary),
                emoji=emoji,
                custom_id=f"tk:new:{tipo}",
            )
        )
        self.tipo = tipo

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["tipo"])

    async def callback(self, interaction: discord.Interaction):
        await _start(interaction, self.tipo)


# modo menu: um seletor com todas as categorias
class PanelSelect(discord.ui.Select):
    def __init__(self, cfg=None):
        options, placeholder = [], "Escolha uma categoria…"
        if cfg:
            placeholder = (cfg["painel"].get("botao") or placeholder)[:150]
            for tipo, c in cfg["categorias"].items():
                options.append(
                    discord.SelectOption(
                        label=c["label"][:100], value=tipo, emoji=_ui_emoji(c["emoji"]),
                        description=(c.get("hint") or "")[:100] or None,
                    )
                )
        super().__init__(
            custom_id="tk:panel",
            placeholder=placeholder,
            options=options or [discord.SelectOption(label="…", value="outros")],
        )

    async def callback(self, interaction: discord.Interaction):
        await _start(interaction, self.values[0])


def _banner_url(p):
    # None = banner padrao (anexo) · "" = sem banner · link = imagem propria
    if p.get("banner"):
        return p["banner"]
    if p.get("banner") is None and BANNER_FILE.exists():
        return f"attachment://{BANNER_NAME}"
    return None


def panel_files(cfg) -> list:
    if cfg["painel"].get("banner") is None and BANNER_FILE.exists():
        return [discord.File(BANNER_FILE, filename=BANNER_NAME)]
    return []


# painel: banner, titulo, lista das categorias e os botoes (ou menu) embaixo
def build_panel_view(cfg):
    p = cfg["painel"]
    children = []
    banner = _banner_url(p)
    if banner:
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(banner)))

    head = f"# {p['titulo']}" if p.get("titulo") else ""
    if p.get("descricao"):
        head = (head + "\n" if head else "") + p["descricao"]
    if head:
        children.append(discord.ui.TextDisplay(head))

    cats = cfg["categorias"]
    lines = []
    for c in cats.values():
        lines.append(f"{c['emoji']}  **{c['label']}**" + (f"\n-# {c['hint']}" if c.get("hint") else ""))
    if lines:
        children += [discord.ui.Separator(), discord.ui.TextDisplay("\n".join(lines))]

    children.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large, visible=False))
    if p.get("modo") == "menu":
        children.append(discord.ui.ActionRow(PanelSelect(cfg)))
    else:
        fixo = p.get("botao") or ""
        botoes = [
            NewTicketButton(tipo, fixo or c["label"], c["botao"], _ui_emoji(c["emoji"]))
            for tipo, c in cats.items()
        ]
        for i in range(0, len(botoes), 5):
            children.append(discord.ui.ActionRow(*botoes[i:i + 5]))

    if p.get("info"):
        children += [discord.ui.Separator(), discord.ui.TextDisplay(p["info"])]
    if p.get("rodape"):
        children.append(discord.ui.TextDisplay(f"-# {p['rodape']}"))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_colour=p.get("cor")))
    return view


async def post_panel(channel: discord.TextChannel) -> discord.Message:
    cfg = get_cfg(channel.guild.id)
    msg = await channel.send(view=build_panel_view(cfg), files=panel_files(cfg))
    paineis = cfg["paineis"]
    paineis.append([channel.id, msg.id])
    set_path(channel.guild.id, ("paineis",), paineis)
    return msg


async def refresh_panels(guild: discord.Guild) -> int:
    # reedita todos os paineis ja postados com a config nova
    cfg = get_cfg(guild.id)
    view_ok, mantidos = 0, []
    for cid, mid in cfg["paineis"]:
        ch = guild.get_channel(cid)
        if ch is None:
            continue
        try:
            await ch.get_partial_message(mid).edit(
                view=build_panel_view(cfg), attachments=panel_files(cfg)
            )
            view_ok += 1
            mantidos.append([cid, mid])
        except discord.NotFound:
            continue  # painel apagado
        except discord.HTTPException as e:
            log.warning("nao consegui atualizar o painel %s: %s", mid, e)
            mantidos.append([cid, mid])
    if mantidos != cfg["paineis"]:
        set_path(guild.id, ("paineis",), mantidos)
    return view_ok


# ---------------------------------------------------------------- abrir ticket

class TicketModal(discord.ui.Modal):
    assunto = discord.ui.TextInput(label="Assunto", placeholder="Resuma em poucas palavras", max_length=80)
    descricao = discord.ui.TextInput(
        label="Detalhes",
        style=discord.TextStyle.paragraph,
        placeholder="Explique com calma o que você precisa",
        max_length=1000,
        required=False,
    )

    def __init__(self, tipo: str, cfg: dict):
        t = cat_of(cfg, tipo)
        super().__init__(title=f"{t['emoji']} {t['label']}"[:45])
        self.tipo = tipo
        self.assunto.label = cfg["modal"]["assunto"][:45]
        self.descricao.label = cfg["modal"]["detalhes"][:45]
        self.descricao.placeholder = (t.get("hint") or "")[:100] or None

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket(interaction, self.tipo, self.assunto.value, self.descricao.value)


async def _start(interaction, tipo):
    cfg = get_cfg(interaction.guild.id)
    if tipo not in cfg["categorias"]:
        await interaction.response.send_message(
            view=Card("Categoria indisponível", "Essa opção não existe mais."), ephemeral=True
        )
        return
    # checa o limite antes de abrir a janela
    existing = _open_tickets(interaction.guild, interaction.user.id)
    if len(existing) >= cfg["limite"]:
        await interaction.response.send_message(
            view=Card("Você já tem um ticket aberto", f"Continue por aqui: {existing[0].mention}"),
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(TicketModal(tipo, cfg))


def _channel_name(cfg, t, tipo, user) -> str:
    slug = re.sub(r"[^a-z0-9-]", "", user.name.lower())[:20] or "membro"
    emoji = "" if t["emoji"].startswith("<") else t["emoji"]  # emoji custom nao vale em nome
    name = render(cfg["ticket"]["nome_canal"], emoji=emoji, tipo=tipo, usuario=slug, id=user.id)
    name = re.sub(r"\s+", "-", name.strip().lower())[:100]
    return name or f"{tipo}-{slug}"


async def create_ticket(interaction: discord.Interaction, tipo: str, assunto: str, descricao: str):
    await interaction.response.defer(ephemeral=True)
    guild, user = interaction.guild, interaction.user
    cfg = get_cfg(guild.id)
    t = cfg["categorias"].get(tipo)
    if t is None:
        await interaction.followup.send(
            view=Card("Categoria indisponível", "Essa opção não existe mais."), ephemeral=True
        )
        return

    # clicou duas vezes rapido
    if len(_open_tickets(guild, user.id)) >= cfg["limite"]:
        await interaction.followup.send(view=Card("Você já tem um ticket aberto"), ephemeral=True)
        return

    roles = staff_roles(guild, cfg, tipo)
    # so o dono e a staff veem
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manag
