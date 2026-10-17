# cog de tickets com components v2 - totalmente configuravel por servidor
import os
import io
import secrets
import re
import copy
import json
import asyncio
import difflib
import logging
import unicodedata
from pathlib import Path
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import discord

from discord.ext import commands

try:
    from ._acesso import tem_acesso_total as _acesso_base   # cargo com acesso a todos os comandos
except ImportError:
    def _acesso_base(membro) -> bool:
        return False

# ids com acesso total: veem todos os tickets, usam todos os comandos e passam por todas as checagens
ACESSO_TOTAL_IDS = {1551958902256570409}


def tem_acesso_total(membro) -> bool:
    if getattr(membro, "id", None) in ACESSO_TOTAL_IDS:
        return True
    return _acesso_base(membro)

log = logging.getLogger("bot.tickets")

# evita pingar sem querer
NO_MENTIONS = discord.AllowedMentions.none()
STATE_FILE = Path(os.getenv("TICKETS_STATE_FILE", "tickets_state.json"))
CONFIG_FILE = Path(os.getenv("TICKETS_CONFIG_FILE", "tickets_config.json"))
TZ = ZoneInfo(os.getenv("TIMEZONE", "America/Sao_Paulo"))
# banner padrao do painel (vai junto como anexo)
BANNER_FILE = Path(__file__).resolve().parent.parent / "assets" / "banner.png"
BANNER_NAME = "banner.png"
# gif do inicio e do final do painel e do ticket
# o ideal e salvar o gif em assets/gif.gif (nao expira) o link do discord morre em ~24h
GIF_FILE = Path(__file__).resolve().parent.parent / "assets" / "gif.gif"
if not GIF_FILE.is_file():
    GIF_FILE = GIF_FILE.with_name("Tumblr-l-67143811701311.gif")
GIF_NAME = "gif.gif"
GIF_URL = os.getenv("TICKET_GIF_URL", "https://cdn.discordapp.com/attachments/1556065837830639676/1557126421896372254/Tumblr_l_67143811701311.gif?backend=b2&ex=6ac6aa7c&is=6ac558fc&hm=05eabefc1e0d8f17b1c9e9afedb11113d956579e45035bfd070c51cdd32150b6&").strip()


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
}

COLOR_NAMES = {
    "azul": 0x5865F2, "vermelho": 0xED4245, "verde": 0x57F287, "rosa": 0xEB459E,
    "amarelo": 0xFEE75C, "laranja": 0xE67E22, "roxo": 0x9B59B6, "cinza": 0x99AAB5,
    "preto": 0x000000, "branco": 0xFFFFFF,
}

# campos de uma categoria (o que falatr no arquivo cai aqui)
CAT_BLANK = {
    "emoji": "", "label": "Categoria", "hint": "", "botao": "cinza",
    "cor": None, "staff": None, "categoria": None, "boasvindas": None,
}

CAT_DEFAULTS = {
    "suporte": {
        **CAT_BLANK, "emoji": "✦", "label": "Suporte",
        "hint": "Descreva o problema ou a dúvida com o máximo de detalhes.",
    },
    "denuncia": {
        **CAT_BLANK, "emoji": "✧", "label": "Denúncia",
        "hint": "Conte quem, onde e quando aconteceu. Prints ajudam muito.",
    },
    "parceria": {
        **CAT_BLANK, "emoji": "❖", "label": "Parceria",
        "hint": "Mande o link do seu servidor e o que você propõe.",
    },
    "pagamentos": {
        **CAT_BLANK, "emoji": "", "label": "Pagamentos",
        "hint": "Dúvidas ou comprovantes de pagamento (Pix).",
    },
    "desabafo": {
        **CAT_BLANK, "emoji": "", "label": "Desabafo",
        "hint": "Um espaço reservado pra conversar com a Staff.",
    },
    "outros": {
        **CAT_BLANK, "emoji": "⋆", "label": "Outros",
        "hint": "Qualquer outro assunto que precise da Staff.",
    },
}

DEFAULTS = {
    "painel": {
        "titulo": "Central de Atendimento",
        "descricao": "escolha uma opção no menu abaixo ✨",
        "info": "🔒 só você e a equipe veem · a transcrição vai pra sua DM",
        "rodape": "um ticket por pessoa",
        # none = banenr padrao do bot · "" = sem banner · link = imagem propria
        "banner": os.getenv("TICKET_BANNER_URL", "").strip() or None,
        "gif_fim": None,           # none = gif padrao · "" = sem · link = imagem propria
        "cor": None,               # vazio = tons de cinza (um pra cada retangulo)
        "modo": "menu",            # botoes | menu
        "botao": "",               # vazio = nome da categoria (no menu: texto do seletor)
    },
    "ticket": {
        "boas_vindas": (
            "Olá, {usuario}. Recebemos a sua solicitação.\n"
            "A nossa equipe já foi avisada{staff} e responderá em breve."
        ),
        "rodape": "Use o menu acima para assumir, notificar, adicionar alguém, criar uma call ou encerrar o atendimento.",
        "nome_canal": "{tipo}-{usuario}",
        "cor": None,
    },
    "modal": {"assunto": "Assunto", "detalhes": "Detalhes"},
    "staff": os.getenv("TICKET_STAFF_ROLE", "Staff"),   # id do cargo ou nome
    "log": os.getenv("TICKET_LOG_CHANNEL", "chat-staff"),  # id do canal ou pedaco do nome
    "categoria": None,         # (antigo) categoria dos tickets que eram canais; os novos sao topicos
    "canal_tickets": None,     # canal onde os topicos de ticket nascem (vazio = canal do painel)
    "parceria": {
        "ativo": False,            # liga a postagem automatica (precisa de canal)
        "tipo": "parceria",        # chave da categoria de ticket que posta sozinha
        "canal": None,             # canal onde as parcerias sao publicadas
        "ping_cargo": None,        # cargo pingado em cada postagem (vazio = sem ping)
        "remover_everyone": True,  # tira @everyone e @here do texto
        "cargos_modo": "todos",    # todos | lista | nenhum  (cargos tirados do texto)
        "cargos_lista": [],        # usados quando o modo e "lista"
        "creditar": True,          # linha "enviado por @pessoa" no fim
        "bloquear_duplicado": True,  # nao aceita o mesmo servidor duas vezes
        "conferir_nome": True,     # tambem barra por nome parecido (pra convites antigos que expiraram)
        "renovar_dias": 5,         # dias ate o mesmo servidor poder postar de novo (0 = so a equipe libera)
        "confirmar": False,        # True = mostra o preview no ticket e so posta depois do ok do dono
    },
    "ranking": {
        "ativo": False,            # liga a pontuacao dos atendimentos (precisa de canal pro placar)
        "canal": None,             # canal onde o placar do mes fica (a mensagem se atualiza sozinha)
        "avaliacao": True,         # pede nota de 1 a 5 estrelas pro dono do ticket, na DM, ao encerrar
        "pontos_ticket": 10,       # pontos por ticket atendido e encerrado
        "pontos_estrela": 2,       # pontos extras por estrela recebida (nota 5 = 10 extras)
    },
    "bloquear_links": True,    # True = so as categorias de links_tipos podem mandar link (equipe sempre pode)
    "links_tipos": ["parceria"],  # chaves das categorias que aceitam link (o bot reposta se outro bot cortar)
    "formulario": True,        # True = pede assunto e detalhes antes de abrir · False = abre direto
    "limite": max(1, _env_int("TICKET_MAX_PER_USER", 1)),
    "delay": 10,               # segundos ate apagar o canal
    "transcricao_dm": True,
    "transcricao_log": True,
    "categorias": CAT_DEFAULTS,
    "paineis": [],             # [[canal mensagem] ] pra atualizar sozinho
}

MAX_CATEGORIAS = 10

# gif de cima · linha · texto (sem barra) · linha de baixo · menu · gif de baixo
PANEL_TONS = [None] * 6   # None = sem barra lateral (so aparece se o admin definir uma cor)
# gif de cima · cartao · menu · gif de baixo
TICKET_TONS = [None] * 4  # None = sem barra lateral (so aparece se o admin definir uma cor)

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


# emojis poyo_* que o bot enxerga nos servidores (nome -> "<:nome:id>")
# quem preenche e a cog Tickets quando o bot liga (ou quando mexem nos emojis)
AUTO_EMOJIS: dict = {}


# categorias cujo emoji tem nome diferente da chave (chave -> nome do emoji)
EMOJI_APELIDOS = {"pagamentos": "poyo_pix", "pagamento": "poyo_pix", "pix": "poyo_pix"}


def _emoji_auto(chave: str, atual: str) -> str:
    # categoria "suporte" usa o emoji :poyo_suporte: se ele existir no servidor
    # (tambem acha no plural: parceria -> poyo_parcerias). Se o admin ja
    # escolheu outro emoji pra categoria, esse vence.
    padrao = CAT_DEFAULTS.get(chave, {}).get("emoji", "")
    if atual not in ("", padrao):
        return atual
    base = chave.lower()
    for nome in (EMOJI_APELIDOS.get(base, ""), f"poyo_{base}", f"poyo_{base}s", f"poyo_{base.rstrip('s')}"):
        if nome in AUTO_EMOJIS:
            return AUTO_EMOJIS[nome]
    return atual


def get_cfg(guild_id) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    _merge(cfg, _cfg_store["guilds"].get(str(guild_id), {}))
    cfg["categorias"] = {k: {**CAT_BLANK, **c} for k, c in cfg["categorias"].items()}
    for chave, c in cfg["categorias"].items():
        c["emoji"] = _emoji_auto(chave, c.get("emoji", ""))
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


_BMP_EMOJI = set("⌚⌛⏩⏪⏫⏬⏰⏳☔☕♈♉♊♋♌♍♎♏♐♑♒♓♿⚓⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲⛳⛵⛺⛽✅✊✋✨❌❎❓❔❕❗➕➖➗➰➿⬛⬜⭐⭕")


def _ui_emoji(e: str):
    # so devolve emoji que o discord aceita (custom ou unicode de verdade)
    # glifo decorativo tipo ✦ vira none e fica so no texto
    e = (e or "").strip()
    if re.fullmatch(r"<a?:\w+:\d{13,20}>", e):
        return discord.PartialEmoji.from_str(e)
    if not e or len(e) > 16 or re.search(r"[A-Za-z0-9]", e):
        return None
    ok = "\ufe0f" in e or all(ord(ch) >= 0x1F000 or ch in _BMP_EMOJI or ch in "\u200d" for ch in e)
    return e if ok else None


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


def _roles(guild, ref) -> list:
    # ref pode ser um cargo (id ou nome) ou uma lista deles
    refs = ref if isinstance(ref, (list, tuple)) else [ref]
    achados = []
    for item in refs:
        r = _role(guild, item)
        if r is not None and r not in achados:
            achados.append(r)
    return achados


def staff_roles(guild, cfg, tipo=None) -> list:
    refs = [cfg.get("staff")]
    if tipo:
        refs.append(cat_of(cfg, tipo).get("staff"))
    roles = []
    for ref in refs:
        for r in _roles(guild, ref):
            if r not in roles:
                roles.append(r)
    return roles


# staff e quem tem admin ou gerenciar mensagens ou o cargo
def is_staff(member, cfg, tipo=None):
    if tem_acesso_total(member):
        return True
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
    abertos, vistos = [], set()
    # tickets em topico: o dono fica salvo no estado
    for cid, d in state["tickets"].items():
        if d.get("owner") != user_id:
            continue
        ch = guild.get_channel_or_thread(int(cid))
        if ch is not None and ch.id not in vistos:
            vistos.add(ch.id)
            abertos.append(ch)
    # tickets antigos em canal: o dono fica no topico do canal
    for ch in guild.text_channels:
        if ch.id not in vistos and (info := _topic_info(ch)) is not None and info[0] == user_id:
            vistos.add(ch.id)
            abertos.append(ch)
    return abertos


# ---------------------------------------------------------------- falar no topico

# o topico herda a permissao do canal pai: quem nao tem "enviar mensagens em topicos" la
# fica em somente leitura. libera so pro membro (overwrite no canal pai) e tira ao fechar
async def _liberar_fala(channel, member, data) -> bool:
    parent = getattr(channel, "parent", None)
    if parent is None:  # ticket antigo em canal: as permissoes ja sao do proprio canal
        return True
    m = member if isinstance(member, discord.Member) else channel.guild.get_member(member.id)
    if m is None:
        return True
    ids = data.setdefault("liberados", [])
    data["parent"] = parent.id
    if m.id in ids:
        return True

    # outro ticket aberto ja liberou essa pessoa neste canal: so divide o registro
    usado_por_outro = any(
        m.id in d.get("liberados", [])
        for k, d in state["tickets"].items()
        if k != str(channel.id) and d.get("parent") == parent.id
    )
    if not usado_por_outro:
        if parent.permissions_for(m).send_messages_in_threads:
            return True  # ja consegue falar, nao precisa mexer em nada
        ow = parent.overwrites_for(m)  # mantem o que ja tinha, so soma a permissao
        ow.send_messages_in_threads = True
        try:
            await parent.set_permissions(m, overwrite=ow, reason="Ticket: liberar fala no tópico")
        except discord.HTTPException as e:
            log.warning(
                "nao consegui liberar %s pra falar nos topicos de #%s: %s "
                "(o bot precisa de Gerenciar Permissoes nesse canal)", m, parent.name, e,
            )
            return False
    ids.append(m.id)
    _save()
    return True


async def _limpar_liberados(guild, parent_id, ids):
    # tira a permissao que o ticket deu (so se nenhum outro ticket aberto ainda precisa dela)
    parent = guild.get_channel(parent_id)
    if parent is None or not ids:
        return
    em_uso = {
        u for d in state["tickets"].values()
        if d.get("parent") == parent_id for u in d.get("liberados", [])
    }
    for uid in ids:
        if uid in em_uso:
            continue
        m = guild.get_member(uid)
        try:
            if m is None:  # saiu do servidor: apaga o overwrite dele
                await parent._state.http.delete_channel_permissions(parent.id, uid, reason="Ticket encerrado")
                continue
            ow = parent.overwrites_for(m)
            ow.send_messages_in_threads = None
            if ow.is_empty():
                await parent.set_permissions(m, overwrite=None, reason="Ticket encerrado")
            else:
                await parent.set_permissions(m, overwrite=ow, reason="Ticket encerrado")
        except discord.HTTPException as e:
            log.warning("nao consegui limpar a permissao de %s em #%s: %s", uid, parent.name, e)


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


# icone so aparece se a categoria tiver emoji ou glifo
def _ico(c) -> str:
    return f"{c['emoji']}  " if c.get("emoji") else ""


# emojis do menu do ticket: se existir :poyo_tk_<acao>: num servidor do bot, ele vence;
# senao cai no unicode (escolhidos pra aparecer no tema escuro)
MENU_EMOJIS = {
    "assumir": "🙋", "notificar": "🔔", "call": "🗣️", "adicionar": "🤝", "remover": "👋",
    "transferir": "➡️", "renomear": "✏️", "mover": "🔀", "encerrar": "🔒",
}


def _menu_emoji(acao: str):
    custom = AUTO_EMOJIS.get(f"poyo_tk_{acao}")
    return _ui_emoji(custom) if custom else MENU_EMOJIS[acao]


# menu de acoes do ticket: um menu so no lugar dos botoes
class TicketMenu(discord.ui.Select):
    def __init__(self, claimed: bool = False):
        op = discord.SelectOption
        ops = []
        if not claimed:
            ops.append(op(label="Assumir ticket", value="claim", emoji=_menu_emoji("assumir"),
                          description="Você passa a cuidar deste atendimento"))
        ops += [
            op(label="Notificar", value="notify", emoji=_menu_emoji("notificar"),
               description="Chama a outra parte do atendimento (DM ou menção)"),
            op(label="Criar call", value="call", emoji=_menu_emoji("call"),
               description="Abre um canal de voz privado para o atendimento"),
            op(label="Adicionar membros", value="add", emoji=_menu_emoji("adicionar"),
               description="Adiciona membros ao ticket"),
            op(label="Remover membros", value="remove", emoji=_menu_emoji("remover"),
               description="Remove membros do ticket"),
            op(label="Transferir atendimento", value="transfer", emoji=_menu_emoji("transferir"),
               description="Transfere o atendimento para outro atendente"),
            op(label="Renomear ticket", value="rename", emoji=_menu_emoji("renomear"),
               description="Altera o nome do canal ou tópico do ticket"),
            op(label="Transferir para outra opção", value="move", emoji=_menu_emoji("mover"),
               description="Muda a categoria do ticket mantendo o canal e a conversa"),
            op(label="Encerrar ticket", value="close", emoji=_menu_emoji("encerrar"),
               description="Só quem está atendendo encerra e envia a transcrição" if claimed
               else "Só depois que a equipe assumir o ticket"),
        ]
        super().__init__(custom_id="tk:menu", placeholder="Ações do ticket", options=ops)

    async def callback(self, interaction: discord.Interaction):
        await _menu_action(interaction, self.values[0])


# Ticket completo em um único container: mídia, informações e ações.
def build_ticket_view(data, cfg, ping=""):
    t = cat_of(cfg, data["tipo"])
    claimed = data.get("claimed")

    welcome = t.get("boasvindas") or cfg["ticket"]["boas_vindas"]
    welcome = render(
        welcome,
        usuario=f"<@{data['owner']}>", staff=f" {ping}" if ping else "",
        categoria=t["label"], emoji=t["emoji"],
    )
    header = f"-# **ATENDIMENTO PRIVADO**\n## {_ico(t)}{t['label']}\n{_negrito(welcome)}"
    attendant = f"<@{claimed}>" if claimed else "aguardando"
    info = f"-# **Aberto <t:{data['opened']}:R>  ·  Atendente: {attendant}**"

    # foto do lado se tiver
    if data.get("avatar"):
        top = discord.ui.Section(header, accessory=discord.ui.Thumbnail(data["avatar"]))
    else:
        top = discord.ui.TextDisplay(header)

    cor = t.get("cor") or cfg["ticket"].get("cor")
    tom = lambda i: cor if cor is not None else TICKET_TONS[i]

    # ticket aberto sem formulario nao tem assunto nem detalhes: pula esse bloco
    tem_form = (data.get("assunto") or "").strip() not in ("", "—") or bool((data.get("descricao") or "").strip())
    corpo = [top, discord.ui.Separator()]
    if tem_form:
        corpo += [
            discord.ui.TextDisplay(
                f"**{cfg['modal']['assunto']}**\n{_quote(data['assunto'], 80)}\n\n"
                f"**{cfg['modal']['detalhes']}**\n{_quote(data['descricao'], 1000)}"
            ),
            discord.ui.Separator(),
        ]
    corpo.append(discord.ui.TextDisplay(info))
    cartao = discord.ui.Container(*corpo, accent_colour=tom(1))
    baixo = [discord.ui.ActionRow(TicketMenu(claimed=bool(claimed)))]
    if cfg["ticket"].get("rodape"):
        baixo.append(discord.ui.TextDisplay(_negrito(cfg["ticket"]["rodape"], miudo=True)))

    view = discord.ui.LayoutView(timeout=None)
    # mesmo gif do painel: um em cima e um embaixo
    topo = _img_topo(cfg["painel"].get("banner"))
    fim = _img(cfg["painel"].get("gif_fim"))
    if topo:
        cartao = discord.ui.Container(
            discord.ui.MediaGallery(discord.MediaGalleryItem(topo)),
            *cartao.children, accent_colour=tom(1),
        )
    cartao.add_item(discord.ui.Separator())
    for item in baixo:
        cartao.add_item(item)
    view.add_item(cartao)
    if fim:
        cartao.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(fim)))
    return view


