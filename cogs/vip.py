# cog do painel VIP - components v2 (container), mesmo visual fofinho do painel de tickets
#
# comandos (admin):
#   ,pvip                          posta o painel no canal atual
#   ,pvip atualizar                reedita os paineis ja postados
#   ,pvipconfig                    abre o painel de config (la tem o alternador Slash / ,)
#   imagens: igual o ticket - no painel de config tem o 📎 (o bot pede a imagem no chat), ou:
#   ,pvipconfig topo               anexe uma imagem (ou responda uma) = banner do painel
#   ,pvipconfig fim                anexe uma imagem = decoracao de baixo (padrao: gif de lacinhos)
#   ,pvipconfig imagem <vip>       anexe uma imagem = banner do cartao daquele VIP
#   (os tres aceitam tambem um link, "nenhum" (sem imagem) ou "padrao" no lugar do anexo)
#   ,pvipconfig ajuda              como mudar TODOS os textos (painel e cada VIP)
#   ,pvipconfig texto <campo> ...  muda um texto do painel (titulo, descricao, info, menu, botao...)
#   ,pvipconfig vip <vip> <campo> ...  muda nome, descricao, resumo, perms, emoji ou cargo de um VIP
#   ,pvipconfig ver                mostra tudo que esta valendo agora
#
# o painel NAO tem lista: tem um menu "Conheça cada VIP". quem escolhe um cargo
# recebe um cartao so pra ele (ephemeral) com a descricao e as permissoes.
# o botao "Adquirir VIP" abre o ticket de Pagamentos.
#
# >>> pra colocar os textos do seu amigo: edite so o dicionario VIPS aqui embaixo <<<
import asyncio
import copy
import json
import logging
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

try:
    from ._acesso import tem_acesso_total   # cargo com acesso a todos os comandos
except ImportError:
    def tem_acesso_total(membro) -> bool:
        return False

log = logging.getLogger("bot.vip")

NO_MENTIONS = discord.AllowedMentions.none()
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
STATE_FILE = Path(os.getenv("VIP_STATE_FILE", "vip_state.json"))
CONFIG_FILE = Path(os.getenv("VIP_CONFIG_FILE", "vip_config.json"))   # textos editados pelo admin

# ---------------------------------------------------------------- textos (edite aqui)

TITULO = "Planos VIP"
DESCRICAO = "escolha o seu VIP no menu abaixo e veja tudo sobre ele ✨"
INFO = "{pix} pagamento via Pix · atendimento pelo ticket ✨"   # "" = sem essa linha
MENU = "Conheça cada VIP"          # texto do menu fechado
BOTAO = "Adquirir VIP"             # texto do botao
PERMISSOES = "Permissões"          # titulo da lista de permissoes no cartao

# barra colorida do lado do container: None = sem barra (igual o ticket)
COR_PAINEL = None
# idem pro cartao de cada VIP: None = sem barra | "cargo" = usa a cor do cargo | 0xRRGGBB
COR_CARTAO = None

# categoria do ticket que o botao "Adquirir VIP" abre ("" = esconde o botao)
TICKET_CATEGORIA = "pagamentos"
BOTAO_ESTILO = "cinza"        # cinza | azul | verde | vermelho

# True = permissoes numa linha so  |  False = uma por linha
PERMS_EM_LINHA = False
MARCA = "✧"   # marcador de cada permissao

# cada VIP:
#   nome       texto que aparece
#   cargo      nome (ou ID) do cargo no servidor (mostra a mencao no cartao)
#   emoji      nome de um emoji do servidor (poyo_...) ou um emoji/glifo direto
#   glifo      o que aparece se o emoji do servidor nao existir
#   cor        so usado se COR_CARTAO = "cargo" e o cargo nao tiver cor
#   resumo     frase curta que aparece no menu (max 100 caracteres)
#   descricao  texto do cartao
#   perms      lista de permissoes
VIPS = {
    "elite": {
        "nome": "Elite", "cargo": "Elite", "emoji": "poyo_parceria", "glifo": "✦", "cor": 0xEB459E,
        "resumo": "Toque pra ver os detalhes do Elite.",
        "descricao": "Descrição do Elite em breve.",
        "perms": ["Permissões do Elite em breve."],
    },
    "imune": {
        "nome": "Imune", "cargo": "Imune", "emoji": "poyo_suporte", "glifo": "✧", "cor": 0x5865F2,
        "resumo": "Toque pra ver os detalhes do Imune.",
        "descricao": "Descrição do Imune em breve.",
        "perms": ["Permissões do Imune em breve."],
    },
    "imperio": {
        "nome": "Império", "cargo": "Império", "emoji": "poyo_desabafo", "glifo": "❖", "cor": 0x1ABC9C,
        "resumo": "Toque pra ver os detalhes do Império.",
        "descricao": "Descrição do Império em breve.",
        "perms": ["Permissões do Império em breve."],
    },
    "owner": {
        "nome": "Owner", "cargo": "Owner", "emoji": "poyo_outros", "glifo": "⋆", "cor": 0xFEE75C,
        "resumo": "Toque pra ver os detalhes do Owner.",
        "descricao": "Descrição do Owner em breve.",
        "perms": ["Permissões do Owner em breve."],
    },
}

BUTTON_STYLES = {
    "azul": discord.ButtonStyle.primary,
    "cinza": discord.ButtonStyle.secondary,
    "verde": discord.ButtonStyle.success,
    "vermelho": discord.ButtonStyle.danger,
}

# ---------------------------------------------------------------- textos editaveis (por servidor)

LIM_PAINEL = {"titulo": 100, "descricao": 800, "info": 300, "menu": 150, "botao": 80, "permissoes": 40}
LIM_VIP = {"nome": 80, "descricao": 1500, "resumo": 100, "perms": 1500, "emoji": 80, "cargo": 100}
VAZIO = {"nenhum", "vazio", "-", "sumir"}


