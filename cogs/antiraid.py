# cog anti-raid com components v2 - configuravel por servidor (painel igual ao do ,ptconfig)
#
# o que faz:
#   - conta quantos canais cada pessoa/bot criou e quantos @everyone/@here mandou numa janela de tempo
#   - se passar do limite (padrao: 3 canais e/ou 3 @everyone em 10s) considera raid e pune sozinho:
#       bot    -> tira os cargos e BANE
#       membro -> tira os cargos e deixa de castigo (timeout) por quase 28 dias
#   - quem foi punido fica TRANCADO: se alguem devolver cargo, o Poyo tira de novo
#   - so sai do trancamento com a SENHA do anti-raid:  ,antiraid liberar <@pessoa ou id>
#     (a senha e definida no painel e fica guardada so como hash, nunca em texto)
#   - avisa no canal de logs e (opcional) apaga os canais e mensagens criados no raid
#
# precisa das permissoes: Gerenciar cargos, Banir membros, Ver registro de auditoria e
# Castigar membros (e Gerenciar canais/mensagens se a limpeza estiver ligada). o cargo do
# Poyo tem que ficar ACIMA dos cargos que ele vai tirar.
#
# arquivo novo: nao mexe em nenhum cog existente. o bot.py carrega sozinho (cogs/*.py)
import os
import re
import copy
import json
import time
import hmac
import asyncio
import hashlib
import secrets
import logging
from collections import deque
from datetime import timedelta
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

try:
    from ._acesso import tem_acesso_total   # cargo com acesso a todos os comandos
except ImportError:
    def tem_acesso_total(membro) -> bool:
        return False

log = logging.getLogger("bot.antiraid")

# evita pingar sem querer
NO_MENTIONS = discord.AllowedMentions.none()
CONFIG_FILE = Path(os.getenv("ANTIRAID_CONFIG_FILE", "antiraid_config.json"))

EVERYONE_RE = re.compile(r"@(?:everyone|here)\b", re.IGNORECASE)

# depois de punir alguem, ignora essa pessoa por um tempo (evita punir/logar em loop)
COOLDOWN_POS_PUNICAO = 60
# trava de seguranca da limpeza (nunca apaga mais que isso por raid)
MAX_LIMPEZA = 50


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


# ---------------------------------------------------------------- config

DEFAULTS = {
    "ativo": True,              # liga/desliga a protecao
    "janela": 10,               # segundos que contam pro raid
    "limite_canais": 3,         # canais criados na janela (0 = nao conta canais)
    "limite_everyone": 3,       # mensagens com @everyone/@here na janela (0 = nao conta)
    "gatilho": "qualquer",      # qualquer = um dos dois ja basta | ambos = precisa dos dois juntos
    "limpar": True,             # apaga os canais e as mensagens que o raid criou
    "log": None,                # id do canal onde o aviso do raid cai
    "avisar_usuarios": [],      # IDs que recebem aviso por DM quando o raid dispara
    "ignorar_usuarios": [],     # lista branca: pessoas/bots que nunca sao punidos
    "ignorar_cargos": [],       # lista branca: quem tem esses cargos nunca e punido
    "senha": None,              # {"salt": hex, "hash": hex} - nunca guarda a senha em texto
    "bloqueio_global": None,     # registro persistente do bloqueio global
}

# castigo maximo do discord e 28 dias; fica um pouco abaixo pra nao ser recusado
TIMEOUT_TRANCA = timedelta(days=27, hours=23)
SENHA_MIN = 6
SENHA_ITER = 200_000
MAX_TENTATIVAS = 5          # erros de senha seguidos...
BLOQUEIO_SENHA = 300        # ...e a pessoa fica esse tanto de segundos sem poder tentar

_state = _read_json(CONFIG_FILE)
_tentativas: dict = {}      # (guild_id, user_id) -> (erros, bloqueado_ate)


def get_cfg(guild_id) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    over = _state.get("guilds", {}).get(str(guild_id), {})
    for k, v in over.items():
        if k in cfg:
            cfg[k] = v
    return cfg


def set_cfg(guild_id, key, value):
    _state.setdefault("guilds", {}).setdefault(str(guild_id), {})[key] = value
    _write_json(CONFIG_FILE, _state)


def bloqueio_global(guild_id) -> Optional[dict]:
    """Retorna o bloqueio global persistente, se o servidor estiver travado."""
    valor = get_cfg(guild_id).get("bloqueio_global")
    return valor if isinstance(valor, dict) and valor.get("ativo") else None


def ativar_bloqueio_global(guild_id, motivo: str, user_id: int):
    set_cfg(guild_id, "bloqueio_global", {
        "ativo": True,
        "em": int(time.time()),
        "user_id": int(user_id),
        "motivo": motivo[:500],
    })


def desativar_bloqueio_global(guild_id):
    set_cfg(guild_id, "bloqueio_global", None)


# ---------------------------------------------------------------- senha

def _hash_senha(senha: str) -> dict:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, SENHA_ITER)
    return {"salt": salt.hex(), "hash": h.hex()}


def _confere_senha(guild_id, senha: str) -> bool:
    # roda em thread (pbkdf2 e pesado) - nao chama direto dentro de async
    reg = get_cfg(guild_id)["senha"]
    if not reg:
        return False
    try:
        h = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(reg["salt"]), SENHA_ITER)
        return hmac.compare_digest(h.hex(), reg["hash"])
    except (ValueError, KeyError, TypeError):
        return False