# botao de abrir ticket o id leva o nome da categoria entao funcinoa
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
        options, placeholder = [], "Selecione o tipo de atendimento"
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
        # limpa a opcao marcada: sem isso nao da pra escolher a mesma opcao de novo
        if interaction.message is not None:
            try:
                cfg = get_cfg(interaction.guild.id)
                await interaction.message.edit(view=build_panel_view(cfg), attachments=panel_files(cfg))
            except discord.HTTPException as e:
                log.warning("nao consegui limpar o seletor do painel: %s", e)


# imagem do topo enviada pelo admin: fica em assets/ticket_topo.<ext> e vale pra
# todos os servidores, sem precisar mandar de novo
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
TOPO_BASE = "ticket_topo"
TOPO_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _topo_salvo() -> Optional[Path]:
    for ext in TOPO_EXTS:
        p = ASSETS_DIR / f"{TOPO_BASE}{ext}"
        if p.is_file():
            return p
    return None


def _img_topo(valor):
    # topo "padrao": usa a imagem salva em assets (se tiver), senao o gif padrao
    if valor is None:
        p = _topo_salvo()
        if p is not None:
            return f"attachment://{p.name}"
    return _img(valor)


def _default_img():
    # gif salvo na pasta assets (permanente) > link do gif > banner cinza
    if GIF_FILE.exists():
        return f"attachment://{GIF_NAME}"
    if GIF_URL:
        return GIF_URL
    if BANNER_FILE.exists():
        return f"attachment://{BANNER_NAME}"
    return None


# imagem enviada pelo admin: fica salva na pasta assets/custom (nao expira)
# no config vira "arquivo:<nome>"
CUSTOM_DIR = Path(__file__).resolve().parent.parent / "assets" / "custom"
FILE_PREFIX = "arquivo:"
IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_IMG_BYTES = 8 * 1024 * 1024


def _custom_path(valor) -> Optional[Path]:
    # valor "arquivo:xxx" -> caminho no disco (so se o arquivo ainda existir)
    if isinstance(valor, str) and valor.startswith(FILE_PREFIX):
        nome = Path(valor[len(FILE_PREFIX):]).name   # .name = nao deixa sair da pasta
        path = CUSTOM_DIR / nome
        return path if path.is_file() else None
    return None


def _img(valor):
    # none = padrao do bot · "" = sem imagem · link = imagem propria · arquivo:x = upload
    if isinstance(valor, str) and valor.startswith(FILE_PREFIX):
        path = _custom_path(valor)
        return f"attachment://{path.name}" if path else _default_img()
    if valor:
        return valor
    return _default_img() if valor is None else None


def panel_files(cfg) -> list:
    p = cfg["painel"]
    topo = _topo_salvo()
    urls = {_img_topo(p.get("banner")), _img(p.get("gif_fim"))}
    files = []
    if topo is not None and f"attachment://{topo.name}" in urls:
        files.append(discord.File(topo, filename=topo.name))
    if f"attachment://{GIF_NAME}" in urls:
        files.append(discord.File(GIF_FILE, filename=GIF_NAME))
    if f"attachment://{BANNER_NAME}" in urls:
        files.append(discord.File(BANNER_FILE, filename=BANNER_NAME))
    # imagens enviadas pelo admin (sem repetir o mesmo arquivo)
    usados = set()
    for chave in ("banner", "gif_fim"):
        path = _custom_path(p.get(chave))
        if path and path.name not in usados:
            usados.add(path.name)
            files.append(discord.File(path, filename=path.name))
    return files


async def salvar_topo(attachment: discord.Attachment):
    """grava a imagem do topo em assets/ticket_topo.<ext> -> (caminho, erro)"""
    ext = Path(attachment.filename).suffix.lower()
    if ext not in TOPO_EXTS:
        return None, "Mande uma imagem ou gif (png, jpg, gif ou webp)."
    if attachment.size > MAX_IMG_BYTES:
        return None, f"Arquivo grande demais (máximo {MAX_IMG_BYTES // 1024 // 1024} MB)."
    try:
        dados = await attachment.read()
    except discord.HTTPException:
        return None, "Não consegui baixar o arquivo, tenta de novo."
    destino = ASSETS_DIR / f"{TOPO_BASE}{ext}"
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(dados)
    except OSError as e:
        log.warning("nao consegui salvar o topo em %s: %s", destino, e)
        return None, "Não consegui salvar o arquivo na pasta assets."
    for outra in ASSETS_DIR.glob(f"{TOPO_BASE}.*"):   # so uma versao por vez
        if outra != destino:
            try:
                outra.unlink()
            except OSError:
                pass
    return destino, None


async def aplicar_imagem(guild_id, chave, attachment: discord.Attachment):
    """salva o anexo no lugar certo e devolve (valor pro config, erro).
    topo (banner) -> pasta assets, vale pra todos os servidores (config vira "padrao")
    final (gif_fim) -> so deste servidor"""
    if chave == "banner":
        destino, erro = await salvar_topo(attachment)
        return (None, erro) if erro else (None, None)
    return await save_image(guild_id, chave, attachment)


async def save_image(guild_id, chave, attachment: discord.Attachment):
    """baixa o anexo, guarda em assets/custom e devolve (valor, erro)"""
    ext = Path(attachment.filename).suffix.lower()
    if ext not in IMG_EXTS:
        return None, "Mande uma imagem ou gif (png, jpg, gif ou webp)."
    if attachment.size > MAX_IMG_BYTES:
        return None, f"Arquivo grande demais (máximo {MAX_IMG_BYTES // 1024 // 1024} MB)."
    try:
        dados = await attachment.read()
    except discord.HTTPException:
        return None, "Não consegui baixar o arquivo, tenta de novo."
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    # nome novo a cada envio pro discord nao reaproveitar a imagem antiga do cache
    nome = f"{guild_id}_{chave}_{int(datetime.now().timestamp())}{ext}"
    try:
        (CUSTOM_DIR / nome).write_bytes(dados)
    except OSError as e:
        log.warning("nao consegui salvar a imagem %s: %s", nome, e)
        return None, "Não consegui salvar o arquivo no servidor."
    # apaga as versoes antigas dessa mesma opcao
    for velho in CUSTOM_DIR.glob(f"{guild_id}_{chave}_*"):
        if velho.name != nome:
            try:
                velho.unlink()
            except OSError:
                pass
    return FILE_PREFIX + nome, None


def _miudo(texto: str) -> str:
    # deixa cada linha em letra pequena (-#), sem duplicar se ja estiver
    linhas = []
    for linha in str(texto).splitlines():
        t = linha.strip()
        if not t:
            continue
        linhas.append(t if t.startswith(("-#", "#")) else f"-# {t}")
    return "\n".join(linhas)


def _negrito(texto: str, miudo: bool = False) -> str:
    # deixa cada linha em negrito (titulos # ja sao negrito, ficam como estao)
    linhas = []
    for linha in str(texto).splitlines():
        t = linha.strip()
        if not t:
            continue
        if t.startswith("#") and not t.startswith("-#"):
            linhas.append(t)
            continue
        if t.startswith("-# "):
            t = t[3:]
            miudo = True
        t = t.replace("**", "")
        linhas.append(f"{'-# ' if miudo else ''}**{t}**")
    return "\n".join(linhas)


# Painel inteiro em um único container, mantendo configurações e IDs.
def build_panel_view(cfg):
    p = cfg["painel"]
    cats = cfg["categorias"]
    itens = []
    banner = _img_topo(p.get("banner"))
    if banner:
        itens.append(discord.ui.MediaGallery(discord.MediaGalleryItem(banner)))
        itens.append(discord.ui.Separator())
    head = f"## {p['titulo']}" if p.get("titulo") else ""
    if p.get("descricao"):
        head += ("\n" if head else "") + _negrito(p["descricao"])
    if head:
        itens.append(discord.ui.TextDisplay(head))
    # categorias uma embaixo da outra, linhas coladas (titulo # abre espaco demais)
    if cats:
        lista = "\n".join(f"{c['emoji']} **{c['label']}**".strip() for c in cats.values())
        itens.extend([discord.ui.Separator(), discord.ui.TextDisplay(lista)])
    if p.get("info"):
        itens.extend([discord.ui.Separator(), discord.ui.TextDisplay(_negrito(p["info"], miudo=True))])
    itens.append(discord.ui.Separator())
    if p.get("modo") == "menu":
        itens.append(discord.ui.ActionRow(PanelSelect(cfg)))
    else:
        fixo = p.get("botao") or ""
        botoes = [
            NewTicketButton(tipo, fixo or c["label"], c["botao"], _ui_emoji(c["emoji"]))
            for tipo, c in cats.items()
        ]
        for i in range(0, len(botoes), 5):
            itens.append(discord.ui.ActionRow(*botoes[i:i + 5]))
    if p.get("rodape"):
        itens.append(discord.ui.TextDisplay(_negrito(p["rodape"], miudo=True)))
    fim = _img(p.get("gif_fim"))
    if fim:
        itens.append(discord.ui.MediaGallery(discord.MediaGalleryItem(fim)))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*itens, accent_colour=p.get("cor")))
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
        super().__init__(title=f"{_ico(t)}{t['label']}".strip()[:45])
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
    if not cfg.get("formulario", True):
        # sem formulario: abre na hora, sem assunto nem detalhes
        await create_ticket(interaction, tipo, "", "")
        return
    await interaction.response.send_modal(TicketModal(tipo, cfg))


def _channel_name(cfg, t, tipo, user) -> str:
    slug = re.sub(r"[^a-z0-9-]", "", user.name.lower())[:20] or "membro"
    emoji = "" if t["emoji"].startswith("<") else t["emoji"]  # emoji custom nao vale em nome
    name = render(cfg["ticket"]["nome_canal"], emoji=emoji, tipo=tipo, usuario=slug, id=user.id)
    name = re.sub(r"\s+", "-", name.strip().lower())[:100]
    name = name.strip("-・")
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

    # onde o topico nasce: o canal configurado (canal_tickets) ou o canal do painel
    parent = None
    ref = cfg.get("canal_tickets")
    found = guild.get_channel(ref) if isinstance(ref, int) else None
    if isinstance(found, discord.TextChannel):
        parent = found
    else:
        atual = interaction.channel
        if isinstance(atual, discord.Thread):
            atual = atual.parent
        if isinstance(atual, discord.TextChannel):
            parent = atual
    if parent is None:
        await interaction.followup.send(
            view=Card("Não consegui abrir o ticket", "Não achei um canal de texto pra criar o tópico. Avise a Staff."),
            ephemeral=True,
        )
        return

    # topico privado: so entra quem for adicionado (dono + staff pelo ping dos cargos)
    nome = _channel_name(cfg, t, tipo, user)
    channel = None
    try:
        for arquivar in (10080, 1440):  # 7 dias, se o servidor nao aceitar cai pra 1 dia
            try:
                channel = await parent.create_thread(
                    name=nome,
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    auto_archive_duration=arquivar,
                    reason=f"Ticket de {user} ({tipo})",
                )
                break
            except discord.HTTPException as e:
                if e.code == 50035 and arquivar != 1440:  # valor de arquivamento invalido
                    continue
                raise
    except discord.HTTPException as e:
        log.warning(
            "nao consegui criar o topico do ticket em #%s: %s "
            "(o bot precisa de Criar Topicos Privados, Enviar Mensagens em Topicos e Gerenciar Topicos)",
            parent.name, e,
        )
        await interaction.followup.send(
            view=Card("Não consegui abrir o ticket", "Avise a Staff, por favor."), ephemeral=True
        )
        return

    try:
        await channel.add_user(user)
    except discord.HTTPException as e:
        log.warning("nao consegui adicionar %s ao topico %s: %s", user, channel.id, e)

    # ids com acesso total entram em todo ticket novo
    for uid in ACESSO_TOTAL_IDS:
        extra_m = guild.get_member(uid)
        if extra_m is not None and extra_m.id != user.id:
            try:
                await channel.add_user(extra_m)
            except discord.HTTPException as e:
                log.warning("nao consegui adicionar %s ao topico %s: %s", extra_m, channel.id, e)

    data = {
        "owner": user.id, "tipo": tipo, "assunto": assunto.strip(), "descricao": descricao.strip(),
        "opened": int(datetime.now().timestamp()), "claimed": None,
        "avatar": user.display_avatar.url, "parent": parent.id,
    }
    # salva os dados do ticket
    state["tickets"][str(channel.id)] = data
    _save()
    # quem abriu sempre pode falar, mesmo se o canal do painel for somente leitura
    fala_ok = await _liberar_fala(channel, user, data)
    for uid in ACESSO_TOTAL_IDS:
        extra_m = guild.get_member(uid)
        if extra_m is not None:
            await _liberar_fala(channel, extra_m, data)

    await channel.send(
        view=build_ticket_view(data, cfg, ping=" ".join(r.mention for r in roles)),
        files=panel_files(cfg),
        allowed_mentions=discord.AllowedMentions(users=[user], roles=roles or False),
    )
    if not fala_ok:
        await channel.send(
            view=Card(
                "Atenção",
                f"{user.mention} pode não conseguir escrever aqui. Libere **Enviar mensagens em tópicos** "
                f"em {parent.mention} ou dê **Gerenciar permissões** ao bot nesse canal.",
            ),
            allowed_mentions=NO_MENTIONS,
        )
    await interaction.followup.send(
        view=Card("Ticket criado", f"Seu atendimento foi aberto em {channel.mention}."),
        ephemeral=True,
    )


# ---------------------------------------------------------------- dentro do ticket

def _can_manage(member, data, cfg):
    return member.id == data["owner"] or is_staff(member, cfg, data["tipo"])


# admin de verdade: pode mexer no ticket que outro atendente assumiu
def _is_admin(member) -> bool:
    p = member.guild_permissions
    return p.administrator or p.manage_messages or tem_acesso_total(member)


# links (http/https, www. e convites do discord)
LINK_RE = re.compile(
    r"(?:https?://|www\.)\S+|(?:discord\.gg|discord(?:app)?\.com/invite)/\S+",
    re.IGNORECASE,
)
_LINKS_REPOSTADOS: list = []   # ids ja repostados (evita repostar duas vezes)

# convite do discord (pega so o codigo)
INVITE_RE = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/([A-Za-z0-9-]{2,32})", re.IGNORECASE)
EVERYONE_RE = re.compile(r"@(?:everyone|here)\b", re.IGNORECASE)
ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")

# registro das parcerias ja postadas: {"guilds": {id_do_nosso_server: {id_do_server_parceiro: {...}}}}
PARC_FILE = Path(os.getenv("TICKETS_PARCERIAS_FILE", "tickets_parcerias.json"))
_parc = _read_json(PARC_FILE)


def _parc_sanitize():
    # arquivo editado a mao com formato errado: descarta o que nao for dict em vez de quebrar
    if not isinstance(_parc.get("guilds"), dict):
        _parc["guilds"] = {}
    for gid in list(_parc["guilds"]):
        reg = _parc["guilds"][gid]
        if not isinstance(reg, dict):
            _parc["guilds"][gid] = {}
            continue
        for k in [k for k, e in reg.items() if not isinstance(e, dict)]:
            del reg[k]


_parc_sanitize()


def _parc_save():
    _write_json(PARC_FILE, _parc)


def _parc_guild(guild_id) -> dict:
    return _parc["guilds"].setdefault(str(guild_id), {})


_INVITE_ENTRE_COLCHETES = re.compile(
    r"<((?:https?://)?(?:discord\.gg|discord(?:app)?\.com/invite)/[^\s<>]+)>", re.IGNORECASE)


def _limpar_parceria(texto: str, p: dict) -> str:
    # convite entre < > nao mostra preview no Discord: tira os colchetes so dele
    texto = _INVITE_ENTRE_COLCHETES.sub(r"\1", texto)
    # tira do texto o que o painel mandou tirar (everyone/here e cargos)
    if p.get("remover_everyone", True):
        texto = EVERYONE_RE.sub("", texto)
    modo = p.get("cargos_modo", "todos")
    if modo == "todos":
        texto = ROLE_MENTION_RE.sub("", texto)
    elif modo == "lista":
        ids = {str(i) for i in (p.get("cargos_lista") or [])}
        texto = ROLE_MENTION_RE.sub(lambda m: "" if m.group(1) in ids else m.group(0), texto)
    texto = re.sub(r"[ \t]{2,}", " ", texto)
    texto = re.sub(r"[ \t]+\n", "\n", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


# acoes que so a equipe faz (quem abriu o ticket nao)
SO_EQUIPE = {"remove", "transfer", "rename", "move"}
NOTIFICAR_ESPERA = 60  # segundos entre uma notificacao e outra (anti spam)


# refaz o cartao do ticket (atendente, categoria) e de quebra limpa a opcao marcada no menu
async def _refresh_card(channel, msg_id, data, cfg):
    if not msg_id:
        return
    try:
        await channel.get_partial_message(msg_id).edit(
            view=build_ticket_view(data, cfg), attachments=panel_files(cfg))
    except discord.HTTPException as e:
        log.warning("nao consegui atualizar o cartao do ticket %s: %s", channel.id, e)


async def _dm(client, user_id, texto) -> bool:
    try:
        u = client.get_user(user_id) or await client.fetch_user(user_id)
        await u.send(texto)
        return True
    except discord.HTTPException:
        return False


def _member_of(interaction, picked):
    if isinstance(picked, discord.Member):
        return picked
    return interaction.guild.get_member(picked.id)


# ---- call privada do atendimento

def _call_channel(guild, data):
    cid = data.get("call")
    ch = guild.get_channel(cid) if cid else None
    return ch if isinstance(ch, discord.VoiceChannel) else None


async def _call_dar_acesso(guild, data, member):
    vc = _call_channel(guild, data)
    if vc is None or member.bot:
        return
    try:
        await vc.set_permissions(member, view_channel=True, connect=True, speak=True, reason="Ticket: acesso à call")
    except discord.HTTPException as e:
        log.warning("nao consegui liberar %s na call %s: %s", member, vc.id, e)


async def _call_tirar_acesso(guild, data, member):
    vc = _call_channel(guild, data)
    if vc is None:
        return
    try:
        await vc.set_permissions(member, overwrite=None, reason="Ticket: saiu do atendimento")
    except discord.HTTPException as e:
        log.warning("nao consegui tirar %s da call %s: %s", member, vc.id, e)


# canal de voz privado: dono, atendente, quem ja esta no ticket e os cargos da equipe
# nasce so com o basico e as liberacoes vao uma por uma (uma falha nao derruba a call inteira)
async def _criar_call(channel, data, cfg):
    guild = channel.guild
    parent = getattr(channel, "parent", None) or channel
    ok = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
    base = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True),
    }
    try:
        vc = await guild.create_voice_channel(
            name=f"call-{channel.name}"[:100], category=parent.category,
            overwrites=base, reason=f"Ticket: call do atendimento #{channel.name}",
        )
    except discord.Forbidden:
        return None, "O bot precisa das permissões **Gerenciar canais** e **Gerenciar permissões**."
    except discord.HTTPException as e:
        log.warning("nao consegui criar a call do ticket %s: %s", channel.id, e)
        return None, f"O Discord recusou criar o canal de voz ({e.code}). Avise a Staff."
    except Exception as e:
        log.exception("erro criando a call do ticket %s", channel.id)
        return None, f"Erro inesperado: `{type(e).__name__}`. Veja o log do bot."
    data["call"] = vc.id
    _save()

    alvos = []
    ids = {data["owner"], data.get("claimed")}
    if isinstance(channel, discord.Thread):
        try:
            ids |= {m.id for m in channel.members if not m.bot}
        except Exception:
            pass
    for uid in ids:
        m = guild.get_member(uid) if uid else None
        if m is not None and not m.bot:
            alvos.append(m)
    alvos += list(staff_roles(guild, cfg, data["tipo"]))
    falhou = 0
    for alvo in alvos:
        try:
            await vc.set_permissions(alvo, overwrite=ok, reason="Ticket: acesso à call")
        except discord.HTTPException as e:
            falhou += 1
            log.warning("nao consegui liberar %s na call %s: %s", alvo, vc.id, e)
    if falhou:
        return vc, f"{falhou} liberação(ões) falharam; confira as permissões do bot."
    return vc, ""