def _carregar_cfg() -> dict:
    try:
        d = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("guilds"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"guilds": {}}


_store = _carregar_cfg()


def _salvar_cfg():
    try:
        CONFIG_FILE.write_text(json.dumps(_store, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("nao consegui salvar %s: %s", CONFIG_FILE, e)


def get_cfg(guild_id=None) -> dict:
    """textos valendo agora = padrao do arquivo + o que o admin mudou nesse servidor"""
    c = {"titulo": TITULO, "descricao": DESCRICAO, "info": INFO, "menu": MENU,
         "botao": BOTAO, "permissoes": PERMISSOES, "vips": copy.deepcopy(VIPS)}
    ov = _store["guilds"].get(str(guild_id), {}) if guild_id is not None else {}
    for campo in LIM_PAINEL:
        if campo in ov.get("painel", {}):
            c[campo] = ov["painel"][campo]
    for key, campos in ov.get("vips", {}).items():
        if key in c["vips"]:
            c["vips"][key].update(campos)
    return c


def _set_painel(gid, campo, valor):
    g = _store["guilds"].setdefault(str(gid), {})
    if valor is None:
        g.get("painel", {}).pop(campo, None)
    else:
        g.setdefault("painel", {})[campo] = valor
    _salvar_cfg()


def _set_vip(gid, key, campo, valor):
    g = _store["guilds"].setdefault(str(gid), {})
    if valor is None:
        g.get("vips", {}).get(key, {}).pop(campo, None)
    else:
        g.setdefault("vips", {}).setdefault(key, {})[campo] = valor
    _salvar_cfg()


def get_modo_slash(gid) -> bool:
    # False = comandos com "," (o slash tambem funciona) | True = so slash
    return bool(_store["guilds"].get(str(gid), {}).get("modo_slash", False))


def set_modo_slash(gid, valor: bool):
    _store["guilds"].setdefault(str(gid), {})["modo_slash"] = bool(valor)
    _salvar_cfg()


def aplicar_painel(gid, campo: str, valor: str):
    """muda um texto do painel -> (erro, mensagem de ok)"""
    campo = _norm(campo)
    if campo not in LIM_PAINEL:
        return "Texto desconhecido. Use: " + ", ".join(f"`{k}`" for k in LIM_PAINEL), None
    v = valor.strip()
    if v.lower() in REMOVER:
        _set_painel(gid, campo, None)
        return None, f"`{campo}` voltou ao padrão"
    if campo == "info" and v.lower() in VAZIO:
        _set_painel(gid, campo, "")
        return None, "linha de info removida"
    if not v:
        return "Faltou o texto novo.", None
    if len(v) > LIM_PAINEL[campo]:
        return f"`{campo}` aceita no máximo {LIM_PAINEL[campo]} caracteres (o seu tem {len(v)}).", None
    _set_painel(gid, campo, v)
    return None, f"`{campo}` atualizado"


def aplicar_vip(gid, key: str, campo: str, valor: str):
    """muda um campo de um VIP -> (erro, mensagem de ok)"""
    campo = _norm(campo)
    if campo not in LIM_VIP:
        return "Campo desconhecido. Use: " + ", ".join(f"`{k}`" for k in LIM_VIP), None
    v = valor.strip()
    if v.lower() in REMOVER:
        _set_vip(gid, key, campo, None)
        return None, f"`{campo}` voltou ao padrão"
    if not v:
        return "Faltou o valor novo.", None
    if len(v) > LIM_VIP[campo]:
        return f"`{campo}` aceita no máximo {LIM_VIP[campo]} caracteres (o seu tem {len(v)}).", None
    novo = v
    if campo == "perms":
        # uma permissao por linha (ou separadas por |)
        novo = [x.strip() for x in re.split(r"[\n|]+", v) if x.strip()]
        if len(novo) > 15 or any(len(x) > 100 for x in novo):
            return "Máximo de 15 permissões, cada uma com até 100 caracteres.", None
    elif campo == "emoji":
        if re.fullmatch(r":\w+:", v):
            novo = v.strip(":")          # :poyo_pix: -> poyo_pix
    elif campo == "cargo":
        m = re.fullmatch(r"<@&(\d+)>", v)
        if m:
            novo = int(m.group(1))       # @menção do cargo -> id
    _set_vip(gid, key, campo, novo)
    return None, f"`{campo}` de **{get_cfg(gid)['vips'][key]['nome']}** atualizado"


# ---------------------------------------------------------------- imagens (mesmo sistema do ticket)
#
# cada imagem (topo, fim e uma por VIP) guarda um valor por servidor:
#   None = padrao | "" = sem imagem | link http(s) | "arquivo:<nome>" = upload do admin
# o upload fica em assets/custom com nome novo a cada envio (assim o discord
# nao reaproveita a imagem antiga do cache)

GIF_FILE = ASSETS_DIR / "gif.gif"            # decoracao padrao de baixo (lacinhos)
if not GIF_FILE.is_file():
    GIF_FILE = ASSETS_DIR / "Tumblr-l-67143811701311.gif"
GIF_NAME = "gif.gif"
CUSTOM_DIR = ASSETS_DIR / "custom"
FILE_PREFIX = "arquivo:"
IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_IMG_BYTES = 8 * 1024 * 1024
REMOVER = {"remover", "remove", "tirar", "apagar", "limpar", "padrao", "padrão", "reset"}


def _custom_path(valor) -> Optional[Path]:
    # valor "arquivo:xxx" -> caminho no disco (so se o arquivo ainda existir)
    if isinstance(valor, str) and valor.startswith(FILE_PREFIX):
        nome = Path(valor[len(FILE_PREFIX):]).name   # .name = nao deixa sair da pasta
        path = CUSTOM_DIR / nome
        return path if path.is_file() else None
    return None


async def save_image(guild_id, chave: str, attachment: discord.Attachment):
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
    nome = f"{guild_id}_{chave}_{int(datetime.now().timestamp())}{ext}"
    try:
        (CUSTOM_DIR / nome).write_bytes(dados)
    except OSError as e:
        log.warning("nao consegui salvar a imagem %s: %s", nome, e)
        return None, "Não consegui salvar o arquivo no servidor."
    _limpar_arquivos(guild_id, chave, manter=nome)     # apaga as versoes antigas
    return FILE_PREFIX + nome, None


def _limpar_arquivos(guild_id, chave: str, manter: Optional[str] = None):
    for velho in CUSTOM_DIR.glob(f"{guild_id}_{chave}_*"):
        if velho.name != manter:
            try:
                velho.unlink()
            except OSError:
                pass


def _get_img(gid, chave: str):
    if gid is None:
        return None
    return _store["guilds"].get(str(gid), {}).get("imagens", {}).get(chave)


def _set_img(gid, chave: str, valor):
    g = _store["guilds"].setdefault(str(gid), {})
    if valor is None:
        g.get("imagens", {}).pop(chave, None)
    else:
        g.setdefault("imagens", {})[chave] = valor
    _salvar_cfg()


def _status_img(valor, padrao: str) -> str:
    if valor is None:
        return f"— {padrao}"
    if valor == "":
        return "sem imagem"
    if valor.startswith(FILE_PREFIX):
        return "✅ enviada" if _custom_path(valor) else "⚠️ arquivo sumiu"
    return "🔗 link"


def _midia(gid, vip: Optional[str] = None):
    # (url do topo, url do fim, arquivos) - File so vale pra um envio, entao monta de novo toda vez
    arquivos, usados = [], set()

    def url(valor):
        if isinstance(valor, str) and valor.startswith(FILE_PREFIX):
            path = _custom_path(valor)
            if path is None:
                return None                      # o arquivo foi apagado do disco
            if path.name not in usados:
                usados.add(path.name)
                arquivos.append(discord.File(path, filename=path.name))
            return f"attachment://{path.name}"
        return valor or None

    do_vip = _get_img(gid, f"card_{vip}") if vip else None
    topo = url(do_vip if do_vip is not None else _get_img(gid, "topo"))   # a do VIP vence a do topo
    f = _get_img(gid, "fim")
    if f is None:                                # padrao: gif de lacinhos
        fim = None
        if GIF_FILE.is_file():
            arquivos.append(discord.File(GIF_FILE, filename=GIF_NAME))
            fim = f"attachment://{GIF_NAME}"
    else:
        fim = url(f)
    return topo, fim, arquivos


async def aplicar_imagem(gid, chave: str, anexo: Optional[discord.Attachment] = None, valor: str = ""):
    """salva uma imagem (anexo, link, 'nenhum' ou 'padrao') -> (erro, mensagem de ok)"""
    if anexo is not None:
        novo, erro = await save_image(gid, f"vip_{chave}", anexo)
        if erro:
            return erro, None
        _set_img(gid, chave, novo)
        return None, "imagem salva"
    t = (valor or "").strip()
    baixo = t.lower()
    if baixo in REMOVER:
        _set_img(gid, chave, None)
        _limpar_arquivos(gid, f"vip_{chave}")
        return None, "voltou ao padrão"
    if baixo in VAZIO:
        _set_img(gid, chave, "")
        _limpar_arquivos(gid, f"vip_{chave}")
        return None, "agora sem imagem"
    if re.fullmatch(r"https?://\S+", t):
        _set_img(gid, chave, t)
        _limpar_arquivos(gid, f"vip_{chave}")
        return None, "link salvo"
    return "Anexe uma imagem, mande um link (https://...) ou use `nenhum` / `padrao`.", None


# ---------------------------------------------------------------- emojis

EMOJIS: dict = {}   # nome -> "<:nome:id>" (a cog preenche quando o bot liga)


def _achar_emoji(nome: str) -> str:
    # acha poyo_parceria tambem como poyo_parcerias (igual o ticket faz)
    base = (nome or "").strip().lower()
    if not base:
        return ""
    for n in (base, f"{base}s", base.rstrip("s")):
        if n in EMOJIS:
            return EMOJIS[n]
    return ""


def _emoji(nome: str, padrao: str = "") -> str:
    return os.getenv(f"VIP_EMOJI_{nome.upper()}", "").strip() or _achar_emoji(nome) or padrao


def _pix() -> str:
    return _emoji("poyo_pix", "✨")


def _vip_emoji(v: dict) -> str:
    e = (v.get("emoji") or "").strip()
    if e.startswith("poyo_"):
        return _achar_emoji(e) or v.get("glifo", "✦")
    return e or v.get("glifo", "✦")


def _ui_emoji(e):
    # so devolve emoji que o discord aceita em botao/menu (custom ou unicode de verdade)
    # glifo decorativo tipo ✦ vira None e fica so no texto
    e = (e or "").strip()
    if re.fullmatch(r"<a?:\w+:\d{13,20}>", e):
        return discord.PartialEmoji.from_str(e)
    if not e or len(e) > 16 or re.search(r"[A-Za-z0-9]", e):
        return None
    ok = "\ufe0f" in e or all(ord(ch) >= 0x1F000 or ch in "\u200d⭐✨⚡✅" for ch in e)
    return e if ok else None


# ---------------------------------------------------------------- helpers de texto

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
        m = miudo
        if t.startswith("-# "):
            t = t[3:]
            m = True
        t = t.replace("**", "")
        linhas.append(f"{'-# ' if m else ''}**{t}**")
    return "\n".join(linhas)


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return s.casefold().strip()


def _achar_cargo(guild: discord.Guild, ref) -> Optional[discord.Role]:
    # ref = id ou nome (sem ligar pra acento/maiuscula); acha tambem "👑 Owner"
    if ref is None or guild is None:
        return None
    if isinstance(ref, int) or str(ref).isdigit():
        return guild.get_role(int(ref))
    alvo = _norm(ref)
    for r in guild.roles:
        if _norm(r.name) == alvo:
            return r
    for r in guild.roles:
        if alvo and alvo in _norm(r.name) and not r.is_default():
            return r
    return None


def _achar_vip(texto: str, vips: Optional[dict] = None) -> Optional[str]:
    alvo = _norm(texto)
    for key, v in (vips if vips is not None else VIPS).items():
        if alvo in (_norm(key), _norm(v["nome"])):
            return key
    return None


def _perms_txt(perms) -> str:
    perms = [str(p).strip() for p in (perms or []) if str(p).strip()]
    if not perms:
        return "-# **em breve**"
    if PERMS_EM_LINHA:
        return "   ".join(f"**{p}**" for p in perms)
    return "\n".join(f"{MARCA} **{p}**" for p in perms)


# ---------------------------------------------------------------- cartoes

class Card(discord.ui.LayoutView):
    # cartao simples pra avisos (container puro, igual o do ticket)
    def __init__(self, title: str, text: str = ""):
        super().__init__(timeout=None)
        body = f"### {title}" + (f"\n{text}" if text else "")
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(body)))


