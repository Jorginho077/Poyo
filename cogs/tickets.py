# cog de tickets com components v2 - totalmente configuravel por servidor
# versao coquette: tema rosa pastel, kaomoji, decoracoes 🌸
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
    # escreve num arquivo temporario e troca pra nao corromper se cair no meio
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
    "rosa": discord.ButtonStyle.primary,  # alias bonito pro tema coquette
}

COLOR_NAMES = {
    "azul": 0x5865F2, "vermelho": 0xED4245, "verde": 0x57F287, "rosa": 0xEB459E,
    "amarelo": 0xFEE75C, "laranja": 0xE67E22, "roxo": 0x9B59B6, "cinza": 0x99AAB5,
    "preto": 0x000000, "branco": 0xFFFFFF,
    # tons coquette
    "rosa_pastel": 0xF7C8D8, "rosa_claro": 0xFBD5E3, "lilas": 0xE6D7F5,
    "creme": 0xF8E8E0, "nude": 0xEAD7C8,
}

# paleta coquette (rosa pastel da barra lateral)
COQUETTE_PINK = 0xF7C8D8
COQUETTE_SOFT = 0xFBD5E3

# campos de uma categoria (o que falatr no arquivo cai aqui)
CAT_BLANK = {
    "emoji": "", "label": "Categoria", "hint": "", "botao": "cinza",
    "cor": None, "staff": None, "categoria": None, "boasvindas": None,
}

CAT_DEFAULTS = {
    "suporte": {
        **CAT_BLANK, "emoji": "🌸", "label": "Suporte",
        "cor": COQUETTE_PINK,
        "hint": "se precisar de ajuda com algo ♡",
    },
    "denuncia": {
        **CAT_BLANK, "emoji": "🎀", "label": "Denúncia",
        "cor": 0xEAD7C8,
        "hint": "reportar alguém ou algum erro",
    },
    "parceria": {
        **CAT_BLANK, "emoji": "💌", "label": "Parceria",
        "cor": 0xE6D7F5,
        "hint": "propor parceria do seu servidor",
    },
    "outros": {
        **CAT_BLANK, "emoji": "🧸", "label": "Outros",
        "cor": COQUETTE_SOFT,
        "hint": "qualquer outro assunto",
    },
}

DEFAULTS = {
    "painel": {
        "titulo": "Central de Atendimento",
        "descricao": (
            "selecione uma opção abaixo para abrir\n"
            "um atendimento privado com a nossa equipe ♡"
        ),
        "info": (
            "-# 🌷 somente você e a equipe têm acesso ao canal\n"
            "-# 📜 a transcrição é enviada na sua DM ao encerrar"
        ),
        "rodape": "um ticket por pessoa  ·  ♡",
        # none = banner padrao do bot · "" = sem banner · link = imagem propria
        "banner": os.getenv(
            "TICKET_BANNER_URL",
            "https://media.base44.com/images/public/6ac4034e14bc929d3a3e823e/7314ef2f6_generated_image.png",
        ).strip(),
        "cor": COQUETTE_PINK,
        "modo": "botoes",          # botoes | menu
        "botao": "",               # vazio = nome da categoria (no menu: texto do seletor)
    },
    "ticket": {
        "boas_vindas": (
            "Olá {usuario}  ( ´ ｀ )  🌸\n"
            "recebemos a sua solicitação ♡\n"
            "a nossa equipe já foi avisada{staff} e responderá em breve ˚ ༘♡"
        ),
        "rodape": "use o menu acima para assumir, adicionar alguém ou encerrar  ˚ ༘♡ ⋆｡˚",
        "nome_canal": "{tipo}-{usuario}",
        "cor": COQUETTE_PINK,
    },
    "modal": {"assunto": "Assunto", "detalhes": "Detalhes"},
    "staff": os.getenv("TICKET_STAFF_ROLE", "Staff"),   # id do cargo ou nome
    "log": os.getenv("TICKET_LOG_CHANNEL", "chat-staff"),  # id do canal ou pedaco do nome
    "categoria": None,         # categoria do discord onde os ticets nascem
    "limite": max(1, _env_int("TICKET_MAX_PER_USER", 1)),
    "delay": 10,               # segundos ate apagar o canal
    "transcricao_dm": True,
    "transcricao_log": True,
    "categorias": CAT_DEFAULTS,
    "paineis": [],             # [[canal mensagem] ] pra atualizar sozinho
}

MAX_CATEGORIAS = 10

# decoracao coquette: separador de rosas + fitas com coraçõezinhos pendurados
ROSE_DIVIDER = "꒰ᨳ᭬ᨳ꒱  ˚ ༘♡ ⋆｡˚  ꒰ᨳ᭬ᨳ꒱"

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
    # categoria apagada: tickets antgios continam funcionando
    return cfg["categorias"].get(tipo) or {**CAT_BLANK, "label": str(tipo).title()}


def _ui_emoji(e: str):
    # so devolve emoji valido pro discord (custom ou unicode) texto comum vira none
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

# cartao simples (container puro sem barra colorida)
class Card(discord.ui.LayoutView):
    def __init__(self, title: str, text: str = "", *extra):
        super().__init__(timeout=None)
        body = f"### {title}" + (f"\n{text}" if text else "")
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(body), *extra))


def _quote(text, limit):
    text = text.strip()[:limit] or "—"
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


# icone so aparece se a categoria tiver emoji
def _ico(c) -> str:
    return f"{c['emoji']} " if c.get("emoji") else ""


# um menu so com as acoes do ticket (assumir / adicionar / encerrar)
class TicketMenu(discord.ui.Select):
    def __init__(self, claimed: bool = False):
        ops = []
        if not claimed:
            ops.append(discord.SelectOption(
                label="Assumir ticket", value="claim", emoji="🙋",
                description="Você passa a cuidar deste atendimento"))
        ops.append(discord.SelectOption(
            label="Adicionar membro", value="add", emoji="➕",
            description="Dá acesso a mais uma pessoa"))
        ops.append(discord.SelectOption(
            label="Encerrar ticket", value="close", emoji="🔒",
            description="Fecha o canal e envia a transcrição"))
        super().__init__(custom_id="tk:menu", placeholder="🌸 ações do ticket", options=ops)

    async def callback(self, interaction: discord.Interaction):
        await _menu_action(interaction, self.values[0])


# monta o cartao do ticket no estilo coquette (blocos em camadas com barra rosa)
def build_ticket_view(data, cfg, ping=""):
    t = cat_of(cfg, data["tipo"])
    claimed = data.get("claimed")

    welcome = t.get("boasvindas") or cfg["ticket"]["boas_vindas"]
    welcome = render(
        welcome,
        usuario=f"<@{data['owner']}>", staff=f" {ping}" if ping else "",
        categoria=t["label"], emoji=t["emoji"],
    )
    accent = t.get("cor") or cfg["ticket"].get("cor") or COQUETTE_PINK

    # bloco 1: cabecalho (titulo + kaomoji + boas-vindas)
    header = f"# {_ico(t)}{t['label']}  ( ´ ｀ )\n{welcome}"
    if data.get("avatar"):
        top = discord.ui.Section(header, accessory=discord.ui.Thumbnail(data["avatar"]))
    else:
        top = discord.ui.TextDisplay(header)

    attendant = f"<@{claimed}>" if claimed else "aguardando  ᶻᶻ"
    info = f"-# 🌷 aberto <t:{data['opened']}:R>  ·  atendente: {attendant}"

    # bloco 2: motivos (assunto + detalhes no estilo "reasons")
    reasons_body = (
        f"### 🌸 motivos\n"
        f"`1`  ·  **{cfg['modal']['assunto']}**\n{_quote(data['assunto'], 80)}\n\n"
        f"`2`  ·  **{cfg['modal']['detalhes']}**\n{_quote(data['descricao'], 1000)}"
    )

    children = [
        top,
        discord.ui.Separator(),
        discord.ui.TextDisplay(reasons_body),
        discord.ui.Separator(),
        discord.ui.TextDisplay(info),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large, visible=False),
        discord.ui.ActionRow(TicketMenu(claimed=bool(claimed))),
    ]
    if cfg["ticket"].get("rodape"):
        children.append(discord.ui.TextDisplay(f"-# {cfg['ticket']['rodape']}"))
    # separador decorativo de rosas no rodape
    children.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large, visible=False))
    children.append(discord.ui.TextDisplay(f"-# {ROSE_DIVIDER}"))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_colour=accent))
    return view