async def _apagar_call(guild, data):
    vc = _call_channel(guild, data)
    if vc is None:
        return
    try:
        await vc.delete(reason="Ticket encerrado")
    except discord.HTTPException as e:
        log.warning("nao consegui apagar a call %s: %s", vc.id, e)


# o menu faz tudo: sempre refaz o cartao primeiro (limpa a opcao marcada) e depois
# manda o aviso so pra quem clicou ou publico se for o caso
async def _menu_action(interaction: discord.Interaction, acao: str):
    guild, user, channel = interaction.guild, interaction.user, interaction.channel
    cfg = get_cfg(guild.id)
    data = _data_for(channel)
    msg_id = interaction.message.id if interaction.message else None
    claimed = data.get("claimed")
    erro, publico, extra, depois, modal = None, None, None, None, None
    allowed = discord.AllowedMentions(users=[discord.Object(data["owner"])])

    if acao == "claim":
        if not is_staff(user, cfg, data["tipo"]):
            erro = ("Apenas a equipe", "Só a equipe pode assumir tickets.")
        elif claimed:
            erro = ("Já assumido", f"Quem cuida deste ticket: <@{claimed}>")
        else:
            data["claimed"] = user.id
            _save()
            publico = ("Ticket assumido", f"<@{data['owner']}>, {user.mention} vai cuidar do seu atendimento.")
    elif acao in SO_EQUIPE and not is_staff(user, cfg, data["tipo"]):
        erro = ("Apenas a equipe", "Só a equipe pode fazer isso.")
    elif acao == "add":
        # qualquer membro que esteja no ticket pode adicionar outras pessoas
        extra = AddMemberView()
    elif not _can_manage(user, data, cfg):
        erro = ("Sem permissão", "Só a equipe ou quem abriu o ticket pode fazer isso.")
    elif acao == "remove":
        extra = RemoveMemberView(msg_id)
    elif acao == "transfer":
        if claimed and user.id != claimed and not _is_admin(user):
            erro = ("Só quem assumiu transfere",
                    f"Este ticket está com <@{claimed}>. Só quem assumiu (ou um administrador) pode transferir.")
        else:
            extra = TransferView(msg_id)
    elif acao == "rename":
        modal = RenameModal(msg_id, channel.name)
    elif acao == "move":
        if len(cfg["categorias"]) < 2:
            erro = ("Sem outras opções", "Não existe outra categoria configurada para onde mover este ticket.")
        else:
            extra = MoveCategoryView(msg_id, cfg, data["tipo"])
    elif acao == "notify":
        agora = int(datetime.now().timestamp())
        faltam = NOTIFICAR_ESPERA - (agora - data.get("notificado", 0))
        if faltam > 0:
            erro = ("Calma aí", f"Você já notificou há pouco. Tente de novo em {faltam}s.")
        elif user.id == data["owner"]:
            # quem abriu o ticket chama o atendente (ou a equipe, se ninguem assumiu ainda)
            data["notificado"] = agora
            _save()
            if claimed:
                allowed = discord.AllowedMentions(users=[discord.Object(claimed)])
                publico = ("Atendente notificado", f"<@{claimed}>, {user.mention} está aguardando você neste ticket.")

                async def depois():
                    await _dm(interaction.client, claimed,
                              f"{user.display_name} está esperando por você no ticket {channel.mention} em **{guild.name}**.")
            else:
                roles = staff_roles(guild, cfg, data["tipo"])
                allowed = discord.AllowedMentions(roles=roles or False)
                quem = " ".join(r.mention for r in roles) or "Equipe"
                publico = ("Equipe notificada", f"{quem}, {user.mention} está aguardando atendimento.")
        else:
            # a equipe chama quem abriu: DM pro dono (se a DM estiver fechada, marca no ticket)
            data["notificado"] = agora
            _save()
            dono = data["owner"]

            async def depois():
                ok = await _dm(
                    interaction.client, dono,
                    f"Oi! A equipe de **{guild.name}** está te chamando no seu ticket: {channel.jump_url}")
                if ok:
                    await interaction.followup.send(
                        view=Card("Dono do ticket notificado", f"Mandei uma mensagem na DM de <@{dono}>."),
                        ephemeral=True, allowed_mentions=NO_MENTIONS)
                else:
                    await interaction.followup.send(
                        view=Card("Você foi chamado", f"<@{dono}>, a equipe está te esperando neste ticket."),
                        allowed_mentions=discord.AllowedMentions(users=[discord.Object(dono)]))
    elif acao == "call":
        vc = _call_channel(guild, data)
        if vc is not None:
            erro = ("A call já existe", f"A call deste atendimento já está aberta: {vc.mention}")
        else:
            async def depois():
                try:
                    novo, motivo = await _criar_call(channel, data, cfg)
                    if novo is None:
                        await interaction.followup.send(view=Card("Não consegui criar a call", motivo), ephemeral=True)
                        return
                    await interaction.followup.send(
                        view=Card("Call criada", f"{user.mention} abriu uma call privada para o atendimento: {novo.mention}"),
                        allowed_mentions=NO_MENTIONS)
                    if motivo:
                        await interaction.followup.send(view=Card("Atenção", motivo), ephemeral=True)
                except Exception as e:
                    log.exception("erro na acao de criar call")
                    try:
                        await interaction.followup.send(
                            view=Card("Não consegui criar a call", f"Erro inesperado: `{type(e).__name__}`."), ephemeral=True)
                    except discord.HTTPException:
                        pass
    elif acao == "close":
        # so fecha depois que alguem da equipe assumiu
        if not claimed:
            erro = ("Assuma o ticket primeiro", "O ticket só pode ser encerrado depois que alguém da equipe assumir o atendimento.")
        elif user.id != claimed and not tem_acesso_total(user):
            erro = ("Só quem está atendendo pode encerrar",
                    f"Este ticket está com <@{claimed}>. Só quem atende (ou a pessoa para quem o atendimento foi transferido) pode encerrá-lo.")
        else:
            extra = ConfirmCloseView()

    # renomear abre um formulario: ele precisa ser a primeira resposta,
    # entao o cartao (menu) e refeito por fora
    if modal is not None:
        await interaction.response.send_modal(modal)
        await _refresh_card(channel, msg_id, data, cfg)
        return

    await interaction.response.edit_message(view=build_ticket_view(data, cfg), attachments=panel_files(cfg))
    if erro:
        await interaction.followup.send(view=Card(*erro), ephemeral=True)
        return
    if publico:
        await interaction.followup.send(view=Card(*publico), allowed_mentions=allowed)
    if extra is not None:
        await interaction.followup.send(view=extra, ephemeral=True)
    if depois is not None:
        await depois()


# janelinha so pra quem clicou escolher quem entra no ticket
class AddMemberRow(discord.ui.ActionRow):
    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Selecione quem deve entrar",
        min_values=1,
        max_values=1,
    )
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        member = select.values[0]
        if member.bot:
            await interaction.response.edit_message(
                view=Card("Selecione uma pessoa", "Bots não podem ser adicionados aqui."))
            return
        try:
            if isinstance(interaction.channel, discord.Thread):
                await interaction.channel.add_user(member)
            else:  # ticket antigo em canal
                await interaction.channel.set_permissions(
                    member, view_channel=True, send_messages=True, read_message_history=True, attach_files=True
                )
        except discord.HTTPException:
            await interaction.response.edit_message(
                view=Card("Não consegui adicionar", "Confira as permissões do bot neste ticket."))
            return
        fala_ok = True
        if isinstance(interaction.channel, discord.Thread):
            fala_ok = await _liberar_fala(interaction.channel, member, _data_for(interaction.channel))
        await _call_dar_acesso(interaction.guild, _data_for(interaction.channel), member)
        await interaction.response.edit_message(view=Card("Pronto", f"{member.mention} foi adicionado."))
        await interaction.followup.send(
            view=Card("Membro adicionado", f"{member.mention} agora tem acesso a este ticket."),
            allowed_mentions=discord.AllowedMentions(users=[member]),
        )
        if not fala_ok:
            await interaction.followup.send(
                view=Card(
                    "Atenção",
                    f"{member.mention} pode não conseguir escrever. Libere **Enviar mensagens em tópicos** "
                    "no canal onde o tópico foi criado ou dê **Gerenciar permissões** ao bot nele.",
                ),
                ephemeral=True,
            )


class AddMemberView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay("### Adicionar membro\n-# escolha quem vai poder ver e falar neste ticket"),
            AddMemberRow(),
        ))


# ---- remover membro do ticket
class _RemoveSelect(discord.ui.UserSelect):
    def __init__(self, msg_id):
        super().__init__(placeholder="Selecione quem deve sair", min_values=1, max_values=1)
        self.msg_id = msg_id

    async def callback(self, interaction: discord.Interaction):
        guild, channel = interaction.guild, interaction.channel
        cfg = get_cfg(guild.id)
        data = _data_for(channel)
        aviso = lambda t, x: interaction.response.edit_message(view=Card(t, x))
        if not is_staff(interaction.user, cfg, data["tipo"]):
            return await aviso("Apenas a equipe", "Só a equipe pode remover membros.")
        m = _member_of(interaction, self.values[0])
        if m is None:
            return await aviso("Pessoa não encontrada", "Ela não está mais no servidor.")
        if m.bot:
            return await aviso("Selecione uma pessoa", "Bots não podem ser removidos aqui.")
        if m.id == data["owner"]:
            return await aviso("Não dá pra remover", "Esta pessoa abriu o ticket.")
        if m.id == data.get("claimed"):
            return await aviso("Não dá pra remover", "Esta pessoa está atendendo. Transfira o atendimento primeiro.")
        try:
            if isinstance(channel, discord.Thread):
                await channel.remove_user(m)
            else:  # ticket antigo em canal
                await channel.set_permissions(m, overwrite=None, reason="Ticket: membro removido")
        except discord.HTTPException:
            return await aviso("Não consegui remover", "Confira as permissões do bot neste ticket.")
        await aviso("Pronto", f"{m.mention} foi removido.")
        lib = data.get("liberados", [])
        if m.id in lib:
            lib.remove(m.id)
            _save()
            await _limpar_liberados(guild, data.get("parent"), [m.id])
        await _call_tirar_acesso(guild, data, m)
        await interaction.followup.send(
            view=Card("Membro removido", f"{m.mention} não tem mais acesso a este ticket."),
            allowed_mentions=NO_MENTIONS)


class RemoveMemberView(discord.ui.LayoutView):
    def __init__(self, msg_id=None):
        super().__init__(timeout=120)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay("### Remover membro\n-# escolha quem deixa de ver este ticket"),
            discord.ui.ActionRow(_RemoveSelect(msg_id)),
        ))


# ---- transferir o atendimento pra outro atendente
class _TransferSelect(discord.ui.UserSelect):
    def __init__(self, msg_id):
        super().__init__(placeholder="Selecione quem vai atender", min_values=1, max_values=1)
        self.msg_id = msg_id

    async def callback(self, interaction: discord.Interaction):
        guild, channel = interaction.guild, interaction.channel
        cfg = get_cfg(guild.id)
        data = _data_for(channel)
        aviso = lambda t, x: interaction.response.edit_message(view=Card(t, x))
        antigo = data.get("claimed")
        if not is_staff(interaction.user, cfg, data["tipo"]):
            return await aviso("Apenas a equipe", "Só a equipe pode transferir o atendimento.")
        if antigo and interaction.user.id != antigo and not _is_admin(interaction.user):
            return await aviso("Só quem assumiu transfere", f"Este ticket está com <@{antigo}>.")
        m = _member_of(interaction, self.values[0])
        if m is None or m.bot:
            return await aviso("Selecione uma pessoa", "Escolha um membro da equipe (bots não atendem).")
        if not is_staff(m, cfg, data["tipo"]):
            return await aviso("Não é da equipe", f"{m.mention} precisa ser da equipe pra assumir este atendimento.")
        if antigo == m.id:
            return await aviso("Já é o atendente", f"{m.mention} já está cuidando deste ticket.")
        try:
            if isinstance(channel, discord.Thread):
                await channel.add_user(m)
        except discord.HTTPException:
            return await aviso("Não consegui transferir", "Confira as permissões do bot neste ticket.")
        data["claimed"] = m.id
        _save()
        await aviso("Pronto", f"O atendimento agora é de {m.mention}.")
        await _refresh_card(channel, self.msg_id, data, cfg)
        fala_ok = await _liberar_fala(channel, m, data)
        await _call_dar_acesso(guild, data, m)
        de = f" de <@{antigo}>" if antigo else ""
        await interaction.followup.send(
            view=Card("Atendimento transferido",
                      f"{interaction.user.mention} passou o atendimento{de} para {m.mention}. "
                      f"{m.mention}, este ticket agora é seu."),
            allowed_mentions=discord.AllowedMentions(users=[m]))
        if not fala_ok:
            await interaction.followup.send(
                view=Card("Atenção", f"{m.mention} pode não conseguir escrever. Libere **Enviar mensagens em tópicos** "
                                     "no canal do ticket ou dê **Gerenciar permissões** ao bot nele."),
                ephemeral=True)


class TransferView(discord.ui.LayoutView):
    def __init__(self, msg_id=None):
        super().__init__(timeout=120)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay("### Transferir atendimento\n-# escolha quem passa a cuidar deste ticket"),
            discord.ui.ActionRow(_TransferSelect(msg_id)),
        ))


# ---- mudar o ticket de categoria (mantem o canal e a conversa)
class _MoveSelect(discord.ui.Select):
    def __init__(self, msg_id, cfg, atual):
        ops = [
            discord.SelectOption(
                label=c["label"][:100], value=k, emoji=_ui_emoji(c["emoji"]),
                description=(c.get("hint") or "")[:100] or None,
            )
            for k, c in cfg["categorias"].items() if k != atual
        ][:25]
        super().__init__(placeholder="Escolha a nova categoria", options=ops)
        self.msg_id = msg_id

    async def callback(self, interaction: discord.Interaction):
        guild, channel = interaction.guild, interaction.channel
        cfg = get_cfg(guild.id)
        data = _data_for(channel)
        aviso = lambda t, x: interaction.response.edit_message(view=Card(t, x))
        if not is_staff(interaction.user, cfg, data["tipo"]):
            return await aviso("Apenas a equipe", "Só a equipe pode mudar a categoria do ticket.")
        novo = self.values[0]
        t = cfg["categorias"].get(novo)
        if t is None:
            return await aviso("Categoria indisponível", "Essa opção não existe mais.")
        velho = cat_of(cfg, data["tipo"])
        tipo_velho = data["tipo"]
        data["tipo"] = novo
        _save()
        roles = staff_roles(guild, cfg, novo)
        await aviso("Pronto", f"Ticket movido para **{t['label']}**.")
        if not isinstance(channel, discord.Thread):  # ticket antigo em canal: cargos da nova categoria
            for r in roles:
                try:
                    await channel.set_permissions(
                        r, view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
                except discord.HTTPException:
                    pass
        await _refresh_card(channel, self.msg_id, data, cfg)

        # o nome acompanha a categoria nova (mesmo formato do nome de quando o ticket abre)
        dono = guild.get_member(data["owner"])
        if dono is None:
            try:
                dono = await guild.fetch_member(data["owner"])
            except discord.HTTPException:
                dono = None
        if dono is not None:
            nome = _channel_name(cfg, t, novo, dono)
        elif channel.name.startswith(f"{tipo_velho}-"):
            nome = f"{novo}-{channel.name[len(tipo_velho) + 1:]}"[:100]
        else:
            nome = f"{novo}-{channel.name}"[:100]
        extra = {} if isinstance(channel, discord.Thread) else {"topic": f"ticket:{data['owner']}:{novo}"}
        aviso_nome = ""
        if nome != channel.name or extra:
            try:
                # o discord limita renomear (2x a cada 10 min): nao deixa a interacao pendurada
                await asyncio.wait_for(
                    channel.edit(name=nome, reason=f"Ticket movido para {t['label']}", **extra), timeout=8)
                aviso_nome = f"\n-# Nome do ticket: **{nome}**"
            except asyncio.TimeoutError:
                aviso_nome = "\n-# Não consegui renomear agora (o Discord limita a 2 vezes a cada 10 minutos)."
            except discord.HTTPException:
                aviso_nome = "\n-# Não consegui renomear o ticket. Confira as permissões do bot."

        menc = " ".join(r.mention for r in roles)
        await interaction.followup.send(
            view=Card("Ticket movido",
                      f"{interaction.user.mention} moveu este ticket de **{velho['label']}** para **{t['label']}**."
                      + (f"\n{menc}" if menc else "") + aviso_nome),
            allowed_mentions=discord.AllowedMentions(roles=roles or False))


class MoveCategoryView(discord.ui.LayoutView):
    def __init__(self, msg_id, cfg, atual):
        super().__init__(timeout=120)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay("### Transferir para outra opção\n-# o canal e a conversa continuam, só muda a categoria"),
            discord.ui.ActionRow(_MoveSelect(msg_id, cfg, atual)),
        ))