def _cor_cartao(v: dict, cargo: Optional[discord.Role]):
    if COR_CARTAO == "cargo":
        if cargo is not None and cargo.colour.value:
            return cargo.colour.value
        return v.get("cor")
    return COR_CARTAO


def build_detalhe(guild: discord.Guild, key: str):
    """cartao do VIP escolhido (so quem clicou ve) -> (view, arquivos)"""
    c = get_cfg(guild.id if guild is not None else None)
    v = c["vips"][key]
    cargo = _achar_cargo(guild, v.get("cargo"))

    cab = (
        f"## {_vip_emoji(v)}  {v['nome']}\n"
        f"{_negrito(v.get('descricao') or 'Descrição em breve.')}"
    )
    # foto do servidor do lado, igual o ticket faz com o avatar
    if guild is not None and guild.icon is not None:
        topo_txt = discord.ui.Section(cab, accessory=discord.ui.Thumbnail(guild.icon.url))
    else:
        topo_txt = discord.ui.TextDisplay(cab)

    topo, fim, arquivos = _midia(guild.id if guild is not None else None, vip=key)
    itens = []
    if topo:
        itens += [discord.ui.MediaGallery(discord.MediaGalleryItem(topo)), discord.ui.Separator()]
    itens += [
        topo_txt,
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"**{c['permissoes']}**\n{_perms_txt(v.get('perms'))}"),
    ]
    if cargo is not None:
        itens.append(discord.ui.TextDisplay(f"-# **Cargo:** {cargo.mention}"))
    if TICKET_CATEGORIA:
        itens += [discord.ui.Separator(), discord.ui.ActionRow(ComprarBotao(c["botao"]))]
    if fim:
        itens.append(discord.ui.MediaGallery(discord.MediaGalleryItem(fim)))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*itens, accent_colour=_cor_cartao(v, cargo)))
    return view, arquivos