# botao de abrir ticket o id leva o nome da categoria entao funciona
# em qlqr painel e continua valendo dps de reiniciar
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
        options, placeholder = [], "🌸 selecione o tipo de atendimento"
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
    # none = banner padrao (anexo) · "" = sem banner · link = imagem propria
    if p.get("banner"):
        return p["banner"]
    if p.get("banner") is None and BANNER_FILE.exists():
        return f"attachment://{BANNER_NAME}"
    return None


def panel_files(cfg) -> list:
    if cfg["painel"].get("banner") is None and BANNER_FILE.exists():
        return [discord.File(BANNER_FILE, filename=BANNER_NAME)]
    return []


# painel: banner titulo lista das categorias e os botoes (ou menu) embaixo
def build_panel_view(cfg):
    p = cfg["painel"]
    children = []
    banner = _banner_url(p)
    if banner:
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(banner)))

    # faixa decorativa de fitas no topo
    children.append(discord.ui.TextDisplay("-# 🎀  🎀  🎀  🎀  🎀"))

    head = f"# {p['titulo']}" if p.get("titulo") else ""
    if p.get("descricao"):
        head = (head + "\n" if head else "") + p["descricao"]
    if head:
        children.append(discord.ui.TextDisplay(head))

    cats = cfg["categorias"]
    lines = []
    for i, c in enumerate(cats.values(), 1):
        lines.append(f"`{i:02d}`  {_ico(c)}**{c['label']}**" + (f"\n-# {c['hint']}" if c.get("hint") else ""))
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
    # separador decorativo de rosas no rodape do painel
    children.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large, visible=False))
    children.append(discord.ui.TextDisplay(f"-# {ROSE_DIVIDER}"))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_colour=p.get("cor") or COQUETTE_PINK))
    return view


async def post_panel(channel: discord.TextChannel) -> discord.Message:
    cfg = get_cfg(channel.guild.id)
    msg = await channel.send(view=build_panel_view(cfg), files=panel_files(cfg))
    paineis = cfg["paineis"]
    paineis.append([channel.id, msg.id])
    set_path(channel.guild.id, ("paineis",), paineis)
    return msg


async def refresh_panels(guild: discord.Guild) -> int:
    # reedita todos os paineis ja postados com a conifg nova
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
    assunto = discord.ui.TextInput(label="Assunto", placeholder="Resuma em poucas palavras ♡", max_length=80)
    descricao = discord.ui.TextInput(
        label="Detalhes",
        style=discord.TextStyle.paragraph,
        placeholder="Explique com calma o que você precisa 🌸",
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
            view=Card("Categoria indisponível", "Essa opção não existe mais."),
            ephemeral=True
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
            view_channel=True, send_messages=True, manage_channels=True, manage_messages=True
        ),
    }
    for r in roles:
        overwrites[r] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, manage_messages=True,
        )

    # categoria do discord: a da categoria de ticket dps a geral dps a do painel
    parent = None
    for ref in (t.get("categoria"), cfg.get("categoria")):
        found = guild.get_channel(ref) if isinstance(ref, int) else None
        if isinstance(found, discord.CategoryChannel):
            parent = found
            break
    if parent is None and interaction.channel:
        parent = interaction.channel.category

    try:
        channel = await guild.create_text_channel(
            _channel_name(cfg, t, tipo, user),
            category=parent,
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
        view=build_ticket_view(data, cfg, ping=" ".join(r.mention for r in roles)),
        allowed_mentions=discord.AllowedMentions(users=[user], roles=roles or False),
    )
    await interaction.followup.send(
        view=Card("Ticket criado 🌸", f"Continue o atendimento em {channel.mention}."),
        ephemeral=True,
    )


# ---------------------------------------------------------------- dentro do ticket

def _can_manage(member, data, cfg):
    return member.id == data["owner"] or is_staff(member, cfg, data["tipo"])


async def _menu_action(interaction: discord.Interaction, action: str):
    cfg = get_cfg(interaction.guild.id)
    data = _data_for(interaction.channel)
    user = interaction.user
    erro = None
    if action == "claim":
        if not is_staff(user, cfg, data["tipo"]):
            erro = ("Apenas a equipe", "Somente a equipe pode assumir tickets.")
        elif data.get("claimed"):
            erro = ("Já assumido", f"Atendente responsável: <@{data['claimed']}>")
        else:
            data["claimed"] = user.id
            _save()
    elif not _can_manage(user, data, cfg):
        erro = ("Sem permissão", "Somente a equipe ou quem abriu o ticket pode usar esta ação.")

    # recria o cartao: isso limpa a opcao escolhida no menu
    await interaction.response.edit_message(view=build_ticket_view(data, cfg))

    if erro:
        await interaction.followup.send(view=Card(*erro), ephemeral=True)
    elif action == "claim":
        await interaction.followup.send(
            view=Card("Ticket assumido 🙋", f"{user.mention} será o responsável pelo seu atendimento."),
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(data["owner"])]),
        )
    elif action == "add":
        await interaction.followup.send(view=AddMemberView(), ephemeral=True)
    elif action == "close":
        await interaction.followup.send(view=ConfirmCloseView(), ephemeral=True)


class AddMemberPick(discord.ui.ActionRow):
    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Selecione a pessoa ♡", min_values=1, max_values=1)
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        member = select.values[0]
        if member.bot:
            await interaction.response.edit_message(view=Card("Seleção inválida", "Escolha uma pessoa, não um bot."))
            return
        await interaction.channel.set_permissions(
            member, view_channel=True, send_messages=True, read_message_history=True, attach_files=True
        )
        await interaction.response.edit_message(view=Card("Membro adicionado 🌸", f"{member.mention} agora tem acesso."))
        await interaction.channel.send(
            view=Card("Membro adicionado 🌸", f"{member.mention} agora tem acesso a este ticket."),
            allowed_mentions=discord.AllowedMentions(users=[member]),
        )


class AddMemberView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay("### Adicionar membro 🌸\nQuem você quer incluir neste ticket?"),
            AddMemberPick(),
        ))