def _espera_senha(key) -> int:
    """segundos que a pessoa ainda precisa esperar pra tentar de novo (0 = pode tentar)"""
    t = _tentativas.get(key)
    if not t:
        return 0
    _, ate = t
    if ate and time.monotonic() < ate:
        return int(ate - time.monotonic()) + 1
    if ate:
        _tentativas.pop(key, None)
    return 0


def _errou_senha(key):
    n, _ = _tentativas.get(key, (0, 0))
    n += 1
    _tentativas[key] = (0, time.monotonic() + BLOQUEIO_SENHA) if n >= MAX_TENTATIVAS else (n, 0)


# ---------------------------------------------------------------- trancados (quem foi punido)

def get_trancado(guild_id, user_id) -> Optional[dict]:
    return _state.get("trancados", {}).get(str(guild_id), {}).get(str(user_id))


def set_trancado(guild_id, user_id, dados: dict):
    _state.setdefault("trancados", {}).setdefault(str(guild_id), {})[str(user_id)] = dados
    _write_json(CONFIG_FILE, _state)


def del_trancado(guild_id, user_id):
    if _state.get("trancados", {}).get(str(guild_id), {}).pop(str(user_id), None) is not None:
        _write_json(CONFIG_FILE, _state)


def lista_trancados(guild_id) -> list:
    return [int(u) for u in _state.get("trancados", {}).get(str(guild_id), {})]


# ---------------------------------------------------------------- visual (mesmo padrao do tickets.py)

# cartao simples (container puro sem barra colorida)
class Card(discord.ui.LayoutView):
    def __init__(self, title: str, text: str = "", *extra):
        super().__init__(timeout=None)
        body = f"### {title}" + (f"\n{text}" if text else "")
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(body), *extra))


sep = discord.ui.Separator   # atalho pq eu uso mt


def _sec(titulo, sub=""):
    # titulo de cada parte da tela
    return discord.ui.TextDisplay(f"### {titulo}" + (f"\n-# {sub}" if sub else ""))


async def _say(ctx, title, text="", ok=True, keep=False):
    await ctx.send(view=Card(("✅ " if ok else "⚠️ ") + title, text),
                   delete_after=None if keep else 8, allowed_mentions=NO_MENTIONS)


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


class _RoleSel(_Fn, discord.ui.RoleSelect):
    pass


class _UserSel(_Fn, discord.ui.UserSelect):
    pass


class _ChanSel(_Fn, discord.ui.ChannelSelect):
    pass


def _admin(membro) -> bool:
    perms = getattr(membro, "guild_permissions", None)
    return bool(perms is not None and perms.administrator) or tem_acesso_total(membro)


def _lista(ids, mencao) -> str:
    if not ids:
        return "ninguém"
    return " ".join(mencao.format(i) for i in ids)


def _permissoes_faltando(guild: discord.Guild, limpar: bool) -> list:
    me = guild.me
    if me is None:
        return []
    p = me.guild_permissions
    checagens = [
        ("Gerenciar cargos", p.manage_roles),
        ("Banir membros", p.ban_members),
        ("Ver registro de auditoria", p.view_audit_log),
        ("Castigar membros", p.moderate_members),
    ]
    if limpar:
        checagens += [("Gerenciar canais", p.manage_channels), ("Gerenciar mensagens", p.manage_messages)]
    return [nome for nome, ok in checagens if not ok]


# ---------------------------------------------------------------- janela de limites

class LimitesModal(discord.ui.Modal):
    def __init__(self, painel):
        super().__init__(title="⏱️ Limites do anti-raid")
        self.painel = painel
        cfg = get_cfg(painel.guild.id)
        self.janela = discord.ui.TextInput(
            label="Janela em segundos (3 a 300)", default=str(cfg["janela"]),
            max_length=3, required=True,
        )
        self.canais = discord.ui.TextInput(
            label="Canais criados pra contar como raid (0 = ignora)", default=str(cfg["limite_canais"]),
            max_length=2, required=True,
        )
        self.everyone = discord.ui.TextInput(
            label="Mensagens com @everyone (0 = ignora)", default=str(cfg["limite_everyone"]),
            max_length=2, required=True,
        )
        for campo in (self.janela, self.canais, self.everyone):
            self.add_item(campo)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            janela = int(str(self.janela.value).strip())
            canais = int(str(self.canais.value).strip())
            everyone = int(str(self.everyone.value).strip())
        except ValueError:
            await _erro_card(interaction, "Não salvei", "Use só números inteiros nos três campos.")
            return
        if not 3 <= janela <= 300:
            await _erro_card(interaction, "Não salvei", "A janela precisa ficar entre 3 e 300 segundos.")
            return
        if not (0 <= canais <= 50 and 0 <= everyone <= 50):
            await _erro_card(interaction, "Não salvei", "Os limites vão de 0 a 50.")
            return
        if canais == 0 and everyone == 0:
            await _erro_card(interaction, "Não salvei", "Pelo menos um dos limites precisa ser maior que 0.")
            return
        gid = self.painel.guild.id
        set_cfg(gid, "janela", janela)
        set_cfg(gid, "limite_canais", canais)
        set_cfg(gid, "limite_everyone", everyone)
        await self.painel.update(interaction, "✅ Salvo")


# ---------------------------------------------------------------- senha: definir / trocar