def build_panel_view(guild: Optional[discord.Guild] = None):
    """painel inteiro num container so -> (view, arquivos)"""
    c = get_cfg(guild.id if guild is not None else None)
    topo, fim, arquivos = _midia(guild.id if guild is not None else None)
    pix = _pix()
    itens = []
    if topo:
        itens += [discord.ui.MediaGallery(discord.MediaGalleryItem(topo)), discord.ui.Separator()]

    itens.append(discord.ui.TextDisplay(f"## {pix}  {c['titulo']}\n{_negrito(c['descricao'])}"))

    # um VIP embaixo do outro, cada um com o seu emoji (igual as categorias do ticket)
    if c["vips"]:
        linha = "\n".join(f"{_vip_emoji(v)} **{v['nome']}**" for v in c["vips"].values())
        itens += [discord.ui.Separator(), discord.ui.TextDisplay(linha)]

    itens += [discord.ui.Separator(), discord.ui.ActionRow(VipSelect(guild))]
    if TICKET_CATEGORIA:
        itens.append(discord.ui.ActionRow(ComprarBotao(c["botao"])))
        if c["info"]:
            itens.append(discord.ui.TextDisplay(_negrito(c["info"].replace("{pix}", pix), miudo=True)))
    if fim:
        itens.append(discord.ui.MediaGallery(discord.MediaGalleryItem(fim)))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*itens, accent_colour=COR_PAINEL))
    return view, arquivos


# ---------------------------------------------------------------- componentes

class VipSelect(discord.ui.Select):
    # menu do painel (custom_id fixo = continua valendo depois de reiniciar o bot)
    def __init__(self, guild=None):
        c = get_cfg(guild.id if guild is not None else None)
        options = []
        if guild is not None:
            for key, v in c["vips"].items():
                options.append(
                    discord.SelectOption(
                        label=v["nome"][:100], value=key, emoji=_ui_emoji(_vip_emoji(v)),
                        description=(v.get("resumo") or "")[:100] or None,
                    )
                )
        super().__init__(
            custom_id="vip:menu",
            placeholder=c["menu"][:150],
            options=options or [discord.SelectOption(label="…", value="vip")],
        )

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        vips = get_cfg(interaction.guild.id)["vips"] if interaction.guild is not None else {}
        if key not in vips:
            await interaction.response.send_message(
                view=Card("VIP indisponível", "Essa opção não existe mais."), ephemeral=True
            )
            return
        view, arquivos = build_detalhe(interaction.guild, key)
        await interaction.response.send_message(
            view=view, files=arquivos, ephemeral=True, allowed_mentions=NO_MENTIONS
        )
        # limpa a opcao marcada: sem isso nao da pra escolher a mesma de novo
        if interaction.message is not None:
            try:
                painel, files = build_panel_view(interaction.guild)
                await interaction.message.edit(view=painel, attachments=files)
            except discord.HTTPException as e:
                log.warning("nao consegui limpar o menu do painel vip: %s", e)