# --- compatibilidade: tickets antigos ainda tem os botoes (so respondem, nao sao mais criados)
class TicketButtonsRow(discord.ui.ActionRow):
    def __init__(self, claimed: bool = False):
        super().__init__()
        self.claim.disabled = claimed
        if claimed:
            self.claim.label = "Assumido"

    @discord.ui.button(label="Assumir ticket", emoji="🙋", style=discord.ButtonStyle.success, custom_id="tk:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_cfg(interaction.guild.id)
        data = _data_for(interaction.channel)
        # so staff assume
        if not is_staff(interaction.user, cfg, data["tipo"]):
            await interaction.response.send_message(
                view=Card("Só a Staff", "Apenas a equipe pode assumir tickets."), ephemeral=True
            )
            return
        if data.get("claimed"):
            await interaction.response.send_message(
                view=Card("Já assumido", f"Quem cuida deste ticket: <@{data['claimed']}>"),
                ephemeral=True,
            )
            return
        data["claimed"] = interaction.user.id
        _save()
        await interaction.response.edit_message(view=build_ticket_view(data, cfg))
        await interaction.followup.send(
            view=Card("🙋 Ticket assumido", f"{interaction.user.mention} vai cuidar do seu atendimento."),
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(data["owner"])]),
        )

    @discord.ui.button(label="Fechar", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="tk:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_cfg(interaction.guild.id)
        data = _data_for(interaction.channel)
        if not _can_manage(interaction.user, data, cfg):
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
        cfg = get_cfg(interaction.guild.id)
        data = _data_for(interaction.channel)
        if not _can_manage(interaction.user, data, cfg):
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
        await interaction.response.edit_message(view=build_ticket_view(data, cfg))
        await interaction.followup.send(
            view=Card("➕ Membro adicionado", f"{member.mention} agora enxerga este ticket."),
            allowed_mentions=discord.AllowedMentions(users=[member]),
        )


class ConfirmCloseSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Confirmar ♡",
            options=[
                discord.SelectOption(label="Encerrar ticket", value="yes", emoji="🔒", description="Apaga o canal e envia a transcrição"),
                discord.SelectOption(label="Manter aberto", value="no", emoji="🌸", description="Cancela e continua o atendimento"),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "yes":
            await interaction.response.edit_message(view=Card("Encerrando 🌸", "Gerando a transcrição."))
            await close_ticket(interaction.channel, interaction.user)
        else:
            await interaction.response.edit_message(view=Card("Tudo certo 🌸", "O ticket continua aberto."))


class ConfirmCloseView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "### Encerrar este ticket? 🌸\n"
                    "O canal será apagado e a transcrição da conversa será enviada."
                ),
                discord.ui.ActionRow(ConfirmCloseSelect()),
            )
        )