class SenhaModal(discord.ui.Modal):
    def __init__(self, painel):
        super().__init__(title="🔐 Senha do anti-raid")
        self.painel = painel
        self.tem = bool(get_cfg(painel.guild.id)["senha"])
        self.atual = None
        if self.tem:
            self.atual = discord.ui.TextInput(label="Senha atual", max_length=64, required=True)
            self.add_item(self.atual)
        self.nova = discord.ui.TextInput(
            label=f"Nova senha (mínimo {SENHA_MIN} caracteres)", min_length=SENHA_MIN,
            max_length=64, required=True,
        )
        self.confirma = discord.ui.TextInput(label="Repita a nova senha", max_length=64, required=True)
        self.add_item(self.nova)
        self.add_item(self.confirma)

    async def on_submit(self, interaction: discord.Interaction):
        gid = self.painel.guild.id
        key = (gid, interaction.user.id)
        espera = _espera_senha(key)
        if espera:
            await _erro_card(interaction, "Calma", f"Muitas tentativas erradas. Espere {espera}s.")
            return
        nova, confirma = str(self.nova.value), str(self.confirma.value)
        if nova != confirma:
            await _erro_card(interaction, "Não salvei", "As duas senhas novas não batem.")
            return
        if self.tem:
            ok = await asyncio.to_thread(_confere_senha, gid, str(self.atual.value))
            if not ok:
                _errou_senha(key)
                await _erro_card(interaction, "Não salvei", "Senha atual errada.")
                return
            _tentativas.pop(key, None)
        reg = await asyncio.to_thread(_hash_senha, nova)
        set_cfg(gid, "senha", reg)
        log.warning("senha do anti-raid %s em %s por %s", "trocada" if self.tem else "definida", gid, interaction.user.id)
        await self.painel.update(interaction, "🔐 Senha trocada" if self.tem else "🔐 Senha definida")


# ---------------------------------------------------------------- liberar quem foi trancado

async def _liberar(guild: discord.Guild, uid: int, quem) -> list:
    """desfaz o trancamento (castigo, ban de bot e cargos) e devolve as linhas do resultado"""
    dados = get_trancado(guild.id, uid) or {}
    # apaga o registro ANTES: senao a vigia re-tira os cargos que eu acabei de devolver
    del_trancado(guild.id, uid)
    razao = f"Anti-raid: liberado por {quem}"[:480]
    linhas = []

    if dados.get("bot"):
        try:
            await guild.unban(discord.Object(id=uid), reason=razao)
            linhas.append("✅ bot desbanido (precisa ser adicionado de novo; os cargos não voltam sozinhos)")
        except discord.NotFound:
            linhas.append("ℹ️ o bot não estava banido")
        except discord.HTTPException as e:
            linhas.append(f"⚠️ erro ao desbanir ({e.status})")
        return linhas

    membro = guild.get_member(uid)
    if membro is None:
        try:
            membro = await guild.fetch_member(uid)
        except discord.HTTPException:
            membro = None
    if membro is None:
        return ["ℹ️ a pessoa não está no servidor; nada pra devolver"]

    try:
        await membro.timeout(None, reason=razao)
        linhas.append("✅ castigo removido")
    except discord.HTTPException as e:
        linhas.append(f"⚠️ não consegui tirar o castigo ({e.status})")

    voltar = [r for r in (guild.get_role(i) for i in dados.get("cargos", [])) if r is not None and r.is_assignable()]
    if voltar:
        try:
            await membro.add_roles(*voltar, reason=razao)
            linhas.append(f"✅ {len(voltar)} cargo(s) devolvido(s)")
        except discord.HTTPException as e:
            linhas.append(f"⚠️ não consegui devolver os cargos ({e.status})")
    else:
        linhas.append("ℹ️ nenhum cargo pra devolver")
    return linhas