class ComprarBotao(discord.ui.Button):
    def __init__(self, label: Optional[str] = None):
        super().__init__(
            style=BUTTON_STYLES.get(BOTAO_ESTILO, discord.ButtonStyle.secondary),
            label=(label or BOTAO)[:80],
            emoji=_ui_emoji(_pix()),
            custom_id="vip:comprar",
        )

    async def callback(self, interaction: discord.Interaction):
        # reaproveita o ticket de pagamentos (modal, limite por pessoa, staff, tudo igual)
        try:
            from . import tickets as tk
            existe = TICKET_CATEGORIA in tk.get_cfg(interaction.guild.id)["categorias"]
        except Exception as e:
            log.warning("ticket indisponivel pro botao vip: %s", e)
            existe = False
        if not existe:
            await interaction.response.send_message(
                view=Card("Ticket indisponível", "Chame a equipe pra adquirir o seu VIP."),
                ephemeral=True,
            )
            return
        await tk._start(interaction, TICKET_CATEGORIA)


def register_views(client):
    # o menu e o botao dos paineis ja postados continuam funcionando apos reiniciar
    painel = discord.ui.LayoutView(timeout=None)
    painel.add_item(discord.ui.ActionRow(VipSelect()))
    painel.add_item(discord.ui.ActionRow(ComprarBotao()))
    client.add_view(painel)


# ---------------------------------------------------------------- painéis postados

def _ler_estado() -> dict:
    try:
        dados = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(dados, dict) and isinstance(dados.get("paineis"), list):
            return dados
    except (OSError, ValueError):
        pass
    return {"paineis": []}


def _salvar_estado(dados: dict):
    try:
        STATE_FILE.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("nao consegui salvar %s: %s", STATE_FILE, e)


async def post_panel(channel: discord.abc.Messageable) -> discord.Message:
    view, arquivos = build_panel_view(channel.guild)
    msg = await channel.send(view=view, files=arquivos, allowed_mentions=NO_MENTIONS)
    dados = _ler_estado()
    dados["paineis"].append([channel.guild.id, channel.id, msg.id])
    _salvar_estado(dados)
    return msg


async def refresh_panels(guild: discord.Guild) -> int:
    # reedita os paineis ja postados desse servidor
    dados = _ler_estado()
    ok, mantidos = 0, []
    for gid, cid, mid in dados["paineis"]:
        if gid != guild.id:
            mantidos.append([gid, cid, mid])
            continue
        ch = guild.get_channel_or_thread(cid)
        if ch is None:
            continue
        try:
            view, arquivos = build_panel_view(guild)
            await ch.get_partial_message(mid).edit(view=view, attachments=arquivos)
            ok += 1
            mantidos.append([gid, cid, mid])
        except discord.NotFound:
            continue   # painel apagado
        except discord.HTTPException as e:
            log.warning("nao consegui atualizar o painel vip %s: %s", mid, e)
            mantidos.append([gid, cid, mid])
    if mantidos != dados["paineis"]:
        dados["paineis"] = mantidos
        _salvar_estado(dados)
    return ok


# ---------------------------------------------------------------- cog

async def _aviso(ctx, titulo: str, texto: str = "", ok: bool = True, tempo: int = 12):
    await ctx.send(
        view=Card(f"{'✅' if ok else '⚠️'} {titulo}", texto),
        delete_after=tempo, allowed_mentions=NO_MENTIONS,
    )


async def _pegar_anexo(ctx) -> Optional[discord.Attachment]:
    # imagem anexada no comando, ou na mensagem que o comando respondeu
    if ctx.message is None:
        return None
    if ctx.message.attachments:
        return ctx.message.attachments[0]
    ref = ctx.message.reference
    if ref is None:
        return None
    msg = ref.resolved if isinstance(ref.resolved, discord.Message) else None
    if msg is None and ref.message_id:
        try:
            msg = await ctx.channel.fetch_message(ref.message_id)
        except discord.HTTPException:
            msg = None
    if msg is not None and msg.attachments:
        return msg.attachments[0]
    return None


# ---------------------------------------------------------------- painel de config (admin)

def _p(gid) -> str:
    return "/" if get_modo_slash(gid) else ","


def _texto_ajuda() -> str:
    return (
        "## Mudar os textos do painel VIP\n"
        "**Painel**\n"
        "`,pvipconfig texto <campo> <texto novo>`\n"
        "Campos: `titulo` · `descricao` · `info` · `menu` (menu fechado) · `botao` · `permissoes` (título da lista no cartão)\n"
        "Ex.: `,pvipconfig texto titulo Seja VIP ✨`\n"
        "Use `{pix}` na `info` pro emoji do Pix · `,pvipconfig texto info nenhum` tira a linha\n\n"
        "**Cada VIP**\n"
        "`,pvipconfig vip <vip> <campo> <valor>`\n"
        "Campos: `nome` · `descricao` · `resumo` (frase do menu) · `perms` · `emoji` · `cargo`\n"
        "`perms`: uma por linha (ou separadas por `|`)\n"
        "Ex.: `,pvipconfig vip elite perms Enviar imagens | Usar emojis externos`\n"
        "Ex.: `,pvipconfig vip elite emoji poyo_pix` · `,pvipconfig vip elite cargo @Elite`\n\n"
        "**Imagens**\n"
        "`,pvipconfig topo` + anexo (ou `topo <link>`) · `fim` · `imagem <vip>`\n"
        "`topo nenhum` tira a imagem · `topo padrao` volta ao original\n"
        "-# no painel (menu 📎) o bot pede a imagem no chat, igual o ticket\n\n"
        "Sem o valor, mostra o atual. Com `padrao` no lugar, volta ao original.\n"
        "Tudo atualiza os painéis já postados na hora."
    )