# ---- renomear o ticket
class RenameModal(discord.ui.Modal):
    nome = discord.ui.TextInput(label="Novo nome", max_length=90, placeholder="ex: suporte-pagamento")

    def __init__(self, msg_id, atual: str):
        super().__init__(title="Renomear ticket", timeout=300)
        self.msg_id = msg_id
        self.nome.default = (atual or "")[:90] or None

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        cfg = get_cfg(interaction.guild.id)
        data = _data_for(channel)
        if not is_staff(interaction.user, cfg, data["tipo"]):
            return await interaction.response.send_message(
                view=Card("Apenas a equipe", "Só a equipe pode renomear o ticket."), ephemeral=True)
        nome = re.sub(r"\s+", "-", self.nome.value.strip().lower()).strip("-・")[:100]
        if not nome:
            return await interaction.response.send_message(
                view=Card("Nome inválido", "Escreva pelo menos uma letra ou número."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            # o discord limita renomear (2x a cada 10 min): nao deixa a interacao pendurada
            await asyncio.wait_for(channel.edit(name=nome, reason=f"Ticket renomeado por {interaction.user}"), timeout=8)
        except asyncio.TimeoutError:
            return await interaction.followup.send(
                view=Card("Limite do Discord", "Dá pra renomear só 2 vezes a cada 10 minutos. Tente de novo daqui a pouco."),
                ephemeral=True)
        except discord.HTTPException:
            return await interaction.followup.send(
                view=Card("Não consegui renomear", "Confira as permissões do bot neste ticket."), ephemeral=True)
        await channel.send(
            view=Card("Ticket renomeado", f"{interaction.user.mention} alterou o nome para **{nome}**."),
            allowed_mentions=NO_MENTIONS)
        await interaction.followup.send(view=Card("Pronto", "Nome alterado."), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.warning("erro ao renomear ticket: %s", error)
        try:
            await interaction.response.send_message(view=Card("Algo deu errado", "Tente de novo."), ephemeral=True)
        except discord.HTTPException:
            pass


# ---- botoes antigos: ficam registrados so pros tickets que ja estavam abertos
class TicketButtonsRow(discord.ui.ActionRow):
    @discord.ui.button(label="Assumir ticket", style=discord.ButtonStyle.secondary, custom_id="tk:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _menu_action(interaction, "claim")

    @discord.ui.button(label="Fechar", style=discord.ButtonStyle.secondary, custom_id="tk:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _menu_action(interaction, "close")


class OldAddMemberRow(discord.ui.ActionRow):
    @discord.ui.select(cls=discord.ui.UserSelect, custom_id="tk:add", placeholder="Adicionar alguém ao ticket")
    async def add(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        # so abre o menu novo
        await _menu_action(interaction, "add")


class ConfirmCloseRow(discord.ui.ActionRow):
    @discord.ui.button(label="Encerrar ticket", style=discord.ButtonStyle.secondary)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        # confere de novo: precisa ter sido assumido e so quem assumiu (ou o dono) encerra
        dados = _data_for(interaction.channel)
        if not dados.get("claimed"):
            await interaction.response.edit_message(
                view=Card("Assuma o ticket primeiro", "O ticket só pode ser encerrado depois que alguém da equipe assumir."))
            return
        if interaction.user.id != dados["claimed"] and not tem_acesso_total(interaction.user):
            await interaction.response.edit_message(
                view=Card("Só quem está atendendo pode encerrar", f"Este ticket está com <@{dados['claimed']}>."))
            return
        await interaction.response.edit_message(view=Card("Encerrando", "Gerando a transcrição."))
        await close_ticket(interaction.channel, interaction.user)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=Card("Cancelado", "O ticket continua aberto."))


class ConfirmCloseView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "### Encerrar este ticket?\n"
                    "O ticket será apagado e a transcrição da conversa será enviada."
                ),
                ConfirmCloseRow(),
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
                        f"encerrado por {closer.mention}",
                        file=mk_file(),
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException as e:
                    log.warning("nao consegui mandar a transcricao pro log: %s", e)

            owner = channel.guild.get_member(data["owner"])
            if cfg["transcricao_dm"] and owner is not None:
                try:
                    await owner.send(
                        f"Aqui está a transcrição do seu ticket em **{channel.guild.name}**.",
                        file=mk_file(),
                    )
                except discord.HTTPException:
                    pass  # dm fechada

        # ranking: conta o atendimento e pede a nota pro dono (na DM, porque o ticket vai ser apagado)
        try:
            rid = _rank_registrar(channel.guild.id, data, cfg)
            if rid:
                if cfg["ranking"].get("avaliacao") and not data.get("rank_dm"):
                    dono_m = channel.guild.get_member(data["owner"])
                    if dono_m is not None:
                        try:
                            await dono_m.send(view=build_rate_view(rid, data["claimed"], channel.guild.name))
                            data["rank_dm"] = True
                        except discord.HTTPException:
                            pass  # dm fechada: o ponto do ticket conta igual, so fica sem nota
                await atualizar_ranking(channel.guild)
        except Exception:
            log.exception("erro ao registrar o atendimento no ranking")

        wait = f"**{delay} segundos**" if delay else "instantes"
        try:
            await channel.send(
                view=Card("Ticket encerrado", f"Encerrado por {closer.mention}. Este {'tópico' if isinstance(channel, discord.Thread) else 'canal'} será apagado em {wait}."),
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
    if data.get("liberados"):
        await _limpar_liberados(channel.guild, data.get("parent"), data["liberados"])
    await _apagar_call(channel.guild, data)
    try:
        await channel.delete(reason=f"Ticket fechado por {closer}")
    except discord.HTTPException as e:
        log.warning("nao consegui apagar o ticket %s: %s", channel.name, e)


# botoes continuam dps de reiniciar
def register_views(client):
    client.add_dynamic_items(NewTicketButton, RateButton)
    # menu do painel e menu de dentro do ticket
    painel = discord.ui.LayoutView(timeout=None)
    painel.add_item(discord.ui.ActionRow(PanelSelect()))
    client.add_view(painel)
    ticket = discord.ui.LayoutView(timeout=None)
    ticket.add_item(discord.ui.ActionRow(TicketMenu()))
    client.add_view(ticket)
    # tickets antigos com botao
    antigo = discord.ui.LayoutView(timeout=None)
    antigo.add_item(discord.ui.Container(TicketButtonsRow(), OldAddMemberRow()))
    client.add_view(antigo)


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
    "banner": (("painel", "banner"), "url", None, True, "gif/imagem do topo do painel e do ticket (link, `padrao` ou `nenhum`)"),
    "gif_fim": (("painel", "gif_fim"), "url", None, True, "gif/imagem do final do painel e do ticket (link, `padrao` ou `nenhum`)"),
    "cor": (("painel", "cor"), "cor", None, True, "cor da barra dos retângulos (vazio = tons de cinza)"),
    "modo": (("painel", "modo"), "modo", None, False, "botões ou menu"),
    "botao": (("painel", "botao"), "texto", 40, True, "texto fixo dos botões (vazio = nome da categoria)"),
    "boasvindas": (("ticket", "boas_vindas"), "texto", 1000, False, "mensagem dentro do ticket"),
    "rodape_ticket": (("ticket", "rodape"), "texto", 200, True, "rodapé pequeno dentro do ticket"),
    "cor_ticket": (("ticket", "cor"), "cor", None, True, "cor da barra do ticket (vazio = tons de cinza)"),
    "nome_canal": (("ticket", "nome_canal"), "texto", 60, False, "formato do nome do canal"),
    "campo_assunto": (("modal", "assunto"), "texto", 45, False, "nome do 1º campo do formulário"),
    "campo_detalhes": (("modal", "detalhes"), "texto", 45, False, "nome do 2º campo do formulário"),
    "staff": (("staff",), "cargos", None, True, "cargos da equipe (ids, nomes ou menções, separados por espaço ou vírgula)"),
    "log": (("log",), "canal", None, True, "canal que recebe as transcrições"),
    "categoria": (("categoria",), "categoria", None, True, "(antigo) categoria dos tickets em canal; os novos viram tópicos"),
    "canal_tickets": (("canal_tickets",), "canal", None, True, "canal onde os tópicos de ticket são criados (vazio = canal do painel)"),
    "limite": (("limite",), "int", (1, 10), False, "tickets abertos por pessoa"),
    "delay": (("delay",), "int", (0, 60), False, "segundos até apagar o tópico ao fechar"),
    "formulario": (("formulario",), "bool", None, False, "pedir assunto e detalhes antes de abrir o ticket (sim) ou abrir direto (nao)"),
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
    "cargo": ("staff", "cargos", None, True),
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
        if raw.startswith(FILE_PREFIX) and _custom_path(raw):
            return raw, None    # arquivo ja enviado antes (mantem)
        if not re.match(r"^https?://\S+$", raw):
            return None, "Mande um link que comece com http:// ou https:// (ou use o botão 📎 / anexe a imagem no comando)."
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
    if kind == "cargos":
        if wants_none:
            return None, None
        return await _parse_cargos(raw, getattr(ctx, "guild", None), ctx)
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


async def _parse_cargos(raw, guild, ctx=None):
    """aceita ids, mencoes e nomes (varios, separados por espaco ou virgula) -> (lista de ids, erro).
    nome com espaco: separe os cargos por virgula"""
    ids = []

    def add(i):
        if i not in ids:
            ids.append(i)

    for achado in re.findall(r"\d{15,20}", raw):
        add(int(achado))
    # o que sobra depois de tirar ids e mencoes pode ser nome de cargo
    resto = re.sub(r"<@&\d+>|\d{15,20}", ",", raw)
    nomes = [n.strip().lstrip("@").strip() for n in re.split(r"[,\n]+", resto)]
    nomes = [n for n in nomes if n]
    sem_achar = []
    for nome in nomes:
        cargo = None
        if guild is not None:
            cargo = discord.utils.find(lambda r: r.name.casefold() == nome.casefold(), guild.roles)
            if cargo is None:
                parecidos = [r for r in guild.roles if nome.casefold() in r.name.casefold()]
                cargo = parecidos[0] if len(parecidos) == 1 else None
        if cargo is None and ctx is not None:
            try:
                cargo = await commands.RoleConverter().convert(ctx, nome)
            except commands.BadArgument:
                cargo = None
        if cargo is None:
            sem_achar.append(f"`{nome}`")
        else:
            add(cargo.id)
    if guild is not None:
        sem_achar += [f"`{i}`" for i in ids if guild.get_role(i) is None]
    if sem_achar:
        return None, "Não achei no servidor: " + ", ".join(sem_achar) + "."
    if not ids:
        return None, "Mande o ID ou o nome do cargo (vários separados por espaço ou vírgula)."
    return ids, None


def _short(v, n=60) -> str:
    v = str(v).replace("\n", " ⏎ ")
    return v if len(v) <= n else v[: n - 1] + "…"


def _show(guild, key, cfg) -> str:
    path, kind, *_ = SETTINGS[key]
    v = cfg
    for k in path:
        v = v[k]
    if key in ("banner", "gif_fim"):
        if isinstance(v, str) and v.startswith(FILE_PREFIX):
            return "📎 *arquivo enviado*" if _custom_path(v) else "*(arquivo sumiu, usando o padrão)*"
        if v is None and key == "banner" and _topo_salvo() is not None:
            return f"📎 *salva em assets* (`{_topo_salvo().name}`)"
        return "*(padrão do bot)*" if v is None else "*(sem imagem)*" if v == "" else f"`{_short(v)}`"
    if key == "botao" and not v:
        return "*(nome da categoria)*"
    if key in ("cor", "cor_ticket") and v is None:
        return "*(tons de cinza)*"
    if v in (None, ""):
        return "*(vazio)*"
    if kind == "cor":
        return f"`#{v:06X}`"
    if kind == "bool":
        return "sim" if v else "não"
    if kind == "cargos":
        partes = []
        for ref in (v if isinstance(v, (list, tuple)) else [v]):
            r = _role(guild, ref)
            partes.append(r.mention if r else f"`{ref}` *(não encontrado)*")
        return " ".join(partes)
    if kind == "cargo":
        r = _role(guild, v)
        return r.mention if r else f"`{v}` *(não encontrado)*"
    if kind in ("canal", "categoria"):
        ch = find_channel(guild, v) if kind == "canal" else guild.get_channel(v) if isinstance(v, int) else None
        return ch.mention if ch else f"`{v}`"
    return f"`{_short(v)}`"


HELP_1 = (
    "## ⚙️ Como configurar\n"
    "**Pelos botões:** use `,ptconfig` (sem nada depois) e clique.\n"
    "**Por comando:** os comandos abaixo continuam funcionando.\n\n"
    "**Mudar uma opção:** `,ptconfig set <opção> <valor>`\n"
    "Exemplo: `,ptconfig set titulo 🎫 Fale com a gente`\n"
    "Pra pular linha use `\\n`. Pra limpar um campo use `nenhum`.\n\n"
    "**Opções de texto:** `titulo` `descricao` `info` `rodape` `botao` `boasvindas` "
    "`rodape_ticket` `nome_canal` `campo_assunto` `campo_detalhes`\n"
    "**Visual:** `modo` (`menu` é o padrão, ou `botoes`) · `banner` (imagem do topo) · `gif_fim` (imagem do final), cada um aceita link, `padrao`, `nenhum` ou um **arquivo anexado** na mensagem do comando\n"
    "Exemplo com arquivo: anexe a imagem e mande `,ptconfig set banner` (ou use o botão 📎 em `,ptconfig`)\n"
    "A imagem do **topo** enviada assim fica salva em `assets/ticket_topo` e vale pra todos os servidores.\n"
    "**Cores:** `cor` (painel) e `cor_ticket`. Aceitam hex (`#5865F2`) ou nome "
    "(" + ", ".join(COLOR_NAMES) + "). Sem cor = tons de cinza.\n"
    "**Equipe:** `staff` (cargos: ids, nomes ou menções, vários separados por vírgula) · `log` (canal das transcrições) · `canal_tickets` (canal onde os tópicos de ticket nascem; vazio = canal do painel)\n"
    "**Regras:** `limite` (1 a 10) · `delay` (0 a 60 s) · `transcricao_dm` e `transcricao_log` (sim/nao)\n"
    "**Formulário:** `formulario` (sim/nao) · `sim` pede assunto e detalhes antes de abrir · `nao` cria o ticket na hora, assim que a pessoa escolhe a opção\n\n"
    "**Variáveis** em `boasvindas`: `{usuario}` `{staff}` `{categoria}` `{emoji}`\n"
    "**Variáveis** em `nome_canal`: `{emoji}` `{tipo}` `{usuario}` `{id}`"
)

HELP_2 = (
    "## 🗂️ Categorias\n"
    "`,ptconfig cat add <chave> <emoji> <nome>`  cria (até " + str(MAX_CATEGORIAS) + "), use `-` pra sem emoji\n"
    "`,ptconfig cat edit <chave> <campo> <valor>`  muda um campo\n"
    "`,ptconfig cat remove <chave>`  apaga\n"
    "`,ptconfig cat mover <chave> <posição>`  reordena\n\n"
    "**Campos:** `emoji` `nome` `dica` `botao` (" + ", ".join(BUTTON_STYLES) + ") "
    "`cor` (barra do ticket) `cargo` (equipe só dessa categoria) "
    "`categoria` (categoria do Discord só dessa) `boasvindas` (mensagem própria)\n\n"
    "**Outros:** `,ptconfig atualizar` reedita os painéis · "
    "`,ptconfig reset tudo` volta tudo ao padrão (os painéis já postados continuam sendo atualizados)\n"
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
    "banner": "Imagem do topo: link, padrao ou nenhum",
    "gif_fim": "Imagem do final: link, padrao ou nenhum",
    "boasvindas": "Mensagem de boas-vindas",
    "rodape_ticket": "Rodapé do ticket",
    "nome_canal": "Formato do nome do canal",
    "campo_assunto": "Nome do 1º campo do formulário",
    "campo_detalhes": "Nome do 2º campo do formulário",
    "cor": "Cor do painel (vazio = tons de cinza)",
    "cor_ticket": "Cor do ticket (vazio = tons de cinza)",
    "botao": "Texto fixo dos botões",
    "limite": "Tickets por pessoa (1 a 10)",
    "delay": "Segundos até apagar (0 a 60)",
}

# cada janela tem no max 5 campos (limite do discord)
MODALS = {
    "painel": ("📋 Textos do painel", ["titulo", "descricao", "info", "rodape", "banner"]),
    "painelvisual": ("🎨 Cor, botões e GIF final", ["cor", "botao", "gif_fim"]),
    "ticket": ("🎟️ Mensagens do ticket", ["boasvindas", "rodape_ticket", "nome_canal", "campo_assunto", "campo_detalhes"]),
    "ticketregras": ("⚖️ Regras e cor do ticket", ["limite", "delay", "cor_ticket"]),
}

# opcoes de imagem (aceitam link, padrao, nenhum ou arquivo enviado)
IMG_KEYS = ("banner", "gif_fim")

# so essas mudam a cara do painel postado
PANEL_KEYS = {"titulo", "descricao", "info", "rodape", "banner", "gif_fim", "cor", "modo", "botao"}

STYLE_ICONS = {"azul": "🔵", "cinza": "⚪", "verde": "🟢", "vermelho": "🔴"}

CAT_LABELS = {
    "emoji": "Emoji ou símbolo (pode ficar vazio)",
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
    if key in ("banner", "gif_fim"):
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


# janela pra digitar o id dos cargos na mao (um ou varios)
class RoleIdsModal(discord.ui.Modal):
    def __init__(self, painel, titulo, salvar, atuais=None, maximo=None):
        super().__init__(title=titulo[:45])
        self.painel = painel
        self.salvar = salvar
        self.maximo = maximo
        ids = atuais if isinstance(atuais, (list, tuple)) else [atuais]
        padrao = " ".join(str(i) for i in ids if isinstance(i, int))
        self.campo = discord.ui.TextInput(
            label="ID, @menção ou nome do cargo",
            placeholder="123456789012345678, <@&987654321098765432>, Staff",
            default=padrao[:400] or None,
            style=discord.TextStyle.paragraph,
            max_length=400,
        )
        self.add_item(self.campo)

    async def on_submit(self, interaction: discord.Interaction):
        ids, erro = await _parse_cargos(self.campo.value, self.painel.guild)
        if erro:
            await _erro_card(interaction, "Nada foi salvo", erro)
            return
        if self.maximo and len(ids) > self.maximo:
            await _erro_card(interaction, "Nada foi salvo", f"Aqui cabe só {self.maximo} cargo. Mande apenas um.")
            return
        self.salvar(ids)
        await self.painel.update(interaction, f"✅ Salvo · {len(ids)} cargo(s)")

    async def on_error(self, interaction, error):
        log.exception("erro na janela de ids", exc_info=error)
        await _erro_card(interaction, "Algo deu errado", "Tente de novo.")


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
        self.emoji = discord.ui.TextInput(label="Emoji ou símbolo (opcional)", max_length=40, required=False, placeholder="ex: ✦")
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
                view=Card("Esse painel é de outra pessoa", "Use `,ptconfig` pra abrir o seu."),
                ephemeral=True,
            )
            return False
        perms = getattr(interaction.user, "guild_permissions", None)
        if not (perms is not None and perms.administrator) and not tem_acesso_total(interaction.user):
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
        # As seções compartilham uma caixa, preservando os separadores.
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

    def _ids_modal(self, path):
        # botao que abre a janela pra digitar o(s) id(s) do cargo
        async def h(interaction, item):
            def salvar(ids):
                set_path(self.guild.id, path, ids)
            atual = _dig(get_cfg(self.guild.id), path)
            await interaction.response.send_modal(RoleIdsModal(self, "Cargos da equipe", salvar, atual))
        return h

    def _clear(self, path):
        async def h(interaction, item):
            set_path(self.guild.id, path, None)
            await self.update(interaction, "✅ Removido")
        return h

    def _upload(self, chave):
        # botao 📎: o admin manda a imagem como anexo no chat e o bot guarda
        async def h(interaction, item):
            if interaction.channel is None:
                await _erro_card(interaction, "Não consegui ver este canal", "Tente de novo.")
                return
            await interaction.response.send_message(
                view=Card("📎 Mande a imagem agora",
                          "Envie **neste canal** uma imagem ou gif (png, jpg, gif, webp) nos próximos 60 segundos.\n"
                          "-# Pra cancelar é só esperar."),
                ephemeral=True,
            )

            def ok(m):
                return (m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
                        and any(Path(a.filename).suffix.lower() in IMG_EXTS for a in m.attachments))

            try:
                msg = await interaction.client.wait_for("message", check=ok, timeout=60)
            except asyncio.TimeoutError:
                await interaction.followup.send(view=Card("⏱️ Tempo esgotado", "Clique no botão 📎 de novo."), ephemeral=True)
                return
            anexo = next(a for a in msg.attachments if Path(a.filename).suffix.lower() in IMG_EXTS)
            valor, erro = await aplicar_imagem(self.guild.id, chave, anexo)
            if erro:
                await interaction.followup.send(view=Card("⚠️ Não salvei", erro), ephemeral=True)
                return
            set_path(self.guild.id, SETTINGS[chave][0], valor)
            # mensagem com a imagem some do chat (a copia fica guardada no bot)
            try:
                await msg.delete()
            except discord.HTTPException:
                pass
            n = await refresh_panels(self.guild)
            onde = " em assets (vale pra todos os servidores)" if chave == "banner" else ""
            self.notice = f"✅ Imagem salva{onde} · {n} painel(is) atualizado(s)" if n else f"✅ Imagem salva{onde}"
            self.render()
            try:
                await self.sent.edit(view=self)
            except (discord.HTTPException, AttributeError):
                pass
        return h

    async def _refresh_all(self, interaction, item):
        await self.update(interaction, "🔄 Painéis reeditados", refresh=True,
                          vazio=" · nenhum painel postado ainda (use `,pticket`)")

    # ---- tela inicial, cada parte tem titulo + explicacao + botoes

    def _page_home(self, cfg):
        g = self.guild
        menu = cfg["painel"]["modo"] == "menu"
        dm, lg = cfg["transcricao_dm"], cfg["transcricao_log"]
        form = cfg.get("formulario", True)
        verde, cinza = discord.ButtonStyle.success, discord.ButtonStyle.secondary
        cats = "\n".join(f"{_ico(c)}**{c['label']}**  `{k}`" for k, c in cfg["categorias"].items())

        return [
            discord.ui.TextDisplay(
                "## ⚙️ Central de configuração\n"
                "-# clique nos botões de cada parte pra mudar. tudo salva na hora e os painéis se atualizam sozinhos"
            ),
            sep(),

            # parte 1
            _sec("📋 Painel de abertura", "a mensagem onde o pessoal clica pra abrir ticket"),
            discord.ui.TextDisplay(
                f"**Modo**  {_show(g, 'modo', cfg)}\n**Título**  {_show(g, 'titulo', cfg)}\n"
                f"**Imagem do topo**  {_show(g, 'banner', cfg)}\n**Imagem do final**  {_show(g, 'gif_fim', cfg)}\n**Cor**  {_show(g, 'cor', cfg)}"
            ),
            discord.ui.ActionRow(
                self._btn("Editar textos", self._modal("painel"), emoji="✏️"),
                self._btn("Cor e botões", self._modal("painelvisual"), emoji="🎨"),
                self._btn(f"Modo: {'menu' if menu else 'botões'}", self._toggle(("painel", "modo")),
                          verde if menu else cinza, "🔀"),
            ),
            discord.ui.ActionRow(
                self._btn("Imagem do topo", self._upload("banner"), emoji="📎"),
                self._btn("Imagem do final", self._upload("gif_fim"), emoji="📎"),
            ),
            sep(),

            # parte 2
            _sec("🎟️ Dentro do ticket", "o que aparece no canal quando alguém abre"),
            discord.ui.TextDisplay(
                f"**Boas-vindas**  {_show(g, 'boasvindas', cfg)}\n**Nome do canal**  {_show(g, 'nome_canal', cfg)}\n"
                f"**Tickets por pessoa**  {_show(g, 'limite', cfg)}\n**Segundos até apagar**  {_show(g, 'delay', cfg)}\n"
                f"**Formulário (assunto e detalhes)**  {_show(g, 'formulario', cfg)}"
            ),
            discord.ui.ActionRow(
                self._btn("Editar mensagens", self._modal("ticket"), emoji="✏️"),
                self._btn("Regras e cor", self._modal("ticketregras"), emoji="⚖️"),
                self._btn(f"Formulário: {'sim' if form else 'não'}", self._toggle(("formulario",)),
                          verde if form else cinza, "📝"),
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
                f"**Canal dos tópicos de ticket**  {_show(g, 'canal_tickets', cfg)}"
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
            discord.ui.TextDisplay("## 👥 Equipe e canais\n-# escolha nos menus, salva na hora"),
            sep(),

            _sec("🛡️ Cargo da equipe", f"agora: {_show(g, 'staff', cfg)}  ·  admin e gerenciar mensagens já contam como staff"),
            discord.ui.ActionRow(_RoleSel(self._pick(("staff",)), placeholder="Escolher o cargo", min_values=1, max_values=1)),
            discord.ui.ActionRow(
                self._btn("Digitar ID ou nome", self._ids_modal(("staff",)), discord.ButtonStyle.primary, "🔢"),
                self._btn("Tirar cargo", self._clear(("staff",))),
            ),
            sep(),

            _sec("📄 Canal das transcrições", f"agora: {_show(g, 'log', cfg)}"),
            discord.ui.ActionRow(_ChanSel(
                self._pick(("log",)), placeholder="Escolher o canal",
                channel_types=[discord.ChannelType.text], min_values=1, max_values=1,
            )),
            discord.ui.ActionRow(self._btn("Tirar canal", self._clear(("log",)))),
            sep(),

            _sec("🧵 Canal dos tópicos de ticket", f"agora: {_show(g, 'canal_tickets', cfg)}  ·  cada ticket vira um tópico privado aqui (vazio = canal do painel)"),
            discord.ui.ActionRow(_ChanSel(
                self._pick(("canal_tickets",)), placeholder="Escolher o canal",
                channel_types=[discord.ChannelType.text], min_values=1, max_values=1,
            )),
            discord.ui.ActionRow(self._btn("Tirar canal", self._clear(("canal_tickets",)))),
            sep(),

            discord.ui.ActionRow(self._btn("Voltar pro início", self._goto("home"), emoji="⬅️")),
        ]

    # ---- lista de categorias

    def _page_cats(self, cfg):
        cats = cfg["categorias"]
        linhas = [
            f"{_ico(c)}**{c['label']}**  `{k}`" + (f"\n-# {c['hint']}" if c.get("hint") else "")
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
            discord.ui.TextDisplay(f"## 🗂️ Categorias ({len(cats)}/{MAX_CATEGORIAS})\n" + "\n".join(linhas)),
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
        cargos = _roles(g, c.get("staff"))
        cargo_txt = " ".join(r.mention for r in cargos) or "*(usa o geral)*"
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

        async def ids_cargo(interaction, item):
            def salvar(ids):
                _cat_set(g.id, key, "staff", ids)
            await interaction.response.send_modal(
                RoleIdsModal(self, "Cargos desta categoria", salvar, c.get("staff")))

        async def tirar_cat(interaction, item):
            _cat_set(g.id, key, "categoria", None)
            await self.update(interaction, "✅ Removido")

        def escolher(campo):
            async def h(interaction, item):
                _cat_set(g.id, key, campo, item.values[0].id)
                await self.update(interaction, "✅ Salvo")
            return h

        estilos = [
            discord.SelectOption(label=f"Botão {n}", value=n, emoji=STYLE_ICONS[n], default=n == c["botao"])
            for n in BUTTON_STYLES
        ]
        pos = ordem.index(key)
        return [
            discord.ui.TextDisplay(
                f"## {_ico(c)}{c['label']}  `{key}`\n"
                f"**Dica**  {_short(c['hint']) if c.get('hint') else '*(vazio)*'}\n"
                f"**Cor da barra**  {'`' + _hex(c['cor']) + '`' if c.get('cor') else '*(padrão)*'}\n"
                f"**Boas-vindas própria**  {_short(c['boasvindas']) if c.get('boasvindas') else '*(usa a geral)*'}"
            ),
            sep(),

            _sec("🎨 Aparência", "emoji, nome, dica, cor da barra, boas-vindas e a cor do botão"),
            discord.ui.ActionRow(self._btn("Editar textos e cor da barra", editar, discord.ButtonStyle.primary, "✏️")),
            discord.ui.ActionRow(_Sel(estilo, placeholder=f"Cor do botão (agora: {c['botao']})", options=estilos)),
            sep(),

            _sec("👥 Quem atende", f"cargos: {cargo_txt}  ·  categoria do Discord: {cat_dc.mention if cat_dc else '*(usa a geral)*'}"),
            discord.ui.ActionRow(_RoleSel(escolher("staff"), placeholder="Cargo só desta categoria", min_values=1, max_values=1)),
            discord.ui.ActionRow(_ChanSel(
                escolher("categoria"), placeholder="Categoria do Discord só desta",
                channel_types=[discord.ChannelType.category], min_values=1, max_values=1,
            )),
            discord.ui.ActionRow(
                self._btn("Digitar ID ou nome", ids_cargo, discord.ButtonStyle.primary, "🔢"),
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
                f"## 🗑️ Apagar {_ico(c)}{c['label']}?\n"
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


# ---- descobrir o nome do servidor pelo texto da parceria (pra convite que expirou)

_ROTULO_NOME = re.compile(
    r"^\s*[-*•>#_\s]*(?:nome(?:\s+do)?(?:\s+(?:servidor|server|sv|comunidade))?|servidor|server|sv|comunidade)"
    r"\s*[:：=\-–—]\s*(.+)$", re.IGNORECASE)
_PREFIXO_NOME = re.compile(
    r"^(?:parcerias?|partner(?:ship)?|divulga[cç][aã]o|servidor|server|sv)\s*(?:com|with|de|do|da)?\s*[:\-–—|]*\s*",
    re.IGNORECASE)
_CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
_MENCAO = re.compile(r"<(?:@[!&]?|#)\d+>")
_COD_EMOJI = re.compile(r":[A-Za-z0-9_]{2,}:")
_GENERICOS = {
    "parceria", "parcerias", "partner", "partnership", "servidor", "server", "discord", "convite", "invite",
    "divulgacao", "anuncio", "ad", "ads", "link", "novo", "nova", "oi", "ola", "pv", "dm",
}


def _norm_nome(n: str) -> str:
    # compara nomes ignorando acento, maiuscula, emoji, espaco e enfeite
    n = unicodedata.normalize("NFKD", n or "")
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    return "".join(ch for ch in n.casefold() if ch.isalnum())


def _nomes_parecidos(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if re.sub(r"\D", "", a) != re.sub(r"\D", "", b):
        return False                    # "Hub 1" e "Hub 2" sao servidores diferentes
    return min(len(a), len(b)) >= 8 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.93


def _limpa_nome(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "")
    t = LINK_RE.sub(" ", t)
    t = _CUSTOM_EMOJI.sub(" ", t)
    t = _MENCAO.sub(" ", t)
    t = _COD_EMOJI.sub(" ", t)
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] not in "SC")   # emoji, simbolos, invisiveis
    t = re.sub(r"[*_~|`>#\[\]()]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—:|.,!?\"'")
    for _ in range(3):
        novo = _PREFIXO_NOME.sub("", t).strip(" -–—:|.,!?\"'")
        if novo == t:
            break
        t = novo
    return t


def _nome_do_texto(texto: str):
    """chuta o nome do servidor pelo texto da parceria: campo 'Nome:', negrito/titulo ou primeira linha curta.
    devolve None se nao tiver certeza (melhor nao chutar do que barrar servidor errado)"""
    linhas = [l for l in (texto or "").splitlines() if l.strip() and not l.lstrip().startswith("-#")]
    candidatos = []   # (texto, max de palavras, estrito)
    for l in linhas:
        m = _ROTULO_NOME.match(l)
        if m:
            candidatos.append((m.group(1), 8, False))
            break
    if not candidatos:
        for l in linhas:
            m = re.search(r"\*\*(.+?)\*\*|__(.+?)__", l) or re.match(r"\s*#{1,3}\s+(.+)", l)
            if m:
                candidatos.append((next(g for g in m.groups() if g), 8, False))
                break
    if not candidatos:
        for l in linhas:
            if _norm_nome(_limpa_nome(l)):
                candidatos.append((l, 4, True))     # primeira linha com texto: so vale se for curtinha e sem cara de frase
                break
    for bruto, max_palavras, estrito in candidatos:
        nome = _limpa_nome(bruto)
        nn = _norm_nome(nome)
        if not (len(nn) >= 4 and nn not in _GENERICOS and len(nome) <= 50 and len(nome.split()) <= max_palavras):
            continue
        if estrito and any(_norm_nome(w) in _PALAVRAS_DE_FRASE for w in nome.split()):
            continue
        return nome
    return None


# palavras que entregam frase de conversa, nao nome de servidor (usado so no palpite da primeira linha)
_PALAVRAS_DE_FRASE = {
    "oi", "ola", "opa", "eae", "fala", "bom", "boa", "dia", "tarde", "noite", "tudo", "bem", "gente", "galera",
    "pessoal", "vim", "vem", "venha", "fazer", "quero", "queria", "gostaria", "vcs", "voce", "voces", "tenho",
    "meu", "minha", "nosso", "nossa", "sou", "somos", "aqui", "segue", "link", "convite", "entra", "entrem",
    "parceria", "parcerias", "partner", "ola", "hello", "hi", "hey", "please", "pode", "podemos", "topa",
}


def _barra(tick: int, n: int = 12, bloco: int = 4) -> str:
    pos = tick % (n + bloco)
    return "".join("▰" if pos - bloco < i <= pos else "▱" for i in range(n))


def _novo_st() -> dict:
    return {"cancelar": False, "lidas": 0, "links": 0, "novos": 0, "por_nome": 0, "repetidos": 0,
            "invalidos": 0, "falhas": 0, "inicio": asyncio.get_running_loop().time()}


# varredura em andamento por servidor: {guild_id: contadores (ver _novo_st)}
_VARRENDO: dict = {}


async def _varrer_parcerias(bot, guild, canal, painel=None):
    """le o canal de parcerias inteiro e salva cada servidor no registro.
    convite valido: descobre o servidor pelo Discord. convite expirado: tenta pelo nome escrito no texto.
    nao mexe no que ja esta salvo; vale o registro mais antigo de cada servidor"""
    st = _VARRENDO[guild.id]
    reg = _parc_guild(guild.id)
    conhecidos = {str(e.get("codigo", "")).lower() for e in reg.values()}
    vistos = set()
    erro, ultimo = None, 0.0
    try:
        async for m in canal.history(limit=None, oldest_first=True):
            if st["cancelar"]:
                break
            st["lidas"] += 1
            texto = m.content or ""
            for e in m.embeds:
                texto += f" {e.url or ''} {e.description or ''}"
            codigos = []
            for c in INVITE_RE.findall(texto):
                if c.lower() not in (x.lower() for x in codigos):
                    codigos.append(c)
            for code in codigos:
                lc = code.lower()
                if lc in vistos:
                    continue
                vistos.add(lc)
                st["links"] += 1
                if lc in conhecidos:
                    continue
                alvo, morto = None, False
                for tentativa in (1, 2):
                    try:
                        convite = await bot.fetch_invite(code, with_counts=False)
                        alvo = getattr(convite, "guild", None)
                        morto = alvo is None
                        break
                    except discord.NotFound:
                        morto = True              # expirado ou apagado
                        break
                    except discord.HTTPException:
                        if tentativa == 2:
                            st["falhas"] += 1
                        else:
                            await asyncio.sleep(3)
                await asyncio.sleep(0.25)
                credito = re.search(r"enviado por <@!?(\d+)>", m.content or "")
                autor = int(credito.group(1)) if credito else (0 if m.author.bot else m.author.id)
                base = {
                    "codigo": code, "autor": autor, "ts": int(m.created_at.timestamp()), "canal": canal.id,
                    "msg": m.id, "importado": True, "salvo_em": int(datetime.now().timestamp()),
                }
                if alvo is not None:
                    if alvo.id == guild.id:
                        continue
                    gid = str(alvo.id)
                    if gid in reg:
                        st["repetidos"] += 1      # mesmo servidor com outro link
                        continue
                    reg[gid] = {**base, "nome": getattr(alvo, "name", None) or gid}
                    st["novos"] += 1
                elif morto:
                    # convite morto: tenta descobrir o servidor pelo nome que a pessoa escreveu
                    nome = _nome_do_texto(m.content or "") if len(codigos) == 1 else None
                    if not nome:
                        st["invalidos"] += 1
                        continue
                    nn = _norm_nome(nome)
                    if any(_nomes_parecidos(nn, _norm_nome(e.get("nome", ""))) for e in reg.values()):
                        st["repetidos"] += 1      # esse servidor ja esta no registro
                        continue
                    reg[f"nome:{nn}"] = {**base, "nome": nome, "por_nome": True}
                    st["por_nome"] += 1
                else:
                    continue                       # discord nao respondeu (ja contado em falhas)
                if (st["novos"] + st["por_nome"]) % 25 == 0:
                    _parc_save()
            agora = asyncio.get_running_loop().time()
            if painel is not None and agora - ultimo > 8:
                ultimo = agora
                await painel.refresh_sent()
    except discord.Forbidden:
        erro = "O bot não consegue ler o histórico do canal (falta **Ver histórico de mensagens**)."
    except Exception:
        log.exception("erro na varredura de parcerias")
        erro = "Deu erro no meio da varredura. O que já foi lido ficou salvo."
    finally:
        cancelado = st["cancelar"]
        _parc_save()
        _VARRENDO.pop(guild.id, None)

    linhas = [
        f"**{st['lidas']}** mensagens lidas · **{st['links']}** convites diferentes",
        f"**{st['novos']}** servidores salvos pelo convite",
        f"**{st['por_nome']}** servidores salvos pelo nome escrito no texto (convite expirado)",
        f"**{st['repetidos']}** já estavam no registro",
        f"**{st['invalidos']}** convites expirados sem nome claro no texto (não dá pra identificar)",
    ]
    if st["falhas"]:
        linhas.append(f"**{st['falhas']}** convites que o Discord não respondeu")
    if erro:
        linhas.append(f"\n⚠️ {erro}")
    titulo = "⏹️ Varredura parada" if cancelado else ("⚠️ Varredura incompleta" if erro else "✅ Varredura concluída")
    onde = painel.sent.channel if painel is not None and painel.sent is not None else canal
    try:
        await onde.send(view=Card(titulo, "\n".join(linhas)), allowed_mentions=NO_MENTIONS)
    except discord.HTTPException:
        pass
    if painel is not None:
        await painel.refresh_sent()


class ParceriaLiberarModal(discord.ui.Modal):
    def __init__(self, painel):
        super().__init__(title="Liberar servidor")
        self.painel = painel
        self.campo = discord.ui.TextInput(
            label="ID do servidor, convite ou nome",
            placeholder="123456789012345678 · discord.gg/abc · Nome",
            max_length=100,
        )
        self.add_item(self.campo)

    async def on_submit(self, interaction: discord.Interaction):
        txt = self.campo.value.strip()
        m = INVITE_RE.search(txt)
        cod = (m.group(1) if m else txt).strip("/ ").lower()
        nn = _norm_nome(txt)
        reg = _parc_guild(self.painel.guild.id)
        achado = None
        if cod:
            achado = next((k for k, e in reg.items()
                           if k == cod or str(e.get("codigo", "")).lower() == cod
                           or (nn and _norm_nome(e.get("nome") or "") == nn)), None)
        if achado is None:
            await self.painel.update(interaction, "⚠️ Não achei esse servidor no registro")
            return
        nome = reg.pop(achado).get("nome", achado)
        _parc_save()
        await self.painel.update(interaction, f"✅ {nome} liberado pra postar de novo")

    async def on_error(self, interaction, error):
        log.exception("erro na janela de liberar parceria", exc_info=error)
        await _erro_card(interaction, "Algo deu errado", "Tente de novo.")


def _renov_txt(p) -> str:
    dias = int(p.get("renovar_dias", 5) or 0)
    if not p.get("bloquear_duplicado", True):
        return "sem bloqueio"
    return "só a equipe libera" if dias <= 0 else f"após {dias} dia{'s' if dias != 1 else ''}"


def _cargos_txt(ids) -> str:
    return " ".join(f"<@&{i}>" for i in ids) if ids else "*nenhum*"


# painel minimalista da postagem automatica de parcerias (mesma cara do ,ptconfig)
class ParceriaView(discord.ui.LayoutView):
    def __init__(self, author_id: int, guild: discord.Guild):
        super().__init__(timeout=1800)
        self.author_id, self.guild = author_id, guild
        self.page, self.notice, self.sent = "home", "", None
        self.tick, self._live = 0, None
        self.render()

    @classmethod
    async def open(cls, ctx):
        view = cls(ctx.author.id, ctx.guild)
        view.sent = await ctx.send(view=view, allowed_mentions=NO_MENTIONS)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                view=Card("Esse painel é de outra pessoa", "Use `,ptparc` pra abrir o seu."), ephemeral=True)
            return False
        perms = getattr(interaction.user, "guild_permissions", None)
        if not (perms is not None and perms.administrator) and not tem_acesso_total(interaction.user):
            await _erro_card(interaction, "Sem permissão", "Só administradores mexem aqui.")
            return False
        return True

    async def on_timeout(self):
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
        log.exception("erro no painel de parcerias", exc_info=error)
        await _erro_card(interaction, "Algo deu errado", "Tente de novo.")

    async def update(self, interaction, notice=""):
        self.notice = notice
        self.render()
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)

    async def go(self, interaction, page):
        self.page = page
        await self.update(interaction)
        if page == "ao_vivo":
            self._start_live()

    def _start_live(self):
        if self._live is None or self._live.done():
            self._live = asyncio.create_task(self._loop_live())

    async def _loop_live(self):
        # a tela "ao vivo" se redesenha sozinha enquanto estiver aberta
        ultimo = None
        try:
            while not self.is_finished() and self.page == "ao_vivo" and self.sent is not None:
                rodando = self.guild.id in _VARRENDO
                await asyncio.sleep(3 if rodando else 6)
                if self.is_finished() or self.page != "ao_vivo":
                    break
                reg = _parc_guild(self.guild.id)
                sig = (len(reg), max((e.get("salvo_em", 0) for e in reg.values()), default=0), rodando)
                if not rodando and sig == ultimo:
                    continue                       # nada mudou: nao gasta edicao
                ultimo = sig
                self.tick += 1
                self.render()
                await self.sent.edit(view=self)
        except discord.HTTPException:
            pass
        except Exception:
            log.exception("erro na tela ao vivo")

    async def refresh_sent(self):
        # redesenha a tela sozinha (usado pela varredura em segundo plano)
        if self.sent is None or self.is_finished() or self.page != "registro":
            return
        self.render()
        try:
            await self.sent.edit(view=self)
        except discord.HTTPException:
            pass

    def render(self):
        cfg = get_cfg(self.guild.id)
        children = getattr(self, f"_page_{self.page}")(cfg)
        if self.notice:
            children.append(discord.ui.TextDisplay(f"-# {self.notice}"))
        self.clear_items()
        self.add_item(discord.ui.Container(*children))

    # ---- pecinhas

    def _btn(self, label, fn, style=discord.ButtonStyle.secondary, emoji=None, disabled=False):
        return _Btn(fn, label=label[:80], style=style, emoji=emoji, disabled=disabled)

    def _goto(self, page):
        async def h(interaction, item):
            await self.go(interaction, page)
        return h

    def _voltar(self):
        return discord.ui.ActionRow(self._btn("Voltar", self._goto("home"), emoji="⬅️"))

    def _flip(self, path):
        async def h(interaction, item):
            atual = _dig(get_cfg(self.guild.id), path)
            set_path(self.guild.id, path, not atual)
            await self.update(interaction, "✅ Salvo")
        return h

    async def _h_varrer(self, interaction, item):
        g = self.guild
        p = get_cfg(g.id)["parceria"]
        canal = g.get_channel(p.get("canal") or 0)
        if not isinstance(canal, discord.TextChannel):
            await self.update(interaction, "⚠️ Escolha o canal das parcerias antes de varrer")
            return
        if g.id in _VARRENDO:
            await self.update(interaction, "⏳ Já tem uma varredura rodando")
            return
        st = _novo_st()
        _VARRENDO[g.id] = st
        st["task"] = asyncio.create_task(_varrer_parcerias(interaction.client, g, canal, self))
        await self.update(interaction, "🔎 Varredura iniciada · aviso aqui quando terminar")

    async def _h_parar(self, interaction, item):
        st = _VARRENDO.get(self.guild.id)
        if st:
            st["cancelar"] = True
        await self.update(interaction, "⏹️ Parando · o que já foi lido fica salvo")

    def _ids_modal(self, titulo, path, unico=False):
        # botao que abre a janela pra digitar id, @ ou nome do(s) cargo(s)
        async def h(interaction, item):
            atual = _dig(get_cfg(self.guild.id), path)

            def salvar(ids):
                set_path(self.guild.id, path, ids[0] if unico else ids)
            await interaction.response.send_modal(
                RoleIdsModal(self, titulo, salvar, atual, maximo=1 if unico else None))
        return h

    def _set(self, path, valor, aviso="✅ Salvo"):
        async def h(interaction, item):
            set_path(self.guild.id, path, valor)
            await self.update(interaction, aviso)
        return h

    # ---- telas

    def _page_home(self, cfg):
        p = cfg["parceria"]
        canal = f"<#{p['canal']}>" if p.get("canal") else "*não definido*"
        ping = f"<@&{p['ping_cargo']}>" if p.get("ping_cargo") else "*sem ping*"
        c = cat_of(cfg, p["tipo"])
        n = len(_parc_guild(self.guild.id))
        ativo = p["ativo"]

        async def ligar(interaction, item):
            atual = get_cfg(self.guild.id)["parceria"]
            if not atual["ativo"] and not self.guild.get_channel(atual.get("canal") or 0):
                await self.update(interaction, "⚠️ Escolha o canal das parcerias antes de ligar")
                return
            set_path(self.guild.id, ("parceria", "ativo"), not atual["ativo"])
            await self.update(interaction, "✅ Desligada" if atual["ativo"] else "✅ Ligada")

        return [
            discord.ui.TextDisplay("## 🤝 Parcerias\n-# postagem automática e regras de link dos tickets"),
            sep(),
            discord.ui.TextDisplay(
                f"**Status**  {'🟢 ligada' if ativo else '⚪ desligada'}\n"
                f"**Canal**  {canal}\n"
                f"**Ping**  {ping}\n"
                f"**Ticket**  {_ico(c)}{c['label']}\n"
                f"**Confirmação**  {'🟢 pede ok antes de postar' if p.get('confirmar') else '⚪ posta direto'}\n"
                f"**Renovação**  {_renov_txt(p)}\n"
                f"**Registradas**  {n}"
            ),
            sep(),
            discord.ui.ActionRow(
                self._btn("Desligar" if ativo else "Ligar", ligar,
                          discord.ButtonStyle.secondary if ativo else discord.ButtonStyle.success),
                self._btn("Destino", self._goto("destino"), emoji="📍"),
                self._btn("Renovação", self._goto("renovacao"), emoji=_poyo_ui("renovar", "🔁")),
                self._btn("Confirmação: sim" if p.get("confirmar") else "Confirmação: não",
                          self._flip(("parceria", "confirmar")),
                          discord.ButtonStyle.success if p.get("confirmar") else discord.ButtonStyle.secondary,
                          emoji="🧾"),
            ),
            discord.ui.ActionRow(
                self._btn("Links", self._goto("links"), emoji="🔗"),
                self._btn("Registro", self._goto("registro"), emoji="🗂️"),
                self._btn("Ao vivo", self._goto("ao_vivo"), emoji="📡"),
            ),
        ]

    def _page_destino(self, cfg):
        p = cfg["parceria"]
        g = self.guild

        async def canal(interaction, item):
            set_path(g.id, ("parceria", "canal"), item.values[0].id)
            await self.update(interaction, "✅ Salvo")

        async def cargo(interaction, item):
            set_path(g.id, ("parceria", "ping_cargo"), item.values[0].id)
            await self.update(interaction, "✅ Salvo")

        async def tipo(interaction, item):
            set_path(g.id, ("parceria", "tipo"), item.values[0])
            await self.update(interaction, "✅ Salvo")

        canal_txt = f"<#{p['canal']}>" if p.get("canal") else "*não definido*"
        ping_txt = f"<@&{p['ping_cargo']}>" if p.get("ping_cargo") else "*sem ping*"
        opcoes = [
            discord.SelectOption(label=c["label"][:100], value=k, emoji=_ui_emoji(c["emoji"]),
                                 default=(k == p["tipo"]))
            for k, c in cfg["categorias"].items()
        ]
        return [
            discord.ui.TextDisplay("## 📍 Destino\n-# onde postar, quem pingar e de qual ticket"),
            sep(),
            _sec("📢 Canal das parcerias", "agora: " + canal_txt),
            discord.ui.ActionRow(_ChanSel(
                canal, placeholder="Escolher o canal",
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=1, max_values=1)),
            discord.ui.ActionRow(self._btn("Tirar canal", self._set(("parceria", "canal"), None, "✅ Removido"))),
            sep(),
            _sec("🔔 Cargo do ping", "agora: " + ping_txt),
            discord.ui.ActionRow(_RoleSel(cargo, placeholder="Escolher o cargo", min_values=1, max_values=1)),
            discord.ui.ActionRow(
                self._btn("Digitar ID ou @", self._ids_modal("Cargo do ping", ("parceria", "ping_cargo"), unico=True),
                          discord.ButtonStyle.primary, "🔢"),
                self._btn("Tirar ping", self._set(("parceria", "ping_cargo"), None, "✅ Removido")),
            ),
            sep(),
            _sec("🎟️ Ticket que posta sozinho", "só o dono do ticket dessa categoria, e só convites"),
            discord.ui.ActionRow(_Sel(tipo, placeholder="Escolher a categoria", options=opcoes)),
            sep(),
            self._voltar(),
        ]

    def _page_renovacao(self, cfg):
        p = cfg["parceria"]
        g = self.guild
        dias = int(p.get("renovar_dias", 5) or 0)

        async def dias_sel(interaction, item):
            set_path(g.id, ("parceria", "renovar_dias"), int(item.values[0]))
            await self.update(interaction, "✅ Salvo")

        valores = sorted({0, 1, 2, 3, 5, 7, 10, 14, 30, dias})
        opcoes = [
            discord.SelectOption(
                label="Nunca (só a equipe libera)" if d == 0 else f"{d} dia{'s' if d != 1 else ''}",
                value=str(d), default=(d == dias),
                emoji=_poyo_ui("renovar", "🔁"),
            )
            for d in valores
        ]
        return [
            discord.ui.TextDisplay(
                f"## {_poyo('renovar', '🔁')} Renovação\n-# quanto tempo um servidor espera pra postar parceria de novo"),
            sep(),
            _sec("⏳ Prazo", f"agora: {_renov_txt(p)}  ·  conta do dia do post e vale pros servidores importados também"),
            discord.ui.ActionRow(_Sel(dias_sel, placeholder="Escolher o prazo", options=opcoes)),
            sep(),
            _sec("🔁 Servidor repetido", "barra o servidor até o prazo passar · conferir nome pega os de convite antigo que expirou"),
            discord.ui.ActionRow(
                self._btn("Bloquear: sim" if p.get("bloquear_duplicado", True) else "Bloquear: não",
                          self._flip(("parceria", "bloquear_duplicado"))),
                self._btn("Conferir nome: sim" if p.get("conferir_nome", True) else "Conferir nome: não",
                          self._flip(("parceria", "conferir_nome"))),
            ),
            sep(),
            self._voltar(),
        ]

    def _page_links(self, cfg):
        g = self.guild
        liberados = cfg.get("links_tipos") or []

        async def tipos(interaction, item):
            set_path(g.id, ("links_tipos",), list(item.values))
            await self.update(interaction, "✅ Salvo")

        opcoes = [
            discord.SelectOption(label=c["label"][:100], value=k, emoji=_ui_emoji(c["emoji"]),
                                 default=(k in liberados))
            for k, c in cfg["categorias"].items()
        ]
        nomes = ", ".join(cat_of(cfg, k)["label"] for k in liberados) or "nenhuma"
        bloq = cfg.get("bloquear_links", True)
        return [
            discord.ui.TextDisplay("## 🔗 Links nos tickets\n-# a equipe sempre pode mandar link"),
            sep(),
            _sec("🚧 Bloquear links", "quem não é da equipe só manda link nas categorias liberadas"),
            discord.ui.ActionRow(self._btn(
                "Bloqueio: ligado" if bloq else "Bloqueio: desligado",
                self._flip(("bloquear_links",)),
                discord.ButtonStyle.success if bloq else discord.ButtonStyle.secondary)),
            sep(),
            _sec("✅ Categorias que aceitam link", f"agora: {nomes}  ·  se outro bot cortar o link, o bot reposta"),
            discord.ui.ActionRow(_Sel(tipos, placeholder="Escolher categorias", options=opcoes,
                                      min_values=0, max_values=len(opcoes))),
            sep(),
            self._voltar(),
        ]

    def _page_registro(self, cfg):
        g = self.guild
        reg = _parc_guild(g.id)
        rodando = _VARRENDO.get(g.id)
        itens = sorted(reg.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)[:8]
        linhas = "\n".join(
            ("🔤 " if e.get("por_nome") else "")
            + f"**{e.get('nome', k)}**"
            + ("  ·  pelo nome" if e.get("por_nome") else f"  `{k}`")
            + f"  ·  <t:{e.get('ts', 0)}:d>"
            + (f"  ·  <@{e['autor']}>" if e.get("autor") else "")
            for k, e in itens
        ) or "*nenhuma parceria registrada*"

        async def liberar(interaction, item):
            await interaction.response.send_modal(ParceriaLiberarModal(self))

        status = ""
        if rodando:
            status = (f"🔎 varrendo o canal… **{rodando['lidas']}** mensagens lidas · "
                      f"**{rodando['novos'] + rodando['por_nome']}** servidores novos\n")
        return [
            discord.ui.TextDisplay(
                f"## 🗂️ Registro ({len(reg)})\n-# servidores já postados · apagar o post também libera o servidor"),
            sep(),
            discord.ui.TextDisplay(status + linhas + ("\n-# mostrando os 8 mais recentes" if len(reg) > 8 else "")),
            sep(),
            _sec("🔎 Varrer o canal de parcerias",
                 "lê o canal inteiro e salva os servidores que ainda não estão no registro · leva um tempo se o canal for grande"),
            discord.ui.ActionRow(
                self._btn("Parar varredura", self._h_parar, discord.ButtonStyle.danger, "⏹️") if rodando
                else self._btn("Varrer canal", self._h_varrer, discord.ButtonStyle.primary, "🔎"),
                self._btn("Ao vivo", self._goto("ao_vivo"), emoji="📡"),
            ),
            sep(),
            discord.ui.ActionRow(
                self._btn("Liberar servidor", liberar, emoji="🔓"),
            ),
            self._voltar(),
        ]

    def _page_ao_vivo(self, cfg):
        g = self.guild
        reg = _parc_guild(g.id)
        st = _VARRENDO.get(g.id)
        spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[self.tick % 10]
        if st:
            dec = int(asyncio.get_running_loop().time() - st.get("inicio", 0))
            topo = (
                f"{spin} **varrendo o canal**\n"
                f"`{_barra(self.tick)}`\n"
                f"**{st['lidas']}** mensagens · **{st['links']}** convites · **{st['novos']}** salvos · "
                f"**{st['por_nome']}** pelo nome · **{st['invalidos']}** sem identificar\n"
                f"-# rodando há {dec // 60}:{dec % 60:02d}"
            )
        else:
            topo = "⚪ **sem varredura**\n-# novas parcerias aparecem aqui assim que são salvas"
        n_nome = sum(1 for e in reg.values() if e.get("por_nome"))
        total = (f"**{len(reg)}** servidores no registro  ·  **{len(reg) - n_nome}** pelo convite  ·  "
                 f"**{n_nome}** pelo nome")
        agora = int(datetime.now().timestamp())
        recentes = sorted(reg.values(), key=lambda e: e.get("salvo_em", e.get("ts", 0)), reverse=True)[:8]
        feed = "\n".join(
            ("✨" if agora - e.get("salvo_em", 0) < 15 else "▫️")
            + f" **{e.get('nome', '?')}**" + ("  🔤" if e.get("por_nome") else "")
            + f"  ·  <t:{e.get('salvo_em', e.get('ts', 0))}:R>"
            for e in recentes
        ) or "*nada salvo ainda*"
        return [
            discord.ui.TextDisplay("## 📡 Ao vivo\n-# atualiza sozinho enquanto esta tela estiver aberta"),
            sep(),
            discord.ui.TextDisplay(topo),
            sep(),
            discord.ui.TextDisplay(total),
            sep(),
            _sec("🆕 Últimos salvos", "✨ = salvo agora há pouco  ·  🔤 = identificado pelo nome"),
            discord.ui.TextDisplay(feed),
            sep(),
            discord.ui.ActionRow(
                self._btn("Parar varredura", self._h_parar, discord.ButtonStyle.danger, "⏹️") if st
                else self._btn("Varrer canal", self._h_varrer, discord.ButtonStyle.primary, "🔎"),
                self._btn("Registro", self._goto("registro"), emoji="🗂️"),
            ),
            self._voltar(),
        ]


# confirmacao da postagem de parceria: preview + "pode prosseguir?" (so o dono do ticket ou a equipe)
class ConfirmarParceriaView(discord.ui.LayoutView):
    def __init__(self, dono_id, tipo, preview, destino, n_anexos, publicar):
        super().__init__(timeout=600)
        self.dono_id, self.tipo, self.publicar = dono_id, tipo, publicar
        self.sent, self.done, self.fim = None, False, None
        extra = f"\n-# {n_anexos} anexo(s) vão junto" if n_anexos else ""
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"### Pode prosseguir com a postagem?\n-# vai ser publicada em {destino.mention} assim:"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(_quote(preview, 3000) + extra),
            discord.ui.ActionRow(
                _Btn(self._sim, label="Pode prosseguir", style=discord.ButtonStyle.success, emoji="✅"),
                _Btn(self._nao, label="Cancelar", style=discord.ButtonStyle.secondary),
            ),
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        u = interaction.user
        ok = u.id == self.dono_id or tem_acesso_total(u)
        if not ok and isinstance(u, discord.Member):
            ok = is_staff(u, get_cfg(u.guild.id), self.tipo)
        if not ok:
            await _erro_card(interaction, "Só quem abriu o ticket decide",
                             "Esse botão é do dono do ticket ou da equipe.")
        return ok

    def _encerrar(self):
        self.done = True
        self.stop()
        if self.fim:
            self.fim()

    async def cancelar(self, titulo, texto):
        if self.done:
            return
        self._encerrar()
        if self.sent is not None:
            try:
                await self.sent.edit(view=Card(titulo, texto))
            except discord.HTTPException:
                pass

    async def _sim(self, interaction, item):
        if self.done:
            await interaction.response.defer()
            return
        self._encerrar()
        await interaction.response.edit_message(view=Card("Publicando", "Um instante."))
        await self.publicar()
        try:
            await interaction.message.delete()   # o resultado ja foi avisado no canal
        except discord.HTTPException:
            pass

    async def _nao(self, interaction, item):
        if self.done:
            await interaction.response.defer()
            return
        self._encerrar()
        await interaction.response.edit_message(
            view=Card("Cancelado", "Nada foi postado. Mande o convite de novo quando quiser."))

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        if self.fim:
            self.fim()
        if self.sent is not None:
            try:
                await self.sent.edit(view=Card("Confirmação expirada",
                                               "Mande o convite de novo se ainda quiser postar."))
            except discord.HTTPException:
                pass


# ---------------------------------------------------------------- ranking da equipe

RANK_FILE = Path(os.getenv("TICKETS_RANKING_FILE", "tickets_ranking.json"))
_rank = _read_json(RANK_FILE)
if not isinstance(_rank.get("guilds"), dict):
    _rank["guilds"] = {}
_rank_locks: dict = {}
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
         "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _rank_save():
    _write_json(RANK_FILE, _rank)


def _rank_guild(guild_id) -> dict:
    g = _rank["guilds"].get(str(guild_id))
    if not isinstance(g, dict):
        g = _rank["guilds"][str(guild_id)] = {}
    if not isinstance(g.get("registros"), list):
        g["registros"] = []
    return g


# emojis poyo_* do ranking: se existirem num servidor do bot eles vencem, senao cai no unicode
#   poyo_trofeu · poyo_rank_1 · poyo_rank_2 · poyo_rank_3 · poyo_estrela · poyo_renovar
def _poyo(nome: str, fallback: str) -> str:
    return AUTO_EMOJIS.get(f"poyo_{nome}") or fallback


def _poyo_ui(nome: str, fallback: str):
    # versao pra botao e menu (so aceita emoji de verdade)
    custom = AUTO_EMOJIS.get(f"poyo_{nome}")
    return (_ui_emoji(custom) if custom else None) or fallback


def _rank_registrar(guild_id, data, cfg):
    """grava o atendimento no ranking e devolve o id do registro (None se nao conta)"""
    if not cfg["ranking"].get("ativo"):
        return None
    if data.get("rank_id"):
        return data["rank_id"]          # fechamento repetido: nao conta duas vezes
    staff, dono = data.get("claimed"), data.get("owner")
    if not staff or staff == dono:      # sem atendente, ou atendeu o proprio ticket: nao pontua
        return None
    g = _rank_guild(guild_id)
    rid = secrets.token_hex(5)
    agora = int(datetime.now().timestamp())
    g["registros"].append({
        "id": rid, "staff": staff, "owner": dono,
        "tipo": data.get("tipo", "outros"), "ts": agora, "nota": None,
    })
    corte = agora - 400 * 86400         # guarda mais de um ano, o resto sai
    g["registros"] = [x for x in g["registros"] if x.get("ts", 0) >= corte]
    data["rank_id"] = rid
    _save()
    _rank_save()
    return rid


def _rank_achar(rid):
    for gid, g in _rank["guilds"].items():
        if isinstance(g, dict):
            for x in g.get("registros", []):
                if x.get("id") == rid:
                    return int(gid), x
    return None


def _rank_periodo(meses_atras: int = 0):
    # (inicio, fim, "Outubro/2026") do mes pedido, no fuso do bot
    agora = datetime.now(TZ)
    m = agora.month - 1 - meses_atras
    ano, mes = agora.year + m // 12, m % 12 + 1
    ini = datetime(ano, mes, 1, tzinfo=TZ)
    fim = datetime(ano + (1 if mes == 12 else 0), 1 if mes == 12 else mes + 1, 1, tzinfo=TZ)
    return int(ini.timestamp()), int(fim.timestamp()), f"{MESES[mes - 1].capitalize()}/{ano}"


def _rank_tabela(guild_id, cfg, ini, fim) -> list:
    r = cfg["ranking"]
    pt, pe = int(r.get("pontos_ticket", 10)), int(r.get("pontos_estrela", 2))
    tab = {}
    for x in _rank_guild(guild_id)["registros"]:
        if not ini <= x.get("ts", 0) < fim:
            continue
        t = tab.setdefault(x["staff"], {"pts": 0, "tickets": 0, "soma": 0, "notas": 0})
        t["tickets"] += 1
        t["pts"] += pt
        n = x.get("nota")
        if isinstance(n, int) and 1 <= n <= 5:
            t["pts"] += n * pe
            t["soma"] += n
            t["notas"] += 1
    return sorted(tab.items(), key=lambda kv: (-kv[1]["pts"], -kv[1]["tickets"], kv[0]))


def build_ranking_view(guild, cfg, meses_atras: int = 0):
    ini, fim, mes = _rank_periodo(meses_atras)
    tabela = _rank_tabela(guild.id, cfg, ini, fim)
    medalhas = [_poyo("rank_1", "🥇"), _poyo("rank_2", "🥈"), _poyo("rank_3", "🥉")]
    estrela = _poyo("estrela", "⭐")
    linhas = []
    for i, (uid, t) in enumerate(tabela[:10]):
        pos = medalhas[i] if i < 3 else f"`{i + 1}º`"
        media = f"  ·  {estrela} {t['soma'] / t['notas']:.1f}" if t["notas"] else ""
        linhas.append(
            f"{pos} <@{uid}>  **{t['pts']} pts**  ·  {t['tickets']} ticket{'s' if t['tickets'] != 1 else ''}{media}")
    corpo = "\n".join(linhas) or "*ninguém atendeu ticket neste mês ainda*"
    r = cfg["ranking"]
    regra = f"cada ticket atendido vale {r.get('pontos_ticket', 10)} pts"
    if r.get("avaliacao") and r.get("pontos_estrela"):
        regra += f" · cada estrela recebida soma +{r['pontos_estrela']}"
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"## {_poyo('trofeu', '🏆')} Ranking da equipe\n-# {mes}"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(corpo),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"-# {regra}\n-# atualizado <t:{int(datetime.now().timestamp())}:R>"),
    ))
    return view


async def atualizar_ranking(guild) -> bool:
    """reedita (ou posta) o placar do mes no canal configurado"""
    cfg = get_cfg(guild.id)
    r = cfg["ranking"]
    if not r.get("ativo") or not r.get("canal"):
        return False
    canal = guild.get_channel(r["canal"])
    if not isinstance(canal, discord.TextChannel):
        return False
    lock = _rank_locks.setdefault(guild.id, asyncio.Lock())
    async with lock:
        g = _rank_guild(guild.id)
        view = build_ranking_view(guild, cfg)
        ref = g.get("painel")
        if isinstance(ref, list) and len(ref) == 2 and ref[0] == canal.id:
            try:
                await canal.get_partial_message(ref[1]).edit(view=view, allowed_mentions=NO_MENTIONS)
                return True
            except discord.NotFound:
                pass                    # placar apagado: posta outro
            except discord.HTTPException as e:
                log.warning("nao consegui atualizar o placar do ranking: %s", e)
                return False
        try:
            msg = await canal.send(view=view, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException as e:
            log.warning("nao consegui postar o placar do ranking em #%s: %s", canal.name, e)
            return False
        g["painel"] = [canal.id, msg.id]
        _rank_save()
        return True


# nota de 1 a 5 estrelas: o id do botao leva o registro e a nota, entao vale depois de reiniciar
class RateButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"tk:rate:(?P<rid>[a-z0-9]+):(?P<n>[1-5])",
):
    def __init__(self, rid: str, n: int):
        super().__init__(
            discord.ui.Button(
                label=str(n), style=discord.ButtonStyle.secondary,
                emoji=_poyo_ui("estrela", "⭐"), custom_id=f"tk:rate:{rid}:{n}",
            )
        )
        self.rid, self.n = rid, n

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["rid"], int(match["n"]))

    async def callback(self, interaction: discord.Interaction):
        achado = _rank_achar(self.rid)
        if achado is None:
            await interaction.response.edit_message(
                view=Card("Avaliação indisponível", "Esse atendimento não está mais no ranking."))
            return
        gid, rec = achado
        if interaction.user.id != rec.get("owner"):
            await _erro_card(interaction, "Essa avaliação não é sua", "Só quem abriu o ticket pode avaliar.")
            return
        if rec.get("nota"):
            await interaction.response.edit_message(view=Card("Você já avaliou", "Obrigado!"))
            return
        rec["nota"] = self.n
        _rank_save()
        estrelas = _poyo("estrela", "⭐") * self.n
        await interaction.response.edit_message(
            view=Card("Obrigado pela avaliação", f"{estrelas}\nSua nota ajuda a equipe a melhorar."))
        guild = interaction.client.get_guild(gid)
        if guild is not None:
            await atualizar_ranking(guild)


def build_rate_view(rid, staff_id, guild_name):
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(
            f"### {_poyo('estrela', '⭐')} Como foi o seu atendimento?\n"
            f"Em **{guild_name}**, quem cuidou de você foi <@{staff_id}>.\n"
            "-# toque numa nota de 1 a 5"),
        discord.ui.ActionRow(*(RateButton(rid, n) for n in range(1, 6))),
    ))
    return view