class LiberarModal(discord.ui.Modal):
    def __init__(self, tela):
        super().__init__(title="🔓 Liberar com senha")
        self.tela = tela
        self.senha = discord.ui.TextInput(label="Senha do anti-raid", max_length=64, required=True)
        self.add_item(self.senha)

    async def on_submit(self, interaction: discord.Interaction):
        guild, uid = self.tela.guild, self.tela.uid
        key = (guild.id, interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        espera = _espera_senha(key)
        if espera:
            await _erro_card(interaction, "Calma", f"Muitas tentativas erradas. Espere {espera}s.")
            return
        if get_trancado(guild.id, uid) is None:
            await _erro_card(interaction, "Já liberado", "Essa pessoa não está mais trancada.")
            return
        ok = await asyncio.to_thread(_confere_senha, guild.id, str(self.senha.value))
        if not ok:
            _errou_senha(key)
            await _erro_card(interaction, "Senha errada", "Não liberei.")
            return
        _tentativas.pop(key, None)
        linhas = await _liberar(guild, uid, interaction.user)
        log.warning("anti-raid: %s liberou %s em %s | %s", interaction.user.id, uid, guild.id, "; ".join(linhas))
        await interaction.followup.send(
            view=Card("🔓 Liberado", f"<@{uid}>\n" + "\n".join(linhas)),
            ephemeral=True, allowed_mentions=NO_MENTIONS,
        )


class LiberarGlobalModal(discord.ui.Modal):
    def __init__(self, guild: discord.Guild):
        super().__init__(title="🔓 Desbloquear o bot inteiro")
        self.guild = guild
        self.senha = discord.ui.TextInput(label="Senha do anti-raid", max_length=64, required=True)
        self.add_item(self.senha)

    async def on_submit(self, interaction: discord.Interaction):
        key = (self.guild.id, interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        espera = _espera_senha(key)
        if espera:
            await _erro_card(interaction, "Calma", f"Muitas tentativas erradas. Espere {espera}s.")
            return
        if bloqueio_global(self.guild.id) is None:
            await _erro_card(interaction, "Já desbloqueado", "O bloqueio global não está ativo.")
            return
        ok = await asyncio.to_thread(_confere_senha, self.guild.id, str(self.senha.value))
        if not ok:
            _errou_senha(key)
            await _erro_card(interaction, "Senha errada", "O bot continua bloqueado.")
            return
        _tentativas.pop(key, None)
        desativar_bloqueio_global(self.guild.id)
        log.warning("anti-raid: bloqueio global liberado por %s em %s", interaction.user.id, self.guild.id)
        await interaction.followup.send(
            view=Card("🔓 Bot desbloqueado", "A senha foi aceita e os comandos voltaram a funcionar."),
            ephemeral=True, allowed_mentions=NO_MENTIONS,
        )


class LiberarView(discord.ui.LayoutView):
    def __init__(self, author_id: int, guild: discord.Guild, uid: int):
        super().__init__(timeout=300)
        self.author_id, self.guild, self.uid = author_id, guild, uid
        dados = get_trancado(guild.id, uid) or {}
        tipo = "bot (banido)" if dados.get("bot") else "membro (sem cargos e de castigo)"
        btn = _Btn(self._abrir, label="Digitar senha", style=discord.ButtonStyle.danger, emoji="🔓")
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                f"### 🔒 Liberar <@{uid}>\n"
                f"**Tipo**  {tipo}\n"
                "-# só sai do trancamento com a senha do anti-raid"
            ),
            discord.ui.ActionRow(btn),
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await _erro_card(interaction, "Essa tela é de outra pessoa", "Use `,antiraid liberar` pra abrir a sua.")
            return False
        if not _admin(interaction.user):
            await _erro_card(interaction, "Sem permissão", "Só administradores liberam.")
            return False
        return True

    async def _abrir(self, interaction, item):
        await interaction.response.send_modal(LiberarModal(self))


class _GlobalUnlockView(discord.ui.LayoutView):
    def __init__(self, author_id: int, guild: discord.Guild):
        super().__init__(timeout=300)
        self.author_id, self.guild = author_id, guild
        btn = _Btn(self._abrir, label="Digitar senha", style=discord.ButtonStyle.danger, emoji="🔓")
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay("### 🔒 Desbloqueio global\nSó a senha definida no painel remove este bloqueio."),
            discord.ui.ActionRow(btn),
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await _erro_card(interaction, "Essa tela é de outra pessoa", "Use `,antiraid liberar global` para abrir a sua.")
            return False
        if not _admin(interaction.user):
            await _erro_card(interaction, "Sem permissão", "Só administradores liberam o bot.")
            return False
        return True

    async def _abrir(self, interaction, item):
        await interaction.response.send_modal(LiberarGlobalModal(self.guild))


# ---------------------------------------------------------------- painel interativo (botoes)

class AntiRaidView(discord.ui.LayoutView):
    def __init__(self, author_id: int, guild: discord.Guild):
        super().__init__(timeout=600)
        self.author_id, self.guild = author_id, guild
        self.notice, self.sent = "", None
        self.render()

    @classmethod
    async def open(cls, ctx):
        view = cls(ctx.author.id, ctx.guild)
        view.sent = await ctx.send(view=view, allowed_mentions=NO_MENTIONS)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                view=Card("Esse painel é de outra pessoa", "Use `,antiraid` pra abrir o seu."),
                ephemeral=True,
            )
            return False
        if not _admin(interaction.user):
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
        log.exception("erro no painel do anti-raid", exc_info=error)
        await _erro_card(interaction, "Algo deu errado", "Tente de novo.")

    # redesenha a tela
    async def update(self, interaction, notice=""):
        self.notice = notice
        self.render()
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)

    def render(self):
        cfg = get_cfg(self.guild.id)
        children = self._page_home(cfg)
        if self.notice:
            children.append(discord.ui.TextDisplay(f"-# {self.notice}"))
        self.clear_items()
        self.add_item(discord.ui.Container(*children))

    # ---- coisinhas pra montar os itens

    def _btn(self, label, fn, style=discord.ButtonStyle.secondary, emoji=None, disabled=False):
        return _Btn(fn, label=label[:80], style=style, emoji=emoji, disabled=disabled)

    def _toggle(self, chave, ligado="✅ Ligado", desligado="⏸️ Desligado"):
        async def h(interaction, item):
            novo = not get_cfg(self.guild.id)[chave]
            set_cfg(self.guild.id, chave, novo)
            await self.update(interaction, ligado if novo else desligado)
        return h

    async def _gatilho(self, interaction, item):
        atual = get_cfg(self.guild.id)["gatilho"]
        set_cfg(self.guild.id, "gatilho", "ambos" if atual != "ambos" else "qualquer")
        await self.update(interaction, "✅ Salvo")

    async def _limites(self, interaction, item):
        await interaction.response.send_modal(LimitesModal(self))

    async def _pick_log(self, interaction, item):
        set_cfg(self.guild.id, "log", item.values[0].id)
        await self.update(interaction, "✅ Salvo")

    async def _tirar_log(self, interaction, item):
        set_cfg(self.guild.id, "log", None)
        await self.update(interaction, "✅ Removido")

    async def _pick_cargos(self, interaction, item):
        ids = [r.id for r in item.values if not r.is_default()]
        set_cfg(self.guild.id, "ignorar_cargos", ids)
        await self.update(interaction, "✅ Salvo")

    async def _pick_usuarios(self, interaction, item):
        set_cfg(self.guild.id, "ignorar_usuarios", [u.id for u in item.values])
        await self.update(interaction, "✅ Salvo")

    async def _pick_avisar(self, interaction, item):
        set_cfg(self.guild.id, "avisar_usuarios", [u.id for u in item.values])
        await self.update(interaction, "✅ Destinatários dos alertas salvos")

    async def _senha(self, interaction, item):
        await interaction.response.send_modal(SenhaModal(self))

    # ---- tela unica, cada parte tem titulo + explicacao + botoes

    def _page_home(self, cfg):
        g = self.guild
        verde, cinza = discord.ButtonStyle.success, discord.ButtonStyle.secondary
        vermelho = discord.ButtonStyle.danger
        ativo, ambos = cfg["ativo"], cfg["gatilho"] == "ambos"

        canais = f"**{cfg['limite_canais']}** canais" if cfg["limite_canais"] else "canais (ignorado)"
        everyone = f"**{cfg['limite_everyone']}** @everyone" if cfg["limite_everyone"] else "@everyone (ignorado)"
        junta = "e" if ambos else "ou"

        faltam = _permissoes_faltando(g, limpar)
        perms = "✅ tudo certo" if not faltam else "⚠️ faltam: " + ", ".join(faltam)

        # so mostra cargo/usuario que ainda existe no servidor
        cargos_ok = [c for c in cfg["ignorar_cargos"] if g.get_role(c)]
        users_ok = [u for u in cfg["ignorar_usuarios"] if g.get_member(u)]
        cargos_sel = [g.get_role(c) for c in cargos_ok]
        users_sel = [g.get_member(u) for u in users_ok]
        avisar_ok = [u for u in cfg.get("avisar_usuarios", []) if g.get_member(u)]
        avisar_sel = [g.get_member(u) for u in avisar_ok]
        canal_log = g.get_channel(cfg["log"]) if cfg["log"] else None
        tem_senha = bool(cfg["senha"])
        trancados = lista_trancados(g.id)

        select_cargos = _RoleSel(
            self._pick_cargos, placeholder="Cargos ignorados (lista branca)",
            min_values=0, max_values=25, default_values=cargos_sel,
        )
        select_users = _UserSel(
            self._pick_usuarios, placeholder="Pessoas e bots ignorados (lista branca)",
            min_values=0, max_values=25, default_values=users_sel,
        )
        select_log = _ChanSel(
            self._pick_log, placeholder="Escolha o canal de logs",
            channel_types=[discord.ChannelType.text], min_values=1, max_values=1,
            default_values=[canal_log] if canal_log else [],
        )
        select_avisar = _UserSel(
            self._pick_avisar, placeholder="Quem recebe alerta por DM",
            min_values=0, max_values=25, default_values=avisar_sel,
        )

        return [
            discord.ui.TextDisplay(
                "## 🛡️ Anti-raid\n"
                "-# clique nos botões de cada parte pra mudar. tudo salva na hora"
            ),
            sep(),

            # parte 1
            _sec("🔘 Proteção", "liga e desliga a vigilância do servidor"),
            discord.ui.TextDisplay(
                f"**Status**  {'🟢 ligada' if ativo else '🔴 desligada'}\n"
                f"**Permissões do Poyo**  {perms}\n"
                "-# o cargo do Poyo precisa ficar acima dos cargos que ele vai tirar"
            ),
            discord.ui.ActionRow(
                self._btn("Desligar" if ativo else "Ligar",
                          self._toggle("ativo", "🟢 Proteção ligada", "🔴 Proteção desligada"),
                          vermelho if ativo else verde, "🛡️"),
            ),
            sep(),

            # parte 2
            _sec("⏱️ O que conta como raid", "quantas coisas em quantos segundos"),
            discord.ui.TextDisplay(
                f"**Regra**  {canais} criados {junta} {everyone} em **{cfg['janela']}s**\n"
                f"**Gatilho**  {'precisa dos dois juntos' if ambos else 'qualquer um dos dois já basta'}"
            ),
            discord.ui.ActionRow(
                self._btn("Editar limites", self._limites, emoji="✏️"),
                self._btn(f"Gatilho: {'os dois' if ambos else 'qualquer'}", self._gatilho,
                          verde if ambos else cinza, "🔀"),
            ),
            sep(),

            # parte 3
            _sec("🚨 O que o Poyo faz", "assim que o raid é detectado"),
            discord.ui.TextDisplay(
                "**Bot**  tira todos os cargos e bane\n"
                "**Membro**  tira todos os cargos e deixa de castigo\n"
                "**Trancado**  quem foi punido só é liberado com a senha (`,antiraid liberar`)\n"
                "**Limpeza do raid**  sempre ativa: apaga os canais e mensagens registrados na janela"
            ),
            discord.ui.ActionRow(
                self._btn("Limpeza automática obrigatória", self._toggle("limpar"),
                          verde, "🧹", disabled=True),
            ),
            sep(),

            # parte 4
            _sec("📜 Canal de logs", "onde o aviso do raid aparece"),
            discord.ui.TextDisplay(f"**Canal**  {canal_log.mention if canal_log else 'não definido'}"),
            discord.ui.ActionRow(select_log),
            discord.ui.ActionRow(
                self._btn("Remover canal", self._tirar_log, emoji="🗑️", disabled=canal_log is None),
            ),
            sep(),

            # parte 5
            _sec("✅ Lista branca", "quem nunca é punido (bots de confiança, a staff, etc.)"),
            discord.ui.TextDisplay(
                f"**Cargos**  {_lista(cargos_ok, '<@&{}>')}\n"
                f"**Pessoas e bots**  {_lista(users_ok, '<@{}>')}\n"
                f"**Alertas por DM**  {_lista(avisar_ok, '<@{}>')}\n"
                "-# ninguém é ignorado automaticamente; use a lista branca somente se realmente necessário"
            ),
            discord.ui.ActionRow(select_cargos),
            discord.ui.ActionRow(select_users),
            discord.ui.ActionRow(select_avisar),
            sep(),

            # parte 6
            _sec("🔐 Senha e trancados", "sem a senha ninguém consegue liberar quem foi punido"),
            discord.ui.TextDisplay(
                f"**Senha**  {'✅ definida' if tem_senha else '⚠️ não definida (ninguém consegue liberar!)'}\n"
                f"**Trancados agora**  {_lista(trancados, '<@{}>')}\n"
                "-# o campo de senha do Discord não esconde o texto: digite com ninguém olhando"
            ),
            discord.ui.ActionRow(
                self._btn("Trocar senha" if tem_senha else "Definir senha", self._senha,
                          cinza if tem_senha else verde, "🔐"),
            ),
        ]