async def build_transcript(channel, data, cfg):
    stamp = lambda dt: dt.astimezone(TZ).strftime("%d/%m/%Y %H:%M")
    t = cat_of(cfg, data["tipo"])
    lines = [
        f"Transcrição de #{channel.name}",
        f"Categoria: {t['label']}",
        f"{cfg['modal']['assunto']}: {data['assunto']}",
        f"{cfg['modal']['detalhes']}: {data['descricao'] or '—'}",
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
    cfg = get_cfg(channel.guild.id)
    data = _data_for(channel)
    # evita fechar duas vezes
    if data.get("closing"):
        return
    data["closing"] = True
    delay = cfg["delay"]

    try:
        if cfg["transcricao_log"] or cfg["transcricao_dm"]:
            transcript = await build_transcript(channel, data, cfg)
            safe = re.sub(r"[^a-z0-9-]", "", channel.name.lower()) or f"ticket-{channel.id}"
            filename = f"transcricao-{safe}.txt"
            mk_file = lambda: discord.File(io.BytesIO(transcript.encode("utf-8")), filename=filename)

            log_channel = find_channel(channel.guild, cfg["log"]) if cfg["transcricao_log"] else None
            if log_channel is not None:
                try:
                    await log_channel.send(
                        f"Transcrição de **{channel.name}** · aberto por <@{data['owner']}> · "
                        f"fechado por {closer.mention}",
                        file=mk_file(),
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException as e:
                    log.warning("nao consegui mandar a transcricao pro log: %s", e)

            owner = channel.guild.get_member(data["owner"])
            if cfg["transcricao_dm"] and owner is not None:
                try:
                    await owner.send(
                        f"Aqui está a transcrição do seu ticket em **{channel.guild.name}** 🌸",
                        file=mk_file(),
                    )
                except discord.HTTPException:
                    pass  # dm fechada

        wait = f"**{delay} segundos**" if delay else "instantes"
        try:
            await channel.send(
                view=Card("Ticket encerrado 🌸", f"Encerrado por {closer.mention}. Este canal será apagado em {wait}."),
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            pass

        await asyncio.sleep(delay)
    except Exception:
        data["closing"] = False  # deu erro no meio: deixa tentar de novo
        raise

    state["tickets"].pop(str(channel.id), None)
    _save()
    try:
        await channel.delete(reason=f"Ticket fechado por {closer}")
    except discord.HTTPException as e:
        log.warning("nao consegui apagar o ticket %s: %s", channel.name, e)


# botoes continuam dps de reiniciar
def register_views(client):
    client.add_dynamic_items(NewTicketButton)
    seletor = discord.ui.LayoutView(timeout=None)
    seletor.add_item(discord.ui.ActionRow(PanelSelect()))
    client.add_view(seletor)
    legado = discord.ui.LayoutView(timeout=None)   # botoes dos tickets antigos
    legado.add_item(discord.ui.Container(TicketButtonsRow(), AddMemberRow()))
    client.add_view(legado)
    cfg = copy.deepcopy(DEFAULTS)
    client.add_view(
        build_ticket_view(
            {"owner": 0, "tipo": "outros", "assunto": "", "descricao": "", "opened": 0},
            {**cfg, "categorias": {k: {**CAT_BLANK, **c} for k, c in cfg["categorias"].items()}},
        )
    )


# ---------------------------------------------------------------- configuracao

NONE_WORDS = {"nenhum", "nenhuma", "vazio", "remover", "-"}
TRUE_WORDS = {"sim", "s", "on", "true", "1", "ligar", "ligado", "ativar"}
FALSE_WORDS = {"nao", "não", "n", "off", "false", "0", "desligar", "desligado", "desativar"}

# chave: (caminho tipo limite pode_ficar_vazio descricao)
SETTINGS = {
    "titulo": (("painel", "titulo"), "texto", 100, True, "título do painel"),
    "descricao": (("painel", "descricao"), "texto", 1000, True, "texto de abertura do painel"),
    "info": (("painel", "info"), "texto", 500, True, "bloco de avisos do painel"),
    "rodape": (("painel", "rodape"), "texto", 200, True, "rodapé pequeno do painel"),
    "banner": (("painel", "banner"), "url", None, True, "imagem do topo do painel (link, `padrao` ou `nenhum`)"),
    "cor": (("painel", "cor"), "cor", None, True, "barra colorida do painel (some por padrão)"),
    "modo": (("painel", "modo"), "modo", None, False, "botões ou menu"),
    "botao": (("painel", "botao"), "texto", 40, True, "texto fixo dos botões (vazio = nome da categoria)"),
    "boasvindas": (("ticket", "boas_vindas"), "texto", 1000, False, "mensagem dentro do ticket"),
    "rodape_ticket": (("ticket", "rodape"), "texto", 200, True, "rodapé pequeno dentro do ticket"),
    "cor_ticket": (("ticket", "cor"), "cor", None, True, "barra colorida do ticket"),
    "nome_canal": (("ticket", "nome_canal"), "texto", 60, False, "formato do nome do canal"),
    "campo_assunto": (("modal", "assunto"), "texto", 45, False, "nome do 1º campo do formulário"),
    "campo_detalhes": (("modal", "detalhes"), "texto", 45, False, "nome do 2º campo do formulário"),
    "staff": (("staff",), "cargo", None, True, "cargo da equipe"),
    "log": (("log",), "canal", None, True, "canal que recebe as transcrições"),
    "categoria": (("categoria",), "categoria", None, True, "categoria do Discord onde os tickets nascem"),
    "limite": (("limite",), "int", (1, 10), False, "tickets abertos por pessoa"),
    "delay": (("delay",), "int", (0, 60), False, "segundos até apagar o canal ao fechar"),
    "transcricao_dm": (("transcricao_dm",), "bool", None, False, "enviar transcrição na DM"),
    "transcricao_log": (("transcricao_log",), "bool", None, False, "enviar transcrição pro canal de log"),
}

# campos de categoria: nome do comando -> (campo salvo tipo limite vazio)
CAT_FIELDS = {
    "emoji": ("emoji", "texto", 40, True),
    "nome": ("label", "texto", 30, False),
    "dica": ("hint", "texto", 100, True),
    "botao": ("botao", "estilo", None, False),
    "cor": ("cor", "cor", None, True),
    "cargo": ("staff", "cargo", None, True),
    "categoria": ("categoria", "categoria", None, True),
    "boasvindas": ("boasvindas", "texto", 1000, True),
}

KEY_RE = re.compile(r"^[a-z0-9_]{1,20}$")


def parse_color(s: str) -> Optional[int]:
    s = s.strip().lower()
    if s in COLOR_NAMES:
        return COLOR_NAMES[s]
    m = re.fullmatch(r"#?([0-9a-f]{6})", s)
    return int(m.group(1), 16) if m else None


async def parse_value(ctx, kind, raw, limit, vazio, default=None):
    """devolve (valor, erro). erro vem preenchido se algo deu errado."""
    raw = raw.strip()
    low = raw.lower()
    wants_none = low in NONE_WORDS

    if kind == "texto":
        if not raw and not vazio:
            return None, "Esse campo não pode ficar vazio."
        if wants_none:
            return ("", None) if vazio else (None, "Esse campo não pode ficar vazio.")
        text = raw.replace("\\n", "\n")
        if len(text) > limit:
            return None, f"Muito grande: o máximo são {limit} caracteres (você mandou {len(text)})."
        return text, None
    if kind == "modo":
        mapa = {"botoes": "botoes", "botões": "botoes", "botao": "botoes", "menu": "menu", "seletor": "menu"}
        if low not in mapa:
            return None, "Use `botoes` ou `menu`."
        return mapa[low], None
    if kind == "url":
        if low in ("padrao", "padrão"):
            return None, None   # volta pro banner do bot
        if wants_none:
            return "", None     # sem banner
        if not re.match(r"^https?://\S+$", raw):
            return None, "Mande um link que comece com http:// ou https://."
        return raw, None
    if kind == "cor":
        if wants_none:
            return None, None
        c = parse_color(raw)
        if c is None:
            return None, "Cor inválida. Use um hex (`#5865F2`) ou: " + ", ".join(COLOR_NAMES) + "."
        return c, None
    if kind == "estilo":
        if low not in BUTTON_STYLES:
            return None, "Cor de botão inválida. Use: " + ", ".join(BUTTON_STYLES) + "."
        return low, None
    if kind == "int":
        lo, hi = limit
        if not raw.lstrip("-").isdigit() or not lo <= int(raw) <= hi:
            return None, f"Mande um número de {lo} a {hi}."
        return int(raw), None
    if kind == "bool":
        if low in TRUE_WORDS:
            return True, None
        if low in FALSE_WORDS:
            return False, None
        return None, "Responda `sim` ou `nao`."
    if kind in ("cargo", "canal", "categoria"):
        if wants_none:
            return None, None
        if low in ("padrao", "padrão") and default is not None:
            return default, None
        conv = {
            "cargo": commands.RoleConverter,
            "canal": commands.TextChannelConverter,
            "categoria": commands.CategoryChannelConverter,
        }[kind]
        try:
            return (await conv().convert(ctx, raw)).id, None
        except commands.BadArgument:
            return None, {"cargo": "Não achei esse cargo.", "canal": "Não achei esse canal de texto.",
                          "categoria": "Não achei essa categoria do Discord."}[kind]
    return None, "Tipo de campo desconhecido."


def _short(v, n=60) -> str:
    v = str(v).replace("\n", " ⏎ ")
    return v if len(v) <= n else v[: n - 1] + "…"


def _show(guild, key, cfg) -> str:
    path, kind, *_ = SETTINGS[key]
    v = cfg
    for k in path:
        v = v[k]
    if key == "banner":
        return "*(padrão do bot)*" if v is None else "*(sem banner)*" if v == "" else f"`{_short(v)}`"
    if key == "botao" and not v:
        return "*(nome da categoria)*"
    if v in (None, ""):
        return "*(vazio)*"
    if kind == "cor":
        return f"`#{v:06X}`"
    if kind == "bool":
        return "sim" if v else "não"
    if kind == "cargo":
        r = _role(guild, v)
        return r.mention if r else f"`{v}` *(não encontrado)*"
    if kind in ("canal", "categoria"):
        ch = find_channel(guild, v) if kind == "canal" else guild.get_channel(v) if isinstance(v, int) else None
        return ch.mention if ch else f"`{v}`"
    return f"`{_short(v)}`"


HELP_1 = (
    "## ⚙️ Como configurar\n"
    "**Pelos botões:** use `,ticketconfig` (sem nada depois) e clique.\n"
    "**Por comando:** os comandos abaixo continuam funcionando.\n\n"
    "**Mudar uma opção:** `,ticketconfig set <opção> <valor>`\n"
    "Exemplo: `,ticketconfig set titulo 🎫 Fale com a gente`\n"
    "Pra pular linha use `\\n`. Pra limpar um campo use `nenhum`.\n\n"
    "**Opções de texto:** `titulo` `descricao` `info` `rodape` `botao` `boasvindas` "
    "`rodape_ticket` `nome_canal` `campo_assunto` `campo_detalhes`\n"
    "**Visual:** `modo` (`botoes` ou `menu`) · `banner` (link, `padrao` volta pro banner do bot, `nenhum` tira)\n"
    "**Cores:** `cor` (painel) e `cor_ticket`. Aceitam hex (`#5865F2`) ou nome "
    "(" + ", ".join(COLOR_NAMES) + "). Sem cor = container liso.\n"
    "**Equipe:** `staff` (cargo) · `log` (canal) · `categoria` (categoria do Discord pros tickets)\n"
    "**Regras:** `limite` (1 a 10) · `delay` (0 a 60 s) · `transcricao_dm` e `transcricao_log` (sim/nao)\n\n"
    "**Variáveis** em `boasvindas`: `{usuario}` `{staff}` `{categoria}` `{emoji}`\n"
    "**Variáveis** em `nome_canal`: `{emoji}` `{tipo}` `{usuario}` `{id}`"
)

HELP_2 = (
    "## 🗂️ Categorias\n"
    "`,ticketconfig cat add <chave> <emoji> <nome>`  cria (até " + str(MAX_CATEGORIAS) + ")\n"
    "`,ticketconfig cat edit <chave> <campo> <valor>`  muda um campo\n"
    "`,ticketconfig cat remove <chave>`  apaga\n"
    "`,ticketconfig cat mover <chave> <posição>`  reordena\n\n"
    "**Campos:** `emoji` `nome` `dica` `botao` (" + ", ".join(BUTTON_STYLES) + ") "
    "`cor` (barra do ticket) `cargo` (equipe só dessa categoria) "
    "`categoria` (categoria do Discord só dessa) `boasvindas` (mensagem própria)\n\n"
    "**Outros:** `,ticketconfig atualizar` reedita os painéis · "
    "`,ticketconfig reset tudo` volta tudo ao padrão (os painéis já postados continuam sendo atualizados)\n"
    "-# Mexeu em algo? Os painéis já postados se atualizam sozinhos."
)


async def _say(ctx, title, text="", ok=True, keep=False):
    await ctx.send(view=Card(("✅ " if ok else "⚠️ ") + title, text), delete_after=None if keep else 8)


# ---------------------------------------------------------------- painel interativo (botoes)

LABELS = {
    "titulo": "Título do painel",
    "descricao": "Texto de abertura",
    "info": "Bloco de avisos",
    "rodape": "Rodapé do painel",
    "banner": "Banner: link, padrao ou nenhum",
    "boasvindas": "Mensagem de boas-vindas",
    "rodape_ticket": "Rodapé do ticket",
    "nome_canal": "Formato do nome do canal",
    "campo_assunto": "Nome do 1º campo do formulário",
    "campo_detalhes": "Nome do 2º campo do formulário",
    "cor": "Cor do painel (#hex ou nome)",
    "cor_ticket": "Cor do ticket (#hex ou nome)",
    "botao": "Texto fixo dos botões",
    "limite": "Tickets por pessoa (1 a 10)",
    "delay": "Segundos até apagar (0 a 60)",
}

# cada janela tem no max 5 campos (limite do discord)
MODALS = {
    "painel": ("📋 Textos do painel", ["titulo", "descricao", "info", "rodape", "banner"]),
    "painelvisual": ("🎨 Cor e botões do painel", ["cor", "botao"]),
    "ticket": ("🎟️ Mensagens do ticket", ["boasvindas", "rodape_ticket", "nome_canal", "campo_assunto", "campo_detalhes"]),
    "ticketregras": ("⚖️ Regras e cor do ticket", ["limite", "delay", "cor_ticket"]),
}

# so essas mudam a cara do painel postado
PANEL_KEYS = {"titulo", "descricao", "info", "rodape", "banner", "cor", "modo", "botao"}

STYLE_ICONS = {"azul": "🔵", "cinza": "⚪", "verde": "🟢", "vermelho": "🔴", "rosa": "🌸"}

CAT_LABELS = {
    "emoji": "Emoji (unicode ou <:nome:id>)",
    "nome": "Nome da categoria",
    "dica": "Dica (aparece no painel)",
    "cor": "Cor da barra (#hex ou nome)",
    "boasvindas": "Boas-vindas só desta categoria",
}

sep = discord.ui.Separator   # atalho pq eu uso mt


def _sec(titulo, sub=""):
    # titulo de cada parte da tela
    return discord.ui.TextDisplay(f"### {titulo}" + (f"\n-# {sub}" if sub else ""))


def _modal_default(key, cfg) -> str:
    path, kind, *_ = SETTINGS[key]
    v = _dig(cfg, path)
    if key == "banner":
        return "padrao" if v is None else "nenhum" if v == "" else v
    if kind == "cor":
        return _hex(v)
    return "" if v is None else str(v)


def _hex(v) -> str:
    return f"#{v:06X}" if isinstance(v, int) else ""


def _dig(cfg, path):
    for k in path:
        cfg = cfg[k]
    return cfg


def _cat_set(guild_id, key, field, value):
    cats = _cats_over(guild_id)
    if key in cats:
        cats[key][field] = value
        _save_cfg()


def _cat_move(guild_id, key, delta):
    cats = _cats_over(guild_id)
    ordem = list(cats)
    if key not in ordem:
        return
    i = ordem.index(key)
    ordem.insert(max(0, min(len(ordem) - 1, i + delta)), ordem.pop(i))
    novo = {k: cats[k] for k in ordem}
    cats.clear()
    cats.update(novo)
    _save_cfg()


async def _erro_card(interaction, title, text=""):
    card = Card(title, text)
    if interaction.response.is_done():
        await interaction.followup.send(view=card, ephemeral=True)
    else:
        await interaction.response.send_message(view=card, ephemeral=True)


# itens q chamam uma funcao qlqr fn(interaction item)
class _Fn:
    def __init__(self, fn, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fn = fn

    async def callback(self, interaction):
        await self.fn(interaction, self)


class _Btn(_Fn, discord.ui.Button):
    pass


class _Sel(_Fn, discord.ui.Select):
    pass


class _RoleSel(_Fn, discord.ui.RoleSelect):
    pass


class _ChanSel(_Fn, discord.ui.ChannelSelect):
    pass


# janela generica serve pra varias opcoes do settings
class SettingsModal(discord.ui.Modal):
    def __init__(self, painel, title, keys):
        super().__init__(title=title[:45])
        self.painel = painel
        self.keys = keys
        cfg = get_cfg(painel.guild.id)
        self.inputs = {}
        for k in keys:
            _, kind, limit, vazio, _ = SETTINGS[k]
            maxlen = limit if kind == "texto" else 1000 if kind == "url" else 20 if kind == "cor" else 3
            ti = discord.ui.TextInput(
                label=LABELS[k][:45],
                default=_modal_default(k, cfg)[:maxlen] or None,
                style=discord.TextStyle.paragraph if kind == "texto" and maxlen > 100 else discord.TextStyle.short,
                required=not vazio,
                max_length=maxlen,
            )
            self.inputs[k] = ti
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction):
        valores, erros = {}, []
        for k in self.keys:
            path, kind, limit, vazio, _ = SETTINGS[k]
            raw = self.inputs[k].value.strip()
            if not raw:
                if not vazio:
                    erros.append(f"**{LABELS[k]}**: não pode ficar vazio.")
                    continue
                # vazio no banner = padrao vazio na cor = sem cor
                raw = {"url": "padrao", "cor": "nenhum"}.get(kind, raw)
            valor, erro = await parse_value(None, kind, raw, limit, vazio)
            if erro:
                erros.append(f"**{LABELS[k]}**: {erro}")
            else:
                valores[k] = valor
        if erros:
            # se um campo ta errado nao salva nenhum
            await _erro_card(interaction, "Nada foi salvo", "\n".join(erros))
            return
        for k, v in valores.items():
            set_path(self.painel.guild.id, SETTINGS[k][0], v)
        await self.painel.update(interaction, "✅ Salvo", refresh=bool(PANEL_KEYS & set(valores)))

    async def on_error(self, interaction, error):
        log.exception("erro na janela de config", exc_info=error)
        await _erro_card(interaction, "Algo deu errado", "Tente de novo.")


# editar uma categoria (emoji nome dica cor boasvindas)
class CatEditModal(discord.ui.Modal):
    def __init__(self, painel, key):
        super().__init__(title="✏️ Editar categoria")
        self.painel, self.key = painel, key
        c = get_cfg(painel.guild.id)["categorias"][key]
        # campo valor atual tamanho max pode ficar vazio paragrafo
        specs = (
            ("emoji", c["emoji"], 40, True, False),
            ("nome", c["label"], 30, False, False),
            ("dica", c.get("hint") or "", 100, True, False),
            ("cor", _hex(c.get("cor")), 20, True, False),
            ("boasvindas", c.get("boasvindas") or "", 1000, True, True),
        )
        self.inputs = {}
        for campo, atual, maxlen, vazio, longo in specs:
            ti = discord.ui.TextInput(
                label=CAT_LABELS[campo][:45], default=str(atual)[:maxlen] or None,
                style=discord.TextStyle.paragraph if longo else discord.TextStyle.short,
                required=not vazio, max_length=maxlen,
            )
            self.inputs[campo] = ti
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction):
        valores, erros = {}, []
        for campo, ti in self.inputs.items():
            salvo, kind, limit, vazio = CAT_FIELDS[campo]
            raw = ti.value.strip()
            if not raw:
                if not vazio:
                    erros.append(f"**{CAT_LABELS[campo]}**: não pode ficar vazio.")
                    continue
                raw = "nenhum" if kind == "cor" else raw
            valor, erro = await parse_value(None, kind, raw, limit, vazio)
            if erro:
                erros.append(f"**{CAT_LABELS[campo]}**: {erro}")
            else:
                valores[salvo] = valor
        if erros:
            await _erro_card(interaction, "Nada foi salvo", "\n".join(erros))
            return
        for salvo, v in valores.items():
            _cat_set(self.painel.guild.id, self.key, salvo, v)
        await self.painel.update(interaction, "✅ Categoria atualizada", refresh=True)

    async def on_error(self, interaction, error):
        log.exception("erro na janela da categoria", exc_info=error)
        await _erro_card(interaction, "Algo deu errado", "Tente de novo.")


class NewCatModal(discord.ui.Modal):
    def __init__(self, painel):
        super().__init__(title="➕ Nova categoria")
        self.painel = painel
        self.chave = discord.ui.TextInput(label="Chave (letras minúsculas, números e _)", max_length=20, placeholder="ex: vip")
        self.emoji = discord.ui.TextInput(label="Emoji (opcional)", max_length=40, required=False, placeholder="deixe vazio para ficar só texto")
        self.nome = discord.ui.TextInput(label="Nome", max_length=30, placeholder="ex: Suporte VIP")
        for ti in (self.chave, self.emoji, self.nome):
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction):
        chave, emoji, nome = self.chave.value.strip().lower(), self.emoji.value.strip(), self.nome.value.strip()
        cats = _cats_over(self.painel.guild.id)
        if not KEY_RE.match(chave):
            await _erro_card(interaction, "Chave inválida", "Use só letras minúsculas, números e `_` (até 20).")
        elif chave in cats:
            await _erro_card(interaction, "Essa chave já existe", "Escolha outra.")
        elif len(cats) >= MAX_CATEGORIAS:
            await _erro_card(interaction, "Limite de categorias", f"O máximo são {MAX_CATEGORIAS}.")
        elif not nome:
            await _erro_card(interaction, "Faltou algo", "O nome é obrigatório.")
        else:
            cats[chave] = {**CAT_BLANK, "emoji": emoji, "label": nome}
            _save_cfg()
            self.painel.page = ("cat", chave)
            await self.painel.update(interaction, "✅ Categoria criada", refresh=True)

    async def on_error(self, interaction, error):
        log.exception("erro ao criar categoria", exc_info=error)
        await _erro_card(interaction, "Algo deu errado", "Tente de novo.")


# a tela principal toda so quem abriu mexe
class ConfigView(discord.ui.LayoutView):
    def __init__(self, author_id: int, guild: discord.Guild, page=("home",)):
        super().__init__(timeout=600)
        self.author_id, self.guild = author_id, guild
        self.page, self.notice, self.sent = page, "", None
        self.render()

    @classmethod
    async def open(cls, ctx, page=("home",)):
        view = cls(ctx.author.id, ctx.guild, page)
        view.sent = await ctx.send(view=view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                view=Card("Esse painel é de outra pessoa", "Use `,ticketconfig` pra abrir o seu."),
                ephemeral=True,
            )
            return False
        perms = getattr(interaction.user, "guild_permissions", None)
        if perms is None or not perms.administrator:
            await _erro_card(interaction, "Sem permissão", "Só administradores mexem aqui.")
            return False
        return True

    async def on_timeout(self):
        # desliga os botoes qnd expira
        if self.sent is None:
            return
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
        try:
            await self.sent.edit(view=self)
        except discord.HTTPException:
            pass

    async def on_error(self, interaction, error, item):
        log.exception("erro no painel de config", exc_info=error)
        await _erro_card(interaction, "Algo deu errado", "Tente de novo.")

    # redesenha a tela refresh=true reedita os paineis postados tambem
    async def update(self, interaction, notice="", refresh=False, vazio=""):
        if refresh:
            if not interaction.response.is_done():
                await interaction.response.defer()
            n = await refresh_panels(self.guild)
            notice = f"{notice} · {n} painel(is) atualizado(s)" if n else (notice + vazio)
        self.notice = notice
        self.render()
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)

    async def go(self, interaction, page):
        self.page = page
        await self.update(interaction)

    def render(self):
        cfg = get_cfg(self.guild.id)
        # categoria foi apagada com o painel aberto
        if self.page[0] in ("cat", "delcat") and self.page[1] not in cfg["categorias"]:
            self.page = ("cats",)
        children = getattr(self, f"_page_{self.page[0]}")(cfg)
        if self.notice:
            children.append(discord.ui.TextDisplay(f"-# {self.notice}"))
        self.clear_items()
        self.add_item(discord.ui.Container(*children))

    # ---- coisinhas pra montar os itens

    def _btn(self, label, fn, style=discord.ButtonStyle.secondary, emoji=None, disabled=False):
        return _Btn(fn, label=label[:80], style=style, emoji=emoji, disabled=disabled)

    def _goto(self, *page):
        async def h(interaction, item):
            await self.go(interaction, page)
        return h

    def _modal(self, nome):
        titulo, keys = MODALS[nome]

        async def h(interaction, item):
            await interaction.response.send_modal(SettingsModal(self, titulo, keys))
        return h

    def _toggle(self, path):
        async def h(interaction, item):
            atual = _dig(get_cfg(self.guild.id), path)
            novo = ("menu" if atual != "menu" else "botoes") if path == ("painel", "modo") else not atual
            set_path(self.guild.id, path, novo)
            await self.update(interaction, "✅ Salvo", refresh=path == ("painel", "modo"))
        return h

    def _pick(self, path):
        # sletor de cargo ou canal salva o id
        async def h(interaction, item):
            set_path(self.guild.id, path, item.values[0].id)
            await self.update(interaction, "✅ Salvo")
        return h

    def _clear(self, path):
        async def h(interaction, item):
            set_path(self.guild.id, path, None)
            await self.update(interaction, "✅ Removido")
        return h

    async def _refresh_all(self, interaction, item):
        await self.update(interaction, "🔄 Painéis reeditados", refresh=True,
                          vazio=" · nenhum painel postado ainda (use `,painelticket`)")

    # ---- tela inicial, cada parte tem titulo + explicacao + botoes

    def _page_home(self, cfg):
        g = self.guild
        menu = cfg["painel"]["modo"] == "menu"
        dm, lg = cfg["transcricao_dm"], cfg["transcricao_log"]
        verde, cinza = discord.ButtonStyle.success, discord.ButtonStyle.secondary
        cats = "\n".join(f"{c['emoji']} **{c['label']}**  `{k}`" for k, c in cfg["categorias"].items())

        return [
            discord.ui.TextDisplay(
                "## ⚙️ Central de configuração 🌸\n"
                "-# clique nos botões de cada parte pra mudar. tudo salva na hora e os painéis se atualizam sozinhos"
            ),
            sep(),

            # parte 1
            _sec("📋 Painel de abertura", "a mensagem onde o pessoal clica pra abrir ticket"),
            discord.ui.TextDisplay(
                f"**Modo**  {_show(g, 'modo', cfg)}\n**Título**  {_show(g, 'titulo', cfg)}\n"
                f"**Banner**  {_show(g, 'banner', cfg)}\n**Cor**  {_show(g, 'cor', cfg)}"
            ),
            discord.ui.ActionRow(
                self._btn("Editar textos", self._modal("painel"), emoji="✏️"),
                self._btn("Cor e botões", self._modal("painelvisual"), emoji="🎨"),
                self._btn(f"Modo: {'menu' if menu else 'botões'}", self._toggle(("painel", "modo")),
                          verde if menu else cinza, "🔀"),
            ),
            sep(),

            # parte 2
            _sec("🎟️ Dentro do ticket", "o que aparece no canal quando alguém abre"),
            discord.ui.TextDisplay(
                f"**Boas-vindas**  {_show(g, 'boasvindas', cfg)}\n**Nome do canal**  {_show(g, 'nome_canal', cfg)}\n"
                f"**Tickets por pessoa**  {_show(g, 'limite', cfg)}\n**Segundos até apagar**  {_show(g, 'delay', cfg)}"
            ),
            discord.ui.ActionRow(
                self._btn("Editar mensagens", self._modal("ticket"), emoji="✏️"),
                self._btn("Regras e cor", self._modal("ticketregras"), emoji="⚖️"),
            ),
            sep(),

            # parte 3
            _sec("📩 Transcrições", "quando o ticket fecha a conversa vira um .txt, escolha pra onde manda"),
            discord.ui.ActionRow(
                self._btn(f"Enviar na DM: {'sim' if dm else 'não'}", self._toggle(("transcricao_dm",)),
                          verde if dm else cinza, "📩"),
                self._btn(f"Enviar no log: {'sim' if lg else 'não'}", self._toggle(("transcricao_log",)),
                          verde if lg else cinza, "📄"),
            ),
            sep(),

            # parte 4
            _sec("👥 Equipe e canais", "quem atende e onde as coisas ficam"),
            discord.ui.TextDisplay(
                f"**Cargo**  {_show(g, 'staff', cfg)}\n**Canal das transcrições**  {_show(g, 'log', cfg)}\n"
                f"**Categoria dos tickets**  {_show(g, 'categoria', cfg)}"
            ),
            discord.ui.ActionRow(self._btn("Escolher cargo e canais", self._goto("geral"), emoji="👥")),
            sep(),

            # parte 5
            _sec("🗂️ Categorias", "os tipos de ticket (suporte, denúncia...)"),
            discord.ui.TextDisplay(cats),
            discord.ui.ActionRow(self._btn("Gerenciar categorias", self._goto("cats"), emoji="🗂️")),
            sep(),

            # parte 6
            _sec("🔧 Manutenção"),
            discord.ui.ActionRow(
                self._btn("Atualizar painéis", self._refresh_all, discord.ButtonStyle.primary, "🔄"),
                self._btn("Resetar tudo", self._goto("reset"), discord.ButtonStyle.danger, "♻️"),
            ),
        ]

    # ---- cargo e canais

    def _page_geral(self, cfg):
        g = self.guild
        return [
            discord.ui.TextDisplay("## 👥 Equipe e canais 🌸\n-# escolha nos menus, salva na hora"),
            sep(),

            _sec("🛡️ Cargo da equipe", f"agora: {_show(g, 'staff', cfg)}  ·  admin e gerenciar mensagens já contam como staff"),
            discord.ui.ActionRow(_RoleSel(self._pick(("staff",)), placeholder="Escolher o cargo", min_values=1, max_values=1)),
            discord.ui.ActionRow(self._btn("Tirar cargo", self._clear(("staff",)))),
            sep(),

            _sec("📄 Canal das transcrições", f"agora: {_show(g, 'log', cfg)}"),
            discord.ui.ActionRow(_ChanSel(
                self._pick(("log",)), placeholder="Escolher o canal",
                channel_types=[discord.ChannelType.text], min_values=1, max_values=1,
            )),
            discord.ui.ActionRow(self._btn("Tirar canal", self._clear(("log",)))),
            sep(),

            _sec("📁 Categoria do Discord", f"agora: {_show(g, 'categoria', cfg)}  ·  onde os canais de ticket nascem"),
            discord.ui.ActionRow(_ChanSel(
                self._pick(("categoria",)), placeholder="Escolher a categoria",
                channel_types=[discord.ChannelType.category], min_values=1, max_values=1,
            )),
            discord.ui.ActionRow(self._btn("Tirar categoria", self._clear(("categoria",)))),
            sep(),

            discord.ui.ActionRow(self._btn("Voltar pro início", self._goto("home"), emoji="⬅️")),
        ]

    # ---- lista de categorias

    def _page_cats(self, cfg):
        cats = cfg["categorias"]
        linhas = [
            f"{c['emoji']}  **{c['label']}**  `{k}`" + (f"\n-# {c['hint']}" if c.get("hint") else "")
            for k, c in cats.items()
        ]

        async def escolher(interaction, item):
            await self.go(interaction, ("cat", item.values[0]))

        async def nova(interaction, item):
            await interaction.response.send_modal(NewCatModal(self))

        opcoes = [
            discord.SelectOption(label=c["label"][:100], value=k, emoji=_ui_emoji(c["emoji"]),
                                 description=f"chave: {k}")
            for k, c in cats.items()
        ]
        return [
            discord.ui.TextDisplay(f"## 🗂️ Categorias ({len(cats)}/{MAX_CATEGORIAS}) 🌸\n" + "\n".join(linhas)),
            sep(),

            _sec("✏️ Editar uma categoria", "escolha qual no menu"),
            discord.ui.ActionRow(_Sel(escolher, placeholder="Escolher categoria", options=opcoes)),
            sep(),

            _sec("➕ Criar categoria", f"máximo de {MAX_CATEGORIAS}"),
            discord.ui.ActionRow(
                self._btn("Nova categoria", nova, discord.ButtonStyle.success, "➕", disabled=len(cats) >= MAX_CATEGORIAS)
            ),
            sep(),

            discord.ui.ActionRow(self._btn("Voltar pro início", self._goto("home"), emoji="⬅️")),
        ]

    # ---- uma categoria

    def _page_cat(self, cfg):
        key = self.page[1]
        c = cfg["categorias"][key]
        ordem = list(cfg["categorias"])
        g = self.guild
        cargo = _role(g, c.get("staff"))
        cat_dc = g.get_channel(c["categoria"]) if isinstance(c.get("categoria"), int) else None

        async def estilo(interaction, item):
            _cat_set(g.id, key, "botao", item.values[0])
            await self.update(interaction, "✅ Salvo", refresh=True)

        async def editar(interaction, item):
            await interaction.response.send_modal(CatEditModal(self, key))

        def mover(delta):
            async def h(interaction, item):
                _cat_move(g.id, key, delta)
                await self.update(interaction, "✅ Ordem alterada", refresh=True)
            return h

        # os dois "tirar" fazem quase a mesma coisa ta dupliacdo msm
        async def tirar_cargo(interaction, item):
            _cat_set(g.id, key, "staff", None)
            await self.update(interaction, "✅ Removido")

        async def tirar_cat(interaction, item):
            _cat_set(g.id, key, "categoria", None)
            await self.update(interaction, "✅ Removido")

        def escolher(campo):
            async def h(interaction, item):
                _cat_set(g.id, key, campo, item.values[0].id)
                await self.update(interaction, "✅ Salvo")
            return h

        estilos = [
            discord.SelectOption(label=f"Botão {n}", value=n, emoji=STYLE_ICONS.get(n, "🔘"), default=n == c["botao"])
            for n in BUTTON_STYLES
        ]
        pos = ordem.index(key)
        return [
            discord.ui.TextDisplay(
                f"## {c['emoji']} {c['label']}  `{key}`\n"
                f"**Dica**  {_short(c['hint']) if c.get('hint') else '*(vazio)*'}\n"
                f"**Cor da barra**  {'`' + _hex(c['cor']) + '`' if c.get('cor') else '*(padrão)*'}\n"
                f"**Boas-vindas própria**  {_short(c['boasvindas']) if c.get('boasvindas') else '*(usa a geral)*'}"
            ),
            sep(),

            _sec("🎨 Aparência", "emoji, nome, dica, cor da barra, boas-vindas e a cor do botão"),
            discord.ui.ActionRow(self._btn("Editar textos e cor da barra", editar, discord.ButtonStyle.primary, "✏️")),
            discord.ui.ActionRow(_Sel(estilo, placeholder=f"Cor do botão (agora: {c['botao']})", options=estilos)),
            sep(),

            _sec("👥 Quem atende", f"cargo: {cargo.mention if cargo else '*(usa o geral)*'}  ·  categoria do Discord: {cat_dc.mention if cat_dc else '*(usa a geral)*'}"),
            discord.ui.ActionRow(_RoleSel(escolher("staff"), placeholder="Cargo só desta categoria", min_values=1, max_values=1)),
            discord.ui.ActionRow(_ChanSel(
                escolher("categoria"), placeholder="Categoria do Discord só desta",
                channel_types=[discord.ChannelType.category], min_values=1, max_values=1,
            )),
            discord.ui.ActionRow(
                self._btn("Tirar cargo", tirar_cargo),
                self._btn("Tirar categoria", tirar_cat),
            ),
            sep(),

            _sec("↕️ Ordem no painel", f"posição {pos + 1} de {len(ordem)}"),
            discord.ui.ActionRow(
                self._btn("Subir", mover(-1), emoji="⬆️", disabled=pos == 0),
                self._btn("Descer", mover(1), emoji="⬇️", disabled=pos == len(ordem) - 1),
            ),
            sep(),

            _sec("⚠️ Outros"),
            discord.ui.ActionRow(
                self._btn("Apagar categoria", self._goto("delcat", key), discord.ButtonStyle.danger, "🗑️", disabled=len(ordem) <= 1),
                self._btn("Voltar", self._goto("cats"), emoji="⬅️"),
            ),
        ]

    def _page_delcat(self, cfg):
        key = self.page[1]
        c = cfg["categorias"][key]

        async def apagar(interaction, item):
            cats = _cats_over(self.guild.id)
            if key in cats and len(cats) > 1:
                del cats[key]
                _save_cfg()
            self.page = ("cats",)
            await self.update(interaction, f"✅ Categoria `{key}` apagada", refresh=True)

        return [
            discord.ui.TextDisplay(
                f"## 🗑️ Apagar {c['emoji']} {c['label']}?\n"
                "Os tickets já abertos dela continuam funcionando."
            ),
            discord.ui.ActionRow(
                self._btn("Sim, apagar", apagar, discord.ButtonStyle.danger, "🗑️"),
                self._btn("Cancelar", self._goto("cat", key)),
            ),
        ]

    def _page_reset(self, cfg):
        async def resetar(interaction, item):
            paineis = get_cfg(self.guild.id)["paineis"]
            _cfg_store["guilds"][str(self.guild.id)] = {"paineis": paineis}
            _save_cfg()
            self.page = ("home",)
            await self.update(interaction, "✅ Tudo voltou ao padrão", refresh=True)

        return [
            discord.ui.TextDisplay(
                "## ♻️ Voltar tudo ao padrão?\n"
                "Isso apaga toda a personalização (textos, cores, categorias e equipe). "
                "Os painéis já postados continuam sendo atualizados."
            ),
            discord.ui.ActionRow(
                self._btn("Sim, resetar tudo", resetar, discord.ButtonStyle.danger, "♻️"),
                self._btn("Cancelar", self._goto("home")),
            ),
        ]


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        register_views(self.bot)

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not ctx.author.guild_permissions.administrator:
            raise commands.MissingPermissions(["administrator"])
        return True

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        # ticket apagado na mao: limpa o regisrto
        if state["tickets"].pop(str(channel.id), None) is not None:
            _save()

    async def _updated(self, ctx, title, text=""):
        n = await refresh_panels(ctx.guild)
        extra = f"\n-# {n} painel(is) atualizado(s)." if n else ""
        await _say(ctx, title, text + extra)

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

    @commands.group(
        name="ticketconfig",
        aliases=("tkconfig", "configticket"),
        invoke_without_command=True,
        extras={
            "categoria": "Utilidades",
            "uso": ",ticketconfig",
            "descricao": "Personaliza o sistema de tickets (textos, cores, categorias, equipe...).",
        },
    )
    async def ticketconfig(self, ctx):
        await ConfigView.open(ctx)

    @ticketconfig.command(name="ajuda", aliases=("help",))
    async def cfg_ajuda(self, ctx):
        for texto in (HELP_1, HELP_2):
            view = discord.ui.LayoutView(timeout=None)
            view.add_item(discord.ui.Container(discord.ui.TextDisplay(texto)))
            await ctx.send(view=view)

    @ticketconfig.command(name="set")
    async def cfg_set(self, ctx, chave: str, *, valor: str):
        chave = chave.lower()
        if chave not in SETTINGS:
            await _say(ctx, "Opção desconhecida",
                       "Use `,ticketconfig ajuda` pra ver a lista.", ok=False)
            return
        path, kind, limit, vazio, desc = SETTINGS[chave]
        default = DEFAULTS.get(path[0]) if len(path) == 1 else None
        value, erro = await parse_value(ctx, kind, valor, limit, vazio, default=default)
        if erro:
            await _say(ctx, f"Valor inválido pra `{chave}`", erro, ok=False)
            return
        set_path(ctx.guild.id, path, value)
        await self._updated(ctx, f"`{chave}` atualizado", _show(ctx.guild, chave, get_cfg(ctx.guild.id)))

    @ticketconfig.command(name="atualizar", aliases=("refresh",))
    async def cfg_atualizar(self, ctx):
        await self._updated(ctx, "Painéis atualizados")

    @ticketconfig.command(name="reset")
    async def cfg_reset(self, ctx, confirmar: str = ""):
        if confirmar.lower() != "tudo":
            await _say(ctx, "Isso apaga toda a personalização",
                       "Se tiver certeza, use `,ticketconfig reset tudo`.", ok=False)
            return
        paineis = get_cfg(ctx.guild.id)["paineis"]
        _cfg_store["guilds"][str(ctx.guild.id)] = {"paineis": paineis}
        _save_cfg()
        await self._updated(ctx, "Tudo voltou ao padrão")

    #  categorias

    @ticketconfig.group(name="cat", aliases=("categorias", "cats"), invoke_without_command=True)
    async def cfg_cat(self, ctx):
        await ConfigView.open(ctx, ("cats",))

    @cfg_cat.command(name="add", aliases=("novo", "criar"))
    async def cat_add(self, ctx, chave: str, emoji: str, *, nome: str):
        chave = chave.lower()
        cats = _cats_over(ctx.guild.id)
        if not KEY_RE.match(chave):
            await _say(ctx, "Chave inválida", "Use só letras minúsculas, números e `_` (até 20).", ok=False)
            return
        if chave in cats:
            await _say(ctx, "Essa chave já existe", f"Use `cat edit {chave} ...` pra mudar.", ok=False)
            return
        if len(cats) >= MAX_CATEGORIAS:
            await _say(ctx, "Limite de categorias", f"O máximo são {MAX_CATEGORIAS}.", ok=False)
            return
        nome = nome.strip()
        if len(nome) > 30 or len(emoji) > 40:
            await _say(ctx, "Texto grande demais", "Nome até 30 e emoji até 40 caracteres.", ok=False)
            return
        cats[chave] = {**CAT_BLANK, "emoji": emoji, "label": nome}
        _save_cfg()
        await self._updated(ctx, f"Categoria {emoji} {nome} criada",
                            f"Edite com `,ticketconfig cat edit {chave} dica ...` e `... botao azul`.")

    @cfg_cat.command(name="edit", aliases=("editar", "set"))
    async def cat_edit(self, ctx, chave: str, campo: str, *, valor: str):
        chave, campo = chave.lower(), campo.lower()
        cats = _cats_over(ctx.guild.id)
        if chave not in cats:
            await _say(ctx, "Categoria não encontrada", "Veja as chaves em `,ticketconfig`.", ok=False)
            return
        if campo not in CAT_FIELDS:
            await _say(ctx, "Campo desconhecido", "Campos: " + ", ".join(f"`{c}`" for c in CAT_FIELDS), ok=False)
            return
        salvo, kind, limit, vazio = CAT_FIELDS[campo]
        value, erro = await parse_value(ctx, kind, valor, limit, vazio)
        if erro:
            await _say(ctx, f"Valor inválido pra `{campo}`", erro, ok=False)
            return
        cats[chave][salvo] = value
        _save_cfg()
        await self._updated(ctx, f"`{chave}` · `{campo}` atualizado")

    @cfg_cat.command(name="remove", aliases=("remover", "apagar", "delete"))
    async def cat_remove(self, ctx, chave: str):
        chave = chave.lower()
        cats = _cats_over(ctx.guild.id)
        if chave not in cats:
            await _say(ctx, "Categoria não encontrada", ok=False)
            return
        if len(cats) <= 1:
            await _say(ctx, "Precisa sobrar uma", "Crie outra categoria antes de apagar esta.", ok=False)
            return
        del cats[chave]
        _save_cfg()
        await self._updated(ctx, f"Categoria `{chave}` apagada",
                            "Tickets já abertos dela continuam funcionando.")

    @cfg_cat.command(name="mover", aliases=("move", "ordem"))
    async def cat_mover(self, ctx, chave: str, posicao: int):
        chave = chave.lower()
        cats = _cats_over(ctx.guild.id)
        if chave not in cats:
            await _say(ctx, "Categoria não encontrada", ok=False)
            return
        ordem = [k for k in cats if k != chave]
        ordem.insert(max(0, min(posicao - 1, len(ordem))), chave)
        novo = {k: cats[k] for k in ordem}
        cats.clear()
        cats.update(novo)
        _save_cfg()
        await self._updated(ctx, f"`{chave}` movida pra posição {ordem.index(chave) + 1}")


async def setup(bot):
    await bot.add_cog(Tickets(bot))