def _texto_ver(gid) -> str:
    c = get_cfg(gid)
    corta = lambda t, n=160: (t[: n - 1] + "…") if len(t) > n else t
    linhas = [f"{MARCA} **{k}:** {corta(str(c[k]) or '(vazio)')}" for k in LIM_PAINEL]
    for key, v in c["vips"].items():
        perms = " | ".join(v.get("perms") or []) or "(vazio)"
        linhas.append(
            f"\n{_vip_emoji(v)} **{key}**\n"
            f"nome: {v['nome']} · emoji: {v.get('emoji') or '-'} · cargo: {v.get('cargo') or '-'}\n"
            f"resumo: {corta(v.get('resumo') or '(vazio)')}\n"
            f"descrição: {corta(v.get('descricao') or '(vazio)')}\n"
            f"perms: {corta(perms)}"
        )
    return ("## Textos do painel VIP\n" + "\n".join(linhas))[:3900]


def _texto_imagens(gid) -> str:
    c = get_cfg(gid)
    linhas = [
        f"{MARCA} **Topo do painel** · {_status_img(_get_img(gid, 'topo'), 'sem imagem')}",
        f"{MARCA} **Final do painel** · {_status_img(_get_img(gid, 'fim'), 'gif de lacinhos')}",
    ]
    for key, v in c["vips"].items():
        linhas.append(f"{MARCA} **{v['nome']}** · {_status_img(_get_img(gid, f'card_{key}'), 'usa a do topo')}")
    return "\n".join(linhas)


def _no_modo(texto: str, gid) -> str:
    # os exemplos aparecem com "/" ou "," conforme o modo do servidor
    return texto.replace(",pvip", f"{_p(gid)}pvip")


def _rotulo_img(gid, chave: str) -> str:
    if chave == "topo":
        return "o topo do painel"
    if chave == "fim":
        return "o final do painel"
    nome = get_cfg(gid)["vips"].get(chave.removeprefix("card_"), {}).get("nome", chave)
    return f"o cartão do {nome}"


class PainelConfig(discord.ui.LayoutView):
    """painel de config: modo slash/prefixo, imagens, textos (so admin, so quem abriu)"""

    def __init__(self, guild: discord.Guild, author_id: int):
        super().__init__(timeout=600)
        self.guild, self.author_id = guild, author_id
        self.sent, self.notice = None, ""
        self.render()

    def render(self):
        self.clear_items()
        gid = self.guild.id
        modo = "Slash (`/`)" if get_modo_slash(gid) else "Prefixo (`,`)"
        corpo = (
            "## Configurar painel VIP\n"
            f"-# Modo de comandos: **{modo}**\n\n"
            f"**Imagens**\n{_texto_imagens(gid)}\n"
            "-# escolha 📎 no menu e mande a imagem no chat\n\n"
            f"-# Textos: `{_p(gid)}pvipconfig ajuda` · atuais: `{_p(gid)}pvipconfig ver`"
        )
        if self.notice:
            corpo += f"\n\n{self.notice}"
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(corpo)))
        self.add_item(discord.ui.ActionRow(PainelSelect(self)))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                view=Card("Esse painel é de outra pessoa", "Abra o seu com `,pvipconfig`."), ephemeral=True
            )
            return False
        perms = getattr(interaction.user, "guild_permissions", None)
        if not (perms is not None and perms.administrator) and not tem_acesso_total(interaction.user):
            await interaction.response.send_message(
                view=Card("Sem permissão", "Só administradores mexem aqui."), ephemeral=True
            )
            return False
        return True