class RankPontosModal(discord.ui.Modal):
    def __init__(self, painel):
        super().__init__(title="Pontuação do ranking")
        self.painel = painel
        r = get_cfg(painel.guild.id)["ranking"]
        self.pt = discord.ui.TextInput(
            label="Pontos por ticket atendido (0 a 1000)", default=str(r["pontos_ticket"]), max_length=4)
        self.pe = discord.ui.TextInput(
            label="Pontos extras por estrela (0 a 100)", default=str(r["pontos_estrela"]), max_length=3)
        self.add_item(self.pt)
        self.add_item(self.pe)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            pt, pe = int(self.pt.value.strip()), int(self.pe.value.strip())
        except ValueError:
            await _erro_card(interaction, "Nada foi salvo", "Mande só números inteiros.")
            return
        if not (0 <= pt <= 1000 and 0 <= pe <= 100):
            await _erro_card(interaction, "Nada foi salvo", "Use 0 a 1000 nos tickets e 0 a 100 nas estrelas.")
            return
        set_path(self.painel.guild.id, ("ranking", "pontos_ticket"), pt)
        set_path(self.painel.guild.id, ("ranking", "pontos_estrela"), pe)
        await self.painel.update(interaction, "✅ Pontuação salva", refresh=True)

    async def on_error(self, interaction, error):
        log.exception("erro na janela de pontuacao", exc_info=error)
        await _erro_card(interaction, "Algo deu errado", "Tente de novo.")