# ---------------------------------------------------------------- cog

class AntiRaid(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild_id, user_id) -> deque de (momento, id_do_canal) / (momento, id_do_canal, id_da_mensagem)
        self._canais: dict = {}
        self._pings: dict = {}
        self._punindo: set = set()
        self._calmo: dict = {}   # (guild_id, user_id) -> momento ate quando ignorar

    # ---- regras

    def _ignorado(self, guild: discord.Guild, user_id: int, cfg: dict, membro=None) -> bool:
        if user_id in cfg["ignorar_usuarios"]:
            return True
        if cfg["ignorar_cargos"]:
            membro = membro or guild.get_member(user_id)
            if membro is not None and any(r.id in cfg["ignorar_cargos"] for r in membro.roles):
                return True
        return False

    def _em_calma(self, key) -> bool:
        ate = self._calmo.get(key)
        if ate is None:
            return False
        if time.monotonic() < ate:
            return True
        self._calmo.pop(key, None)
        return False

    @staticmethod
    def _podar(fila: Optional[deque], janela: int):
        agora = time.monotonic()
        while fila and agora - fila[0][0] > janela:
            fila.popleft()

    def _avaliar(self, key, cfg: dict):
        """devolve (disparou, texto com as contagens)"""
        janela = cfg["janela"]
        canais, pings = self._canais.get(key), self._pings.get(key)
        self._podar(canais, janela)
        self._podar(pings, janela)
        n_c, n_p = len(canais or ()), len(pings or ())

        criterios = []
        if cfg["limite_canais"] > 0:
            criterios.append(n_c > cfg["limite_canais"])
        if cfg["limite_everyone"] > 0:
            criterios.append(n_p > cfg["limite_everyone"])
        if not criterios:
            return False, ""
        disparou = all(criterios) if cfg["gatilho"] == "ambos" else any(criterios)
        texto = f"{n_c} canal(is) criado(s) e {n_p} mensagem(ns) com @everyone/@here em até {janela}s"
        return disparou, texto

    async def _checar(self, guild: discord.Guild, user_id: int, cfg: dict, quem=None):
        key = (guild.id, user_id)
        disparou, texto = self._avaliar(key, cfg)
        if not disparou or key in self._punindo:
            return
        self._punindo.add(key)
        try:
            await self._punir(guild, user_id, cfg, texto, quem)
        except Exception:
            log.exception("erro ao punir %s em %s", user_id, guild.id)
        finally:
            self._punindo.discard(key)
            self._canais.pop(key, None)
            self._pings.pop(key, None)
            self._calmo[key] = time.monotonic() + COOLDOWN_POS_PUNICAO

    # ---- deteccao

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        # canal criado (quem criou vem do registro de auditoria)
        if entry.action is not discord.AuditLogAction.channel_create:
            return
        guild, uid = entry.guild, entry.user_id
        if guild is None or uid is None:
            return
        cfg = get_cfg(guild.id)
        if not cfg["ativo"] or self._ignorado(guild, uid, cfg):
            return
        key = (guild.id, uid)
        if self._em_calma(key):
            return
        canal_id = getattr(entry.target, "id", None)
        self._canais.setdefault(key, deque()).append((time.monotonic(), canal_id))
        await self._checar(guild, uid, cfg, quem=entry.user)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.webhook_id is not None:
            return
        if not (message.mention_everyone or EVERYONE_RE.search(message.content or "")):
            return
        guild, uid = message.guild, message.author.id
        cfg = get_cfg(guild.id)
        membro = message.author if isinstance(message.author, discord.Member) else None
        if not cfg["ativo"] or self._ignorado(guild, uid, cfg, membro):
            return
        key = (guild.id, uid)
        if self._em_calma(key):
            return
        self._pings.setdefault(key, deque()).append((time.monotonic(), message.channel.id, message.id))
        await self._checar(guild, uid, cfg, quem=message.author)

    # ---- vigia dos trancados

    @commands.Cog.listener()
    async def on_member_update(self, antes: discord.Member, depois: discord.Member):
        # alguem devolveu cargo pra um trancado? tira de novo
        if get_trancado(depois.guild.id, depois.id) is None:
            return
        extras = [r for r in depois.roles if not r.is_default() and r.is_assignable()]
        if not extras:
            return
        try:
            await depois.remove_roles(*extras, reason="Anti-raid: membro trancado, cargos só voltam com a senha")
            log.warning("anti-raid: tirei %s cargo(s) devolvido(s) a %s em %s", len(extras), depois.id, depois.guild.id)
        except discord.HTTPException as e:
            log.warning("anti-raid: nao consegui re-tirar cargos de %s: %s", depois.id, e)

    @commands.Cog.listener()
    async def on_member_join(self, membro: discord.Member):
        # trancado que saiu e voltou: castigo de novo
        if membro.bot or get_trancado(membro.guild.id, membro.id) is None:
            return
        try:
            await membro.timeout(TIMEOUT_TRANCA, reason="Anti-raid: membro trancado voltou ao servidor")
        except discord.HTTPException as e:
            log.warning("anti-raid: nao consegui castigar %s ao voltar: %s", membro.id, e)

    # ---- punicao

    async def _punir(self, guild: discord.Guild, user_id: int, cfg: dict, motivo: str, quem=None):
        key = (guild.id, user_id)
        canais_raid = list(self._canais.get(key, ()))
        msgs_raid = list(self._pings.get(key, ()))

        membro = guild.get_member(user_id)
        if membro is None:
            try:
                membro = await guild.fetch_member(user_id)
            except discord.HTTPException:
                membro = None
        usuario = membro or quem
        if usuario is None:
            try:
                usuario = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                usuario = None
        eh_bot = bool(getattr(usuario, "bot", False))
        eh_proprio_bot = self.bot.user is not None and user_id == self.bot.user.id
        nome = str(usuario) if usuario is not None else str(user_id)
        razao = f"Anti-raid: {motivo}"[:480]

        linhas = []

        # Trava o bot inteiro antes de executar a contenção. O registro é
        # persistente, portanto continua valendo mesmo se o processo reiniciar.
        if cfg["senha"]:
            ativar_bloqueio_global(guild.id, motivo, user_id)
            linhas.append("🔒 **bot inteiro bloqueado**: só `,antiraid liberar global` com a senha desbloqueia")
        else:
            linhas.append("🚨 **bloqueio global não ativado: defina uma senha no painel antes de usar esta proteção**")

        # 0) tranca: guarda os cargos dele ANTES de tirar (e antes de qualquer outra coisa),
        #    assim a vigia ja protege e a liberacao sabe o que devolver
        set_trancado(guild.id, user_id, {
            "bot": eh_bot,
            "cargos": [r.id for r in membro.roles if not r.is_default() and not r.managed] if membro else [],
            "em": int(time.time()),
        })
        linhas.append(f"🔒 trancado: só sai com a senha (`,antiraid liberar {user_id}`)")
        if not cfg["senha"]:
            linhas.append("⚠️ **não existe senha definida**: defina no `,antiraid` ou ninguém consegue liberar pelo bot")

        # 1) tira os cargos
        if membro is not None:
            tirar = [r for r in membro.roles if not r.is_default() and r.is_assignable()]
            if tirar:
                try:
                    await membro.remove_roles(*tirar, reason=razao)
                    linhas.append(f"✅ {len(tirar)} cargo(s) removido(s)")
                except discord.Forbidden:
                    linhas.append("⚠️ não consegui tirar os cargos (o cargo dele está acima do meu ou me falta permissão)")
                except discord.HTTPException as e:
                    linhas.append(f"⚠️ erro ao tirar os cargos ({e.status})")
            else:
                linhas.append("ℹ️ não tinha cargo que eu pudesse tirar")
            presos = [r for r in membro.roles if not r.is_default() and not r.is_assignable() and not r.managed]
            if presos:
                linhas.append(f"⚠️ {len(presos)} cargo(s) acima do meu continuam com ele")
        else:
            linhas.append("ℹ️ a pessoa não está mais no servidor")

        # 1b) membro: deixa de castigo (nao fala, nao reage, nao entra em voz)
        if membro is not None and not eh_bot:
            try:
                await membro.timeout(TIMEOUT_TRANCA, reason=razao)
                linhas.append("✅ de castigo por quase 28 dias")
            except discord.Forbidden:
                linhas.append("⚠️ não consegui aplicar o castigo (cargo dele acima do meu, admin, ou falta Castigar membros)")
            except discord.HTTPException as e:
                linhas.append(f"⚠️ erro ao aplicar o castigo ({e.status})")

        # 2) bot: bane
        if eh_bot and not eh_proprio_bot:
            try:
                await guild.ban(membro or discord.Object(id=user_id), reason=razao, delete_message_seconds=0)
                linhas.append("✅ bot banido")
            except discord.Forbidden:
                linhas.append("⚠️ não consegui banir o bot (o cargo dele está acima do meu ou me falta permissão)")
            except discord.HTTPException as e:
                linhas.append(f"⚠️ erro ao banir o bot ({e.status})")
        elif eh_proprio_bot:
            linhas.append("ℹ️ o próprio bot foi detectado; não me bana para preservar o desbloqueio")

        # 3) limpeza opcional
        # A limpeza faz parte da contenção e não depende mais de um botão.
        if True:
            apagados_canais = apagadas_msgs = 0
            for _, canal_id in canais_raid:
                canal = guild.get_channel(canal_id) if canal_id else None
                if canal is None:
                    continue
                try:
                    await canal.delete(reason=razao)
                    apagados_canais += 1
                except discord.HTTPException:
                    pass
            for _, canal_id, msg_id in msgs_raid:
                canal = guild.get_channel_or_thread(canal_id)
                parcial = getattr(canal, "get_partial_message", None)
                if parcial is None:
                    continue
                try:
                    await parcial(msg_id).delete()
                    apagadas_msgs += 1
                except discord.HTTPException:
                    pass
            linhas.append(f"🧹 {apagados_canais} canal(is) e {apagadas_msgs} mensagem(ns) apagado(s)")

        log.warning("raid detectado em %s (%s): %s | %s", guild.id, nome, motivo, "; ".join(linhas))
        await self._avisar(guild, cfg, user_id, nome, eh_bot, motivo, linhas)

    async def _avisar(self, guild, cfg, user_id, nome, eh_bot, motivo, linhas):
        canal = guild.get_channel(cfg["log"]) if cfg["log"] else None
        tipo = "bot" if eh_bot else "membro"
        texto = (
            f"**Quem**  <@{user_id}> · `{nome}` · `{user_id}` ({tipo})\n"
            f"**Motivo**  {motivo}\n"
            f"**O que eu fiz**\n" + "\n".join(linhas)
        )
        if canal is not None:
            try:
                await canal.send(view=Card("🚨 Raid detectado", texto), allowed_mentions=NO_MENTIONS)
            except discord.HTTPException as e:
                log.warning("nao consegui avisar no canal de logs: %s", e)
        aviso_dm = Card("🚨 Anti-raid acionado", f"Servidor: **{guild.name}**\n" + texto)
        for uid in cfg.get("avisar_usuarios", []):
            destinatario = guild.get_member(uid)
            if destinatario is None:
                continue
            try:
                await destinatario.send(view=aviso_dm, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException as e:
                log.warning("nao consegui avisar %s por DM: %s", uid, e)

    # ---- comando

    @commands.group(
        name="antiraid",
        invoke_without_command=True,
        extras={
            "categoria": "Moderação",
            "uso": ",antiraid",
            "descricao": "Abre o painel do anti-raid (limites, logs, lista branca e o que o Poyo faz num raid).",
        },
    )
    @commands.guild_only()
    async def antiraid(self, ctx: commands.Context):
        if not _admin(ctx.author):
            await _say(ctx, "Sem permissão", "Só administradores mexem no anti-raid.", ok=False)
            return
        await AntiRaidView.open(ctx)

    @antiraid.command(
        name="liberar",
        extras={
            "categoria": "Moderação",
            "uso": ",antiraid liberar <@pessoa ou id>",
            "descricao": "Libera quem o anti-raid trancou (pede a senha do anti-raid).",
        },
    )
    @commands.guild_only()
    async def liberar(self, ctx: commands.Context, *, alvo: Optional[str] = None):
        if not _admin(ctx.author):
            await _say(ctx, "Sem permissão", "Só administradores liberam.", ok=False)
            return
        if not get_cfg(ctx.guild.id)["senha"]:
            await _say(ctx, "Sem senha definida", "Defina a senha no `,antiraid` primeiro.", ok=False)
            return
        if (alvo or "").strip().lower() in {"global", "bot", "tudo", "total"}:
            await ctx.send(view=Card(
                "🔒 Bot bloqueado",
                "Desbloquear o bot inteiro exige a senha do anti-raid."
            ), allowed_mentions=NO_MENTIONS)
            await ctx.send(view=_GlobalUnlockView(ctx.author.id, ctx.guild), allowed_mentions=NO_MENTIONS)
            return
        achou = re.search(r"\d{15,20}", alvo or "")
        if achou is None:
            presos = lista_trancados(ctx.guild.id)
            lista = "\n".join(f"<@{u}> · `{u}`" for u in presos) or "ninguém"
            await _say(ctx, "Trancados", f"{lista}\n-# use `,antiraid liberar <@pessoa ou id>`", keep=True)
            return
        uid = int(achou.group())
        if get_trancado(ctx.guild.id, uid) is None:
            await _say(ctx, "Não está trancado", "Essa pessoa não foi trancada pelo anti-raid.", ok=False)
            return
        await ctx.send(view=LiberarView(ctx.author.id, ctx.guild, uid), allowed_mentions=NO_MENTIONS)


async def setup(bot: commands.Bot):
    async def anti_raid_global_check(ctx: commands.Context) -> bool:
        if ctx.guild is None or bloqueio_global(ctx.guild.id) is None:
            return True
        comando = getattr(ctx.command, "qualified_name", "")
        # A única porta de saída é o fluxo que pede a senha; o callback
        # continua exigindo administrador e senha correta.
        if comando == "antiraid liberar":
            return True
        await ctx.send(
            view=Card("🔒 Bot bloqueado", "O anti-raid detectou uma possível invasão. "
                      "Todos os comandos estão temporariamente bloqueados. "
                      "Use `,antiraid liberar <@pessoa ou id>` e informe a senha."),
            allowed_mentions=NO_MENTIONS,
            delete_after=12,
        )
        return False

    bot.add_check(anti_raid_global_check)
    await bot.add_cog(AntiRaid(bot))