class PainelSelect(discord.ui.Select):
    def __init__(self, painel: PainelConfig):
        self.painel = painel
        gid = painel.guild.id
        slash = get_modo_slash(gid)
        opcoes = [
            discord.SelectOption(
                label="Modo Slash", value="modo_slash", emoji="✅" if slash else "⬜",
                description="Usar comandos / em vez de comandos com ,",
            ),
            discord.SelectOption(label="Imagem do topo", value="img_topo", emoji="📎",
                                 description="Banner de cima do painel"),
            discord.SelectOption(label="Imagem do final", value="img_fim", emoji="📎",
                                 description="Decoração de baixo (padrão: lacinhos)"),
        ]
        for key, v in get_cfg(gid)["vips"].items():
            opcoes.append(discord.SelectOption(
                label=f"Imagem do {v['nome']}"[:100], value=f"img_card_{key}", emoji="📎",
                description="Banner só do cartão desse VIP",
            ))
        opcoes += [
            discord.SelectOption(label="Textos atuais", value="ver", emoji="📝",
                                 description="Ver tudo que está valendo agora"),
            discord.SelectOption(label="Como mudar os textos", value="ajuda", emoji="💬",
                                 description="Comandos pra editar o painel e cada VIP"),
            discord.SelectOption(label="Atualizar painéis", value="atualizar", emoji="🔄",
                                 description="Reedita os painéis já postados"),
        ]
        super().__init__(placeholder="Toque em uma opção…", options=opcoes[:25])

    async def callback(self, interaction: discord.Interaction):
        escolha, guild = self.values[0], interaction.guild
        if escolha == "modo_slash":
            novo = not get_modo_slash(guild.id)
            set_modo_slash(guild.id, novo)
            self.painel.render()
            await interaction.response.edit_message(view=self.painel)
            if novo:
                msg = ("Agora os comandos do painel VIP são só `/pvip` e `/pvipconfig`.\n"
                       "-# O `,pvipconfig` continua abrindo este painel, pra ninguém ficar trancado fora.")
            else:
                msg = "Agora os comandos com `,` funcionam (o slash continua funcionando também)."
            await interaction.followup.send(
                view=Card(f"Modo Slash {'ligado' if novo else 'desligado'}", msg), ephemeral=True
            )
            return
        if escolha.startswith("img_"):
            await self._upload(interaction, escolha[4:])
            return
        if escolha == "atualizar":
            n = await refresh_panels(guild)
            await interaction.response.send_message(
                view=Card("Painéis atualizados", f"-# {n} painel(is) atualizado(s)."), ephemeral=True
            )
            return
        texto = _texto_ver(guild.id) if escolha == "ver" else _texto_ajuda()
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(discord.ui.TextDisplay(_no_modo(texto, guild.id))))
        await interaction.response.send_message(view=view, ephemeral=True, allowed_mentions=NO_MENTIONS)

    async def _upload(self, interaction: discord.Interaction, chave: str):
        # igual o 📎 do ticket: o admin manda a imagem no chat e o bot guarda
        guild = interaction.guild
        if interaction.channel is None:
            await interaction.response.send_message(
                view=Card("Não consegui ver este canal", "Tente de novo."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            view=Card(
                "📎 Mande a imagem agora",
                f"Envie **neste canal** uma imagem ou gif (png, jpg, gif, webp) pra {_rotulo_img(guild.id, chave)} "
                "nos próximos 60 segundos.\n-# Pra cancelar é só esperar.",
            ),
            ephemeral=True,
        )

        def ok(m):
            return (
                m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
                and any(Path(a.filename).suffix.lower() in IMG_EXTS for a in m.attachments)
            )

        try:
            msg = await interaction.client.wait_for("message", check=ok, timeout=60)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                view=Card("⏱️ Tempo esgotado", "Escolha a opção 📎 de novo."), ephemeral=True
            )
            return
        anexo = next(a for a in msg.attachments if Path(a.filename).suffix.lower() in IMG_EXTS)
        erro, _ = await aplicar_imagem(guild.id, chave, anexo)
        if erro:
            await interaction.followup.send(view=Card("⚠️ Não salvei", erro), ephemeral=True)
            return
        try:                      # a mensagem com a imagem some do chat (a copia fica guardada no bot)
            await msg.delete()
        except discord.HTTPException:
            pass
        n = await refresh_panels(guild)
        self.painel.notice = f"✅ Imagem salva · {n} painel(is) atualizado(s)" if n else "✅ Imagem salva"
        self.painel.render()
        try:
            await self.painel.sent.edit(view=self.painel)
        except (discord.HTTPException, AttributeError):
            pass


# ---------------------------------------------------------------- cog