# painel do ranking (mesma cara do ,ptconfig e do ,ptparc)
class RankView(discord.ui.LayoutView):
    def __init__(self, author_id: int, guild: discord.Guild):
        super().__init__(timeout=900)
        self.author_id, self.guild = author_id, guild
        self.notice, self.sent, self.meses_atras = "", None, 0
        self.render()

    @classmethod
    async def open(cls, ctx):
        view = cls(ctx.author.id, ctx.guild)
        view.sent = await ctx.send(view=view, allowed_mentions=NO_MENTIONS)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                view=Card("Esse painel é de outra pessoa", "Use `,ptrank` pra abrir o seu."), ephemeral=True)
            return False
        perms = getattr(interaction.user, "guild_permissions", None)
        if not (perms is not None and perms.administrator) and not tem_acesso_total(interaction.user):
            await _erro_card(interaction, "Sem permissão", "Só administradores mexem aqui.")
            return False
        return True

    async def on_timeout(self):
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
        log.exception("erro no painel do ranking", exc_info=error)
        await _erro_card(interaction, "Algo deu errado", "Tente de novo.")

    async def update(self, interaction, notice="", refresh=False):
        if refresh:
            if not interaction.response.is_done():
                await interaction.response.defer()
            ok = await atualizar_ranking(self.guild)
            notice = notice + (" · placar atualizado" if ok else "")
        self.notice = notice
        self.render()
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)

    def render(self):
        cfg = get_cfg(self.guild.id)
        children = self._tela(cfg)
        if self.notice:
            children.append(discord.ui.TextDisplay(f"-# {self.notice}"))
        self.clear_items()
        self.add_item(discord.ui.Container(*children))

    def _btn(self, label, fn, style=discord.ButtonStyle.secondary, emoji=None, disabled=False):
        return _Btn(fn, label=label[:80], style=style, emoji=emoji, disabled=disabled)

    def _flip(self, path, refresh=False):
        async def h(interaction, item):
            atual = _dig(get_cfg(self.guild.id), path)
            set_path(self.guild.id, path, not atual)
            await self.update(interaction, "✅ Salvo", refresh=refresh)
        return h

    def _tela(self, cfg):
        g = self.guild
        r = cfg["ranking"]
        ativo = r["ativo"]
        canal = f"<#{r['canal']}>" if r.get("canal") else "*não definido*"
        ini, fim, mes = _rank_periodo(self.meses_atras)
        tabela = _rank_tabela(g.id, cfg, ini, fim)
        total = sum(t["tickets"] for _, t in tabela)
        medalhas = [_poyo("rank_1", "🥇"), _poyo("rank_2", "🥈"), _poyo("rank_3", "🥉")]
        topo = "\n".join(
            f"{medalhas[i]} <@{uid}>  **{t['pts']} pts**  ·  {t['tickets']}"
            for i, (uid, t) in enumerate(tabela[:3])
        ) or "*sem atendimentos neste mês*"

        async def ligar(interaction, item):
            atual = get_cfg(g.id)["ranking"]
            if not atual["ativo"] and not g.get_channel(atual.get("canal") or 0):
                await self.update(interaction, "⚠️ Escolha o canal do placar antes de ligar")
                return
            set_path(g.id, ("ranking", "ativo"), not atual["ativo"])
            await self.update(interaction, "✅ Desligado" if atual["ativo"] else "✅ Ligado", refresh=True)

        async def pontos(interaction, item):
            await interaction.response.send_modal(RankPontosModal(self))

        async def atualizar(interaction, item):
            if not get_cfg(g.id)["ranking"]["ativo"]:
                await self.update(interaction, "⚠️ Ligue o ranking primeiro")
                return
            await self.update(interaction, "🔄 Pronto", refresh=True)

        async def virar_mes(interaction, item):
            self.meses_atras = 0 if self.meses_atras else 1
            await self.update(interaction)

        async def escolher_canal(interaction, item):
            set_path(g.id, ("ranking", "canal"), item.values[0].id)
            await self.update(interaction, "✅ Salvo", refresh=True)

        async def tirar_canal(interaction, item):
            set_path(g.id, ("ranking", "canal"), None)
            await self.update(interaction, "✅ Removido")

        verde, cinza = discord.ButtonStyle.success, discord.ButtonStyle.secondary
        aval = r.get("avaliacao", True)
        return [
            discord.ui.TextDisplay(
                f"## {_poyo('trofeu', '🏆')} Ranking da equipe\n"
                "-# pontos por ticket atendido e nota de 1 a 5 estrelas dada pelo dono do ticket"),
            sep(),
            discord.ui.TextDisplay(
                f"**Status**  {'🟢 ligado' if ativo else '⚪ desligado'}\n"
                f"**Canal do placar**  {canal}\n"
                f"**Avaliação por estrelas**  {'🟢 pede na DM ao encerrar' if aval else '⚪ desligada'}\n"
                f"**Pontuação**  {r['pontos_ticket']} por ticket"
                + (f" · +{r['pontos_estrela']} por estrela" if aval else "")
            ),
            sep(),
            _sec(f"{_poyo('trofeu', '🏆')} {mes}", f"{total} atendimento(s) no mês"),
            discord.ui.TextDisplay(topo),
            sep(),
            discord.ui.ActionRow(
                self._btn("Desligar" if ativo else "Ligar", ligar, cinza if ativo else verde),
                self._btn("Estrelas: sim" if aval else "Estrelas: não", self._flip(("ranking", "avaliacao")),
                          verde if aval else cinza, _poyo_ui("estrela", "⭐")),
                self._btn("Pontuação", pontos, emoji="🎯"),
            ),
            discord.ui.ActionRow(
                self._btn("Atualizar placar", atualizar, discord.ButtonStyle.primary, "🔄"),
                self._btn("Ver este mês" if self.meses_atras else "Ver mês passado", virar_mes, emoji="📅"),
            ),
            sep(),
            _sec("📍 Canal do placar", "uma mensagem que se atualiza sozinha a cada ticket encerrado"),
            discord.ui.ActionRow(_ChanSel(
                escolher_canal, placeholder="Escolher o canal",
                channel_types=[discord.ChannelType.text], min_values=1, max_values=1)),
            discord.ui.ActionRow(self._btn("Tirar canal", tirar_canal)),
        ]


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._locks: dict = {}
        self._confirmando: dict = {}   # canal do ticket -> confirmacao de parceria esperando o ok

    def _lock(self, guild_id) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, asyncio.Lock())

    async def cog_load(self):
        register_views(self.bot)
        self._ler_emojis()      # se o bot ja estiver ligado (reload da cog)

    def _ler_emojis(self):
        # guarda os emojis poyo_* de todos os servidores onde o bot esta
        achados = {}
        for e in self.bot.emojis:
            if e.name.startswith("poyo_") and e.available:
                achados.setdefault(e.name, str(e))
        AUTO_EMOJIS.clear()
        AUTO_EMOJIS.update(achados)

    @commands.Cog.listener()
    async def on_ready(self):
        self._ler_emojis()

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        self._ler_emojis()

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not (ctx.author.guild_permissions.administrator or tem_acesso_total(ctx.author)):
            raise commands.MissingPermissions(["administrator"])
        return True

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        # ticket apagado na mao: limpa o regisrto (e a call dele)
        data = state["tickets"].pop(str(channel.id), None)
        if data is not None:
            _save()
            await _apagar_call(channel.guild, data)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread):
        # topico de ticket apagado na mao: limpa o registro
        data = state["tickets"].pop(str(thread.id), None)
        if data is not None:
            _save()
            if data.get("liberados"):
                await _limpar_liberados(thread.guild, data.get("parent"), data["liberados"])
            await _apagar_call(thread.guild, data)

    @commands.Cog.listener()
    async def on_thread_update(self, before, after):
        # o discord arquiva topico parado: ticket aberto tem que continuar aberto
        data = state["tickets"].get(str(after.id))
        if data and after.archived and not before.archived and not data.get("closing"):
            try:
                await after.edit(archived=False)
            except discord.HTTPException as e:
                log.warning("nao consegui reabrir o topico do ticket %s: %s", after.id, e)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None or message.webhook_id:
            return
        channel = message.channel
        if not isinstance(channel, (discord.Thread, discord.TextChannel)):
            return
        data = state["tickets"].get(str(channel.id))   # so tickets registrados
        if not data or data.get("closing"):
            return
        if not message.content or not LINK_RE.search(message.content):
            return
        autor = message.author
        cfg = get_cfg(message.guild.id)

        # parceria: o dono do ticket manda o convite e o bot publica no canal de parcerias
        # (vale pro dono mesmo sendo staff)
        p = cfg["parceria"]
        if (p.get("ativo") and data["tipo"] == p.get("tipo") and autor.id == data["owner"]
                and INVITE_RE.search(message.content)):
            await self._auto_postar(message, data, cfg)
            return

        if isinstance(autor, discord.Member) and is_staff(autor, cfg, data["tipo"]):
            return

        if not cfg.get("bloquear_links"):
            return
        if data["tipo"] not in (cfg.get("links_tipos") or []):
            # categoria que nao aceita link: apaga e avisa rapidinho
            try:
                await message.delete()
            except discord.HTTPException as e:
                log.warning("nao consegui apagar link no ticket %s: %s (o bot precisa de Gerenciar Mensagens)",
                            channel.id, e)
                return
            try:
                await channel.send(
                    view=Card("Links não são permitidos aqui",
                              f"{autor.mention}, links só podem ser enviados em tickets de parceria."),
                    allowed_mentions=discord.AllowedMentions(users=[autor]),
                    delete_after=8,
                )
            except discord.HTTPException:
                pass
            return

        # categoria liberada: se outro bot apagar ou cortar o link, o nosso reposta o texto inteiro
        original = message.content
        for espera in (2, 4):
            await asyncio.sleep(espera)
            if message.id in _LINKS_REPOSTADOS:
                return
            try:
                atual = await channel.fetch_message(message.id)
                if atual.content == original:
                    continue          # intacta: confere de novo mais tarde
            except discord.NotFound:
                pass                  # foi apagada
            except discord.HTTPException:
                return
            _LINKS_REPOSTADOS.append(message.id)
            del _LINKS_REPOSTADOS[:-200]
            try:
                await channel.send(
                    view=Card(f"Link enviado por {autor.display_name}", original[:3500]),
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException as e:
                log.warning("nao consegui repostar o link no ticket %s: %s", channel.id, e)
            return

    def _dup_parceria(self, guild, p, alvo_id, nome):
        # devolve (titulo, texto) se o servidor ainda nao pode postar, senao None
        if not p.get("bloquear_duplicado", True):
            return None
        dias = int(p.get("renovar_dias", 5) or 0)
        agora = int(datetime.now().timestamp())

        def livre(e):   # ja passou o prazo de renovacao
            return dias > 0 and agora - int(e.get("ts", 0) or 0) >= dias * 86400

        def quando(e):
            if dias <= 0:
                return "Cada servidor só pode ter uma parceria postada. A equipe pode liberar."
            libera = int(e.get("ts", 0) or 0) + dias * 86400
            return f"Você pode renovar em <t:{libera}:D> (<t:{libera}:R>)."

        reg = _parc_guild(guild.id)
        e = reg.get(str(alvo_id))
        if e:
            if livre(e):
                return None
            return ("Esse servidor já tem parceria postada",
                    f"**{nome}** foi publicada em <t:{e.get('ts', 0)}:D>. {quando(e)}")
        if p.get("conferir_nome", True):
            nn = _norm_nome(nome)
            barram = [x for x in reg.values()
                      if x.get("por_nome") and _nomes_parecidos(nn, _norm_nome(x.get("nome", ""))) and not livre(x)]
            if barram:
                achou = max(barram, key=lambda x: x.get("ts", 0))
                onde = ""
                if achou.get("canal") and achou.get("msg"):
                    onde = f" {'https://discord.com/channels/%s/%s/%s' % (guild.id, achou['canal'], achou['msg'])}"
                return ("Esse servidor parece já ter parceria",
                        f"**{nome}** tem o mesmo nome de **{achou.get('nome')}**, "
                        f"já postado em <t:{achou.get('ts', 0)}:D>.{onde}\n{quando(achou)}\n"
                        "Se for outro servidor, chame a equipe pra liberar.")
        return None

    async def _auto_postar(self, message, data, cfg):
        p = cfg["parceria"]
        guild, channel, autor = message.guild, message.channel, message.author

        async def aviso(titulo, texto, ping=False):
            try:
                await channel.send(
                    view=Card(titulo, texto),
                    allowed_mentions=discord.AllowedMentions(users=[autor]) if ping else NO_MENTIONS,
                )
            except discord.HTTPException:
                pass

        codigos = []
        for c in INVITE_RE.findall(message.content):
            if c.lower() not in (x.lower() for x in codigos):
                codigos.append(c)
        if not codigos:
            return
        if len(codigos) > 1:
            return await aviso("Um convite por vez", f"{autor.mention}, mande o convite de um servidor só.", True)

        destino = guild.get_channel(p["canal"]) if p.get("canal") else None
        if not isinstance(destino, discord.TextChannel):
            return await aviso("Postagem automática indisponível",
                               "O canal de parcerias não está configurado. A equipe vai publicar manualmente.")

        try:
            convite = await self.bot.fetch_invite(codigos[0], with_counts=False)
        except discord.NotFound:
            return await aviso("Convite inválido",
                               f"{autor.mention}, esse convite não existe ou expirou. Mande um convite válido.", True)
        except discord.HTTPException:
            return await aviso("Não consegui verificar o convite", "Tente de novo em instantes.")
        alvo = getattr(convite, "guild", None)
        if alvo is None:
            return await aviso("Convite inválido", f"{autor.mention}, mande o convite de um servidor.", True)
        if alvo.id == guild.id:
            return await aviso("Esse é o convite do nosso servidor",
                               f"{autor.mention}, mande o convite do **seu** servidor.", True)
        nome = getattr(alvo, "name", None) or str(alvo.id)

        async with self._lock(guild.id):
            dup = self._dup_parceria(guild, p, alvo.id, nome)
        if dup:
            return await aviso(*dup)

        # monta o texto: limpa o que o painel mandou tirar, ping do cargo e credito
        corpo = _limpar_parceria(message.content, p)
        topo = f"<@&{p['ping_cargo']}>" if p.get("ping_cargo") else ""
        rodape = f"-# enviado por {autor.mention}" if p.get("creditar", True) else ""
        reserva = len(topo) + len(rodape) + 60
        if len(corpo) > 2000 - reserva:
            corpo = corpo[: 2000 - reserva].rstrip()
            if codigos[0].lower() not in corpo.lower():
                corpo += f"\ndiscord.gg/{codigos[0]}"
        conteudo = "\n".join(x for x in (topo, corpo, rodape) if x)[:2000]

        async def publicar():
            async with self._lock(guild.id):
                # confere de novo: com a confirmacao, outro ticket pode ter postado esse servidor
                dup2 = self._dup_parceria(guild, p, alvo.id, nome)
                if dup2:
                    return await aviso(*dup2)

                # copia os anexos antes de apagar a mensagem original
                arquivos = []
                for a in message.attachments[:4]:
                    if a.size <= guild.filesize_limit:
                        try:
                            arquivos.append(await a.to_file())
                        except discord.HTTPException:
                            pass
                kwargs = {"files": arquivos} if arquivos else {}
                ping_roles = [discord.Object(p["ping_cargo"])] if p.get("ping_cargo") else False
                try:
                    post = await destino.send(
                        conteudo,
                        allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=ping_roles),
                        **kwargs,
                    )
                except discord.HTTPException as e:
                    log.warning("nao consegui postar a parceria em #%s: %s", destino.name, e)
                    return await aviso("Não consegui publicar",
                                       "O bot precisa de permissão pra enviar mensagens no canal de parcerias. "
                                       "A equipe vai ver isso.")

                reg = _parc_guild(guild.id)
                nn_novo = _norm_nome(nome)
                for k, x in list(reg.items()):
                    # registro antigo guardado so pelo nome: o novo (pelo convite) substitui
                    if k != str(alvo.id) and x.get("por_nome") and _nomes_parecidos(nn_novo, _norm_nome(x.get("nome", ""))):
                        del reg[k]
                reg[str(alvo.id)] = {
                    "nome": nome, "codigo": codigos[0], "autor": autor.id,
                    "ts": int(datetime.now().timestamp()), "canal": destino.id, "msg": post.id,
                    "salvo_em": int(datetime.now().timestamp()),
                }
                _parc_save()

            try:
                await message.delete()
            except discord.HTTPException as e:
                log.warning("nao consegui apagar a mensagem original da parceria no ticket %s: %s", channel.id, e)
            dias_r = int(p.get("renovar_dias", 5) or 0)
            nota_r = ""
            if dias_r > 0 and p.get("bloquear_duplicado", True):
                nota_r = f"\n-# Renovação liberada em <t:{int(datetime.now().timestamp()) + dias_r * 86400}:D>."
            await aviso("Parceria publicada", f"{autor.mention}, sua parceria foi publicada: {post.jump_url}{nota_r}", True)

        if not p.get("confirmar"):
            return await publicar()

        # confirmacao ligada: mostra o preview e espera o ok antes de postar
        velho = self._confirmando.pop(channel.id, None)
        if velho is not None:
            await velho.cancelar("Substituída", "Você mandou outro convite, vale o último.")
        n_anexos = sum(1 for a in message.attachments[:4] if a.size <= guild.filesize_limit)
        view = ConfirmarParceriaView(autor.id, data["tipo"], conteudo, destino, n_anexos, publicar)
        view.fim = lambda: self._confirmando.pop(channel.id, None) if self._confirmando.get(channel.id) is view else None
        try:
            view.sent = await channel.send(view=view, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException as e:
            log.warning("nao consegui mandar a confirmacao da parceria no ticket %s: %s", channel.id, e)
            return
        self._confirmando[channel.id] = view

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        # apagou o post da parceria no canal: o servidor sai do registro e pode postar de novo
        if payload.guild_id is None:
            return
        reg = _parc["guilds"].get(str(payload.guild_id))
        if not reg:
            return
        for k, e in list(reg.items()):
            if e.get("msg") == payload.message_id:
                del reg[k]
                _parc_save()
                log.info("parceria %s saiu do registro (post apagado)", e.get("nome", k))

    @commands.group(
        name="ptparc",
        aliases=("parcerias",),
        invoke_without_command=True,
        extras={
            "categoria": "Utilidades",
            "uso": ",ptparc",
            "descricao": "Painel da postagem automática de parcerias e dos links dos tickets.",
        },
    )
    @commands.guild_only()
    async def ptparc(self, ctx):
        await ParceriaView.open(ctx)

    @commands.group(
        name="ptrank",
        aliases=("ranking",),
        invoke_without_command=True,
        extras={
            "categoria": "Utilidades",
            "uso": ",ptrank",
            "descricao": "Painel do ranking de atendimentos da equipe (pontos e estrelas).",
        },
    )
    @commands.guild_only()
    async def ptrank(self, ctx):
        await RankView.open(ctx)

    async def _updated(self, ctx, title, text=""):
        n = await refresh_panels(ctx.guild)
        extra = f"\n-# {n} painel(is) atualizado(s)." if n else ""
        await _say(ctx, title, text + extra)

    @commands.command(
        name="pticket",
        extras={
            "categoria": "Utilidades",
            "uso": ",pticket",
            "descricao": "Posta o painel de tickets no canal atual.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(
        send_messages=True, create_private_threads=True,
        send_messages_in_threads=True, manage_threads=True,
    )
    async def pticket(self, ctx):
        await post_panel(ctx.channel)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.group(
        name="ptconfig",
        invoke_without_command=True,
        extras={
            "categoria": "Utilidades",
            "uso": ",ptconfig",
            "descricao": "Personaliza o sistema de tickets (textos, cores, categorias, equipe...).",
        },
    )
    async def ptconfig(self, ctx):
        await ConfigView.open(ctx)

    @ptconfig.command(name="ajuda", aliases=("help",))
    async def cfg_ajuda(self, ctx):
        for texto in (HELP_1, HELP_2):
            view = discord.ui.LayoutView(timeout=None)
            view.add_item(discord.ui.Container(discord.ui.TextDisplay(texto)))
            await ctx.send(view=view)

    @ptconfig.command(name="set")
    async def cfg_set(self, ctx, chave: str, *, valor: str = ""):
        chave = chave.lower()
        if chave not in SETTINGS:
            await _say(ctx, "Opção desconhecida",
                       "Use `,ptconfig ajuda` pra ver a lista.", ok=False)
            return
        path, kind, limit, vazio, desc = SETTINGS[chave]
        # imagem por arquivo: anexa a imagem na mensagem do comando
        if chave in IMG_KEYS and ctx.message.attachments and not valor.strip():
            valor_salvo, erro = await aplicar_imagem(ctx.guild.id, chave, ctx.message.attachments[0])
            if erro:
                await _say(ctx, f"Não deu pra usar o arquivo em `{chave}`", erro, ok=False)
                return
            set_path(ctx.guild.id, path, valor_salvo)
            await self._updated(ctx, f"`{chave}` atualizado", _show(ctx.guild, chave, get_cfg(ctx.guild.id)))
            return
        if not valor.strip():
            extra = " ou anexe uma imagem" if chave in IMG_KEYS else ""
            await _say(ctx, f"Faltou o valor de `{chave}`", f"Mande o valor{extra}.", ok=False)
            return
        default = DEFAULTS.get(path[0]) if len(path) == 1 else None
        value, erro = await parse_value(ctx, kind, valor, limit, vazio, default=default)
        if erro:
            await _say(ctx, f"Valor inválido pra `{chave}`", erro, ok=False)
            return
        set_path(ctx.guild.id, path, value)
        await self._updated(ctx, f"`{chave}` atualizado", _show(ctx.guild, chave, get_cfg(ctx.guild.id)))

    @ptconfig.command(name="atualizar", aliases=("refresh",))
    async def cfg_atualizar(self, ctx):
        await self._updated(ctx, "Painéis atualizados")

    @ptconfig.command(name="reset")
    async def cfg_reset(self, ctx, confirmar: str = ""):
        if confirmar.lower() != "tudo":
            await _say(ctx, "Isso apaga toda a personalização",
                       "Se tiver certeza, use `,ptconfig reset tudo`.", ok=False)
            return
        paineis = get_cfg(ctx.guild.id)["paineis"]
        _cfg_store["guilds"][str(ctx.guild.id)] = {"paineis": paineis}
        _save_cfg()
        await self._updated(ctx, "Tudo voltou ao padrão")

    # ------------------------------------------------------------ categorias

    @ptconfig.group(name="cat", aliases=("categorias", "cats"), invoke_without_command=True)
    async def cfg_cat(self, ctx):
        await ConfigView.open(ctx, ("cats",))

    @cfg_cat.command(name="add", aliases=("novo", "criar"))
    async def cat_add(self, ctx, chave: str, emoji: str, *, nome: str):
        chave = chave.lower()
        if emoji.lower() in NONE_WORDS:
            emoji = ""   # "-" = sem emoji
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
        await self._updated(ctx, f"Categoria {emoji} {nome} criada".replace("  ", " "),
                            f"Edite com `,ptconfig cat edit {chave} dica ...` e `... botao azul`.")

    @cfg_cat.command(name="edit", aliases=("editar", "set"))
    async def cat_edit(self, ctx, chave: str, campo: str, *, valor: str):
        chave, campo = chave.lower(), campo.lower()
        cats = _cats_over(ctx.guild.id)
        if chave not in cats:
            await _say(ctx, "Categoria não encontrada", "Veja as chaves em `,ptconfig`.", ok=False)
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