class Vip(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        register_views(self.bot)
        self._ler_emojis()      # se o bot ja estiver ligado (reload da cog)

    def _ler_emojis(self):
        # guarda os emojis poyo_* de todos os servidores onde o bot esta
        achados = {}
        for e in self.bot.emojis:
            if e.name.startswith("poyo_") and e.available:
                achados.setdefault(e.name, str(e))
        EMOJIS.clear()
        EMOJIS.update(achados)

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
        # modo slash: comandos com "," sao ignorados (o painel ,pvipconfig continua
        # abrindo pra ninguem ficar trancado fora)
        if (
            get_modo_slash(ctx.guild.id)
            and ctx.interaction is None
            and ctx.command is not None
            and ctx.command.qualified_name != "pvipconfig"
        ):
            raise commands.CheckFailure()
        return True

    # ------------------------------------------------------------ painel

    @commands.hybrid_group(
        name="pvip",
        fallback="postar",
        description="Posta o painel VIP no canal atual.",
        extras={
            "categoria": "Utilidades",
            "uso": ",pvip",
            "descricao": "Posta o painel VIP no canal atual (`,pvip atualizar` reedita os já postados).",
        },
    )
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(send_messages=True, attach_files=True)
    async def pvip(self, ctx):
        await post_panel(ctx.channel)
        if ctx.interaction is not None:     # o slash precisa de uma resposta
            await _aviso(ctx, "Painel VIP postado", tempo=6)

    @pvip.command(name="atualizar", aliases=("refresh",), description="Reedita os painéis VIP já postados.")
    async def pvip_atualizar(self, ctx):
        n = await refresh_panels(ctx.guild)
        await _aviso(ctx, "Painel VIP atualizado", f"-# {n} painel(is) atualizado(s).")

    # ------------------------------------------------------------ config

    @commands.hybrid_group(
        name="pvipconfig",
        aliases=("pvc",),
        fallback="abrir",
        description="Abre o painel de configuração do painel VIP.",
        extras={
            "categoria": "Utilidades",
            "uso": ",pvipconfig",
            "descricao": "Painel de config do VIP: modo Slash/`,`, imagens e textos.",
        },
    )
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def pvipconfig(self, ctx):
        view = PainelConfig(ctx.guild, ctx.author.id)
        view.sent = await ctx.send(view=view, allowed_mentions=NO_MENTIONS)

    # ---- imagens

    async def _definir_imagem(self, ctx, chave: str, rotulo: str, acao: Optional[str], arquivo):
        if arquivo is None and not acao and ctx.interaction is None:
            arquivo = await _pegar_anexo(ctx)     # responder uma imagem tambem vale
        if arquivo is None and not (acao or "").strip():
            await _aviso(
                ctx, f"Faltou a imagem de {rotulo}",
                "Anexe a imagem (ou responda uma), mande um link, ou use `nenhum` / `padrao`.", ok=False,
            )
            return
        erro, ok = await aplicar_imagem(ctx.guild.id, chave, arquivo, acao or "")
        if erro:
            await _aviso(ctx, f"Não deu pra usar em {rotulo}", erro, ok=False)
            return
        n = await refresh_panels(ctx.guild)
        await _aviso(ctx, f"{rotulo.capitalize()}: {ok}", f"-# {n} painel(is) atualizado(s).")

    @pvipconfig.command(name="topo", aliases=("banner",), description="Define a imagem do topo do painel (anexo, link, nenhum ou padrao).")
    @app_commands.describe(arquivo="A imagem ou gif", acao="Um link, nenhum (sem imagem) ou padrao")
    async def cfg_topo(self, ctx, acao: Optional[str] = None, arquivo: Optional[discord.Attachment] = None):
        await self._definir_imagem(ctx, "topo", "topo do painel", acao, arquivo)

    @pvipconfig.command(name="fim", aliases=("final", "gif"), description="Define a imagem de baixo do painel (anexo, link, nenhum ou padrao).")
    @app_commands.describe(arquivo="A imagem ou gif", acao="Um link, nenhum (sem imagem) ou padrao (lacinhos)")
    async def cfg_fim(self, ctx, acao: Optional[str] = None, arquivo: Optional[discord.Attachment] = None):
        await self._definir_imagem(ctx, "fim", "final do painel", acao, arquivo)

    @pvipconfig.command(name="imagem", aliases=("img",), description="Define a imagem do cartão de um VIP (anexo, link, nenhum ou padrao).")
    @app_commands.describe(vip="Qual VIP", arquivo="A imagem ou gif", acao="Um link, nenhum (sem imagem) ou padrao (usa a do topo)")
    @app_commands.choices(vip=[app_commands.Choice(name=k, value=k) for k in VIPS])
    async def cfg_imagem(self, ctx, vip: str, acao: Optional[str] = None, arquivo: Optional[discord.Attachment] = None):
        vips = get_cfg(ctx.guild.id)["vips"]
        key = _achar_vip(vip, vips)
        if key is None:
            await _aviso(ctx, "VIP não encontrado", "Use um destes: " + ", ".join(f"`{k}`" for k in vips), ok=False)
            return
        await self._definir_imagem(ctx, f"card_{key}", f"cartão do {vips[key]['nome']}", acao, arquivo)

    # ---- textos

    @pvipconfig.command(name="ajuda", aliases=("help",), description="Mostra como mudar os textos do painel e dos VIPs.")
    async def cfg_ajuda(self, ctx):
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(discord.ui.TextDisplay(_no_modo(_texto_ajuda(), ctx.guild.id))))
        await ctx.send(view=view)

    @pvipconfig.command(name="texto", aliases=("txt",), description="Muda um texto do painel (título, descrição, botão...).")
    @app_commands.describe(campo="Qual texto", valor="Texto novo (vazio mostra o atual, padrao volta ao original)")
    @app_commands.choices(campo=[app_commands.Choice(name=k, value=k) for k in LIM_PAINEL])
    async def cfg_texto(self, ctx, campo: str = "", *, valor: str = ""):
        c = get_cfg(ctx.guild.id)
        campo_n = _norm(campo)
        if campo_n in LIM_PAINEL and not valor.strip():
            await _aviso(ctx, f"Texto atual de {campo_n}", f"```\n{c[campo_n] or '(vazio)'}\n```", tempo=30)
            return
        erro, ok = aplicar_painel(ctx.guild.id, campo, valor)
        if erro:
            await _aviso(ctx, "Não deu", erro + "\nEx.: `,pvipconfig texto titulo Seja VIP ✨`", ok=False)
            return
        n = await refresh_panels(ctx.guild)
        await _aviso(ctx, ok.capitalize(), f"-# {n} painel(is) atualizado(s).")

    @pvipconfig.command(name="vip", aliases=("editar",), description="Muda nome, descrição, permissões, emoji ou cargo de um VIP.")
    @app_commands.describe(vip="Qual VIP", campo="O que mudar", valor="Valor novo (vazio mostra o atual, padrao volta ao original)")
    @app_commands.choices(
        vip=[app_commands.Choice(name=k, value=k) for k in VIPS],
        campo=[app_commands.Choice(name=k, value=k) for k in LIM_VIP],
    )
    async def cfg_vip(self, ctx, vip: str = "", campo: str = "", *, valor: str = ""):
        vips = get_cfg(ctx.guild.id)["vips"]
        key = _achar_vip(vip, vips)
        if key is None:
            await _aviso(ctx, "VIP não encontrado", "Use um destes: " + ", ".join(f"`{k}`" for k in vips), ok=False)
            return
        campo_n = _norm(campo)
        if campo_n in LIM_VIP and not valor.strip():
            atual = vips[key].get(campo_n, "")
            if isinstance(atual, list):
                atual = "\n".join(atual)
            await _aviso(ctx, f"{campo_n} de {vips[key]['nome']}", f"```\n{atual or '(vazio)'}\n```", tempo=30)
            return
        erro, ok = aplicar_vip(ctx.guild.id, key, campo, valor)
        if erro:
            await _aviso(ctx, "Não deu", erro, ok=False)
            return
        aviso = ""
        if campo_n == "emoji":
            e = get_cfg(ctx.guild.id)["vips"][key].get("emoji", "")
            if e.startswith("poyo_") and not _achar_emoji(e):
                aviso = "\n-# Esse emoji não está em nenhum servidor do bot, então aparece o símbolo no lugar."
        n = await refresh_panels(ctx.guild)
        await _aviso(ctx, ok[:1].upper() + ok[1:], f"-# {n} painel(is) atualizado(s).{aviso}")

    @pvipconfig.command(name="ver", aliases=("mostrar", "textos"), description="Mostra todos os textos que estão valendo agora.")
    async def cfg_ver(self, ctx):
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(discord.ui.TextDisplay(_texto_ver(ctx.guild.id))))
        await ctx.send(view=view, allowed_mentions=NO_MENTIONS)


async def setup(bot):
    await bot.add_cog(Vip(bot))
