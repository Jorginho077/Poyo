"""Chat de brigas: canal liberado só pra quem confirma a idade mínima,
que o Poyo limpa sozinho de tempos em tempos.

Arquivo novo e independente — não mexe em nenhum outro cog. Guarda os
próprios dados em JSON, no mesmo padrão dos outros cogs do Poyo.

Pensa assim: o canal fica fechado pra @everyone (o Poyo esconde quando
você liga o sistema pelo ,pbconfig). Quem confirma a idade e passa do
mínimo configurado ganha uma liberação só pra essa pessoa nesse canal.
Isso é diferente de um canal "+18" de verdade do Discord — pra isso,
marque o canal como "Conteúdo Sensível" nas configurações dele; os dois
podem ser usados juntos.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("bot.briga")

STATE_FILE = Path(os.getenv("BRIGA_STATE_FILE", "briga_state.json"))

IDADE_PADRAO = 16
INTERVALO_PADRAO_H = 5
IDADES_SUGERIDAS = (13, 14, 16, 18, 21)
INTERVALOS_SUGERIDOS_H = (1, 2, 3, 5, 6, 12, 24)

COR_PAINEL = discord.Colour.from_rgb(24, 26, 32)  # slate escuro, discreto
COR_OK = discord.Colour.from_rgb(80, 250, 160)
COR_NEGADO = discord.Colour.from_rgb(237, 66, 69)


# ---------------------------------------------------------------- estado


def _ler() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        bak = STATE_FILE.with_name(STATE_FILE.name + ".bak")
        log.error("%s ilegível (%s); cópia guardada em %s", STATE_FILE, e, bak)
        try:
            STATE_FILE.replace(bak)
        except OSError:
            pass
        return {}


def _escrever(data: dict) -> None:
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        log.warning("não consegui salvar %s: %s", STATE_FILE, e)


state: dict = _ler()
state.setdefault("guilds", {})


def _save() -> None:
    _escrever(state)


def get_cfg(guild_id: int) -> dict:
    g = state["guilds"].setdefault(str(guild_id), {})
    g.setdefault("canal_id", None)
    g.setdefault("idade_minima", IDADE_PADRAO)
    g.setdefault("intervalo_horas", INTERVALO_PADRAO_H)
    g.setdefault("ativo", False)
    g.setdefault("ultima_limpeza", None)
    g.setdefault("verificados", {})  # user_id -> {"idade": int, "quando": epoch}
    return g


# ---------------------------------------------------------------- visual


class Card(discord.ui.LayoutView):
    """Cartão simples, sem barra colorida — pra avisos rápidos."""

    def __init__(self, titulo: str, texto: str = "") -> None:
        super().__init__(timeout=None)
        corpo = f"### {titulo}" + (f"\n{texto}" if texto else "")
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(corpo)))


def _linha(rotulo: str, valor: str) -> str:
    return f"{rotulo:<12}→ {valor}"


def painel_texto(cfg: dict, guild: discord.Guild) -> str:
    canal = guild.get_channel(cfg["canal_id"]) if cfg["canal_id"] else None
    status = "🟢 ativo" if cfg["ativo"] else "⚪ desligado"
    ultima = (
        "ainda não limpou"
        if not cfg["ultima_limpeza"]
        else f"<t:{int(cfg['ultima_limpeza'])}:R>"
    )
    linhas = "\n".join([
        _linha("CANAL", canal.mention if canal else "— não definido —"),
        _linha("IDADE MÍN.", f"{cfg['idade_minima']}+"),
        _linha("LIMPEZA", f"a cada {cfg['intervalo_horas']}h  ·  última: {ultima}"),
        _linha("STATUS", status),
    ])
    return (
        "## 🛡️ Chat de Brigas · config\n"
        f"```\n{linhas}\n```\n"
        "-# Quem confirma a idade e passa do mínimo ganha acesso só pra "
        "essa pessoa nesse canal. O resto do servidor continua sem ver."
    )


# ----------------------------------------------------------- verificação


class IdadeModal(discord.ui.Modal, title="Confirmar idade"):
    idade = discord.ui.TextInput(
        label="Quantos anos você tem?",
        placeholder="Ex.: 17",
        max_length=3,
    )

    def __init__(self, guild_id: int) -> None:
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bruto = self.idade.value.strip()
        if not bruto.isdigit() or not (5 <= int(bruto) <= 120):
            await interaction.response.send_message(
                view=Card("Idade inválida", "Digite só o número da sua idade."),
                ephemeral=True,
            )
            return

        idade = int(bruto)
        cfg = get_cfg(self.guild_id)
        canal = interaction.guild.get_channel(cfg["canal_id"])

        cfg["verificados"][str(interaction.user.id)] = {
            "idade": idade, "quando": time.time(),
        }
        _save()

        if idade < cfg["idade_minima"]:
            await interaction.response.send_message(
                view=Card(
                    "Esse canal não é pra você ainda",
                    f"A idade mínima configurada aqui é **{cfg['idade_minima']} anos**. "
                    "Isso não é sobre confiança, é sobre o tipo de conversa que rola nesse canal.",
                ),
                ephemeral=True,
            )
            return

        if canal is not None:
            try:
                await canal.set_permissions(
                    interaction.user, view_channel=True, send_messages=True,
                    reason="Chat de brigas: idade confirmada",
                )
            except discord.HTTPException as e:
                log.warning(
                    "não consegui liberar %s no chat de brigas de #%s: %s "
                    "(o bot precisa de Gerenciar Permissões nesse canal)",
                    interaction.user, canal.name, e,
                )
                await interaction.response.send_message(
                    view=Card(
                        "Quase lá",
                        "Confirmei sua idade, mas não consegui liberar o canal agora. "
                        "Avise a staff.",
                    ),
                    ephemeral=True,
                )
                return

        await interaction.response.send_message(
            view=Card(
                "Liberado 🔓",
                f"Acesso liberado em {canal.mention if canal else 'o chat de brigas'}. "
                "Lá dentro vale falar mais pesado — fora de lá, as regras normais do "
                "servidor continuam valendo.",
            ),
            ephemeral=True,
        )


class VerificarView(discord.ui.LayoutView):
    """Mensagem fixa no canal, com o botão de confirmar idade."""

    def __init__(self, guild_id: int, idade_minima: int) -> None:
        super().__init__(timeout=None)
        botao = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Confirmar idade",
            emoji="🔞",
            custom_id=f"poyo:briga:verificar:{guild_id}",
        )
        botao.callback = self._clicar
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "## 🛡️ Chat de Brigas\n"
                    f"Esse canal é liberado só pra quem tem **{idade_minima}+ anos**. "
                    "Aqui vale xingar e falar mais pesado — fora daqui, as regras "
                    "normais do servidor continuam valendo.\n\n"
                    "Clique abaixo pra confirmar sua idade e liberar o acesso."
                ),
                discord.ui.Separator(),
                discord.ui.ActionRow(botao),
                accent_colour=COR_PAINEL,
            )
        )

    async def _clicar(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(IdadeModal(interaction.guild.id))


# --------------------------------------------------------------- config UI


class _NumeroSelect(discord.ui.Select):
    def __init__(self, rotulo: str, custom_id: str, opcoes: tuple[int, ...], sufixo: str) -> None:
        super().__init__(
            placeholder=rotulo,
            custom_id=custom_id,
            options=[
                discord.SelectOption(label=f"{n}{sufixo}", value=str(n)) for n in opcoes
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        pass  # sobrescrito em tempo de execução pelo ConfigView


class ConfigView(discord.ui.LayoutView):
    def __init__(self, cog: "Briga", guild: discord.Guild) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.guild = guild
        self._montar()

    def _montar(self) -> None:
        self.clear_items()
        cfg = get_cfg(self.guild.id)

        canal_sel = discord.ui.ChannelSelect(
            placeholder="Escolher o canal do chat de brigas",
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=1,
        )
        canal_sel.callback = self._def_canal

        idade_sel = _NumeroSelect(
            f"Idade mínima (atual: {cfg['idade_minima']}+)",
            "idade", IDADES_SUGERIDAS, "+",
        )
        idade_sel.callback = self._def_idade

        intervalo_sel = _NumeroSelect(
            f"Limpeza automática (atual: a cada {cfg['intervalo_horas']}h)",
            "intervalo", INTERVALOS_SUGERIDOS_H, "h",
        )
        intervalo_sel.callback = self._def_intervalo

        publicar = discord.ui.Button(
            label="Publicar aviso no canal", style=discord.ButtonStyle.primary, emoji="📌",
        )
        publicar.callback = self._publicar

        limpar_agora = discord.ui.Button(
            label="Limpar agora", style=discord.ButtonStyle.secondary, emoji="🧹",
        )
        limpar_agora.callback = self._limpar_agora

        ligar = discord.ui.Button(
            label="Desligar" if cfg["ativo"] else "Ligar",
            style=discord.ButtonStyle.danger if cfg["ativo"] else discord.ButtonStyle.success,
            emoji="⚪" if cfg["ativo"] else "🟢",
        )
        ligar.callback = self._alternar

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(painel_texto(cfg, self.guild)),
                discord.ui.Separator(),
                discord.ui.ActionRow(canal_sel),
                discord.ui.ActionRow(idade_sel),
                discord.ui.ActionRow(intervalo_sel),
                discord.ui.ActionRow(publicar, limpar_agora, ligar),
                accent_colour=COR_PAINEL,
            )
        )

    async def _recarregar(self, interaction: discord.Interaction) -> None:
        self._montar()
        await interaction.response.edit_message(view=self)

    async def _def_canal(self, interaction: discord.Interaction) -> None:
        canal = interaction.data["values"][0]
        get_cfg(self.guild.id)["canal_id"] = int(canal)
        _save()
        await self._recarregar(interaction)

    async def _def_idade(self, interaction: discord.Interaction) -> None:
        get_cfg(self.guild.id)["idade_minima"] = int(interaction.data["values"][0])
        _save()
        await self._recarregar(interaction)

    async def _def_intervalo(self, interaction: discord.Interaction) -> None:
        get_cfg(self.guild.id)["intervalo_horas"] = int(interaction.data["values"][0])
        _save()
        await self._recarregar(interaction)

    async def _alternar(self, interaction: discord.Interaction) -> None:
        cfg = get_cfg(self.guild.id)
        canal = self.guild.get_channel(cfg["canal_id"]) if cfg["canal_id"] else None

        if not cfg["ativo"] and canal is None:
            await interaction.response.send_message(
                view=Card("Falta o canal", "Escolha o canal antes de ligar."), ephemeral=True,
            )
            return

        cfg["ativo"] = not cfg["ativo"]
        if cfg["ativo"] and canal is not None:
            try:
                await canal.set_permissions(
                    self.guild.default_role, view_channel=False,
                    reason="Chat de brigas: ligado, escondendo de quem não verificou",
                )
            except discord.HTTPException as e:
                log.warning("não consegui esconder #%s: %s", canal.name, e)
        _save()
        self.cog._sincronizar_tarefa()
        await self._recarregar(interaction)

    async def _publicar(self, interaction: discord.Interaction) -> None:
        cfg = get_cfg(self.guild.id)
        canal = self.guild.get_channel(cfg["canal_id"]) if cfg["canal_id"] else None
        if canal is None:
            await interaction.response.send_message(
                view=Card("Falta o canal", "Escolha o canal antes de publicar."), ephemeral=True,
            )
            return
        try:
            await canal.send(view=VerificarView(self.guild.id, cfg["idade_minima"]))
        except discord.HTTPException as e:
            await interaction.response.send_message(
                view=Card("Não consegui publicar", str(e)), ephemeral=True,
            )
            return
        await interaction.response.send_message(
            view=Card("Publicado", f"Aviso enviado em {canal.mention}."), ephemeral=True,
        )

    async def _limpar_agora(self, interaction: discord.Interaction) -> None:
        cfg = get_cfg(self.guild.id)
        canal = self.guild.get_channel(cfg["canal_id"]) if cfg["canal_id"] else None
        if canal is None:
            await interaction.response.send_message(
                view=Card("Falta o canal", "Escolha o canal primeiro."), ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        apagadas = await self.cog.limpar_canal(canal)
        cfg["ultima_limpeza"] = time.time()
        _save()
        await interaction.followup.send(
            view=Card("Limpo", f"{apagadas} mensagens apagadas em {canal.mention}."),
            ephemeral=True,
        )


# ---------------------------------------------------------------------- cog


class Briga(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(_VerificarGenerico)
        self._sincronizar_tarefa()

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(_VerificarGenerico)
        if self.limpeza_periodica.is_running():
            self.limpeza_periodica.cancel()

    def _sincronizar_tarefa(self) -> None:
        algum_ativo = any(g.get("ativo") for g in state["guilds"].values())
        if algum_ativo and not self.limpeza_periodica.is_running():
            self.limpeza_periodica.start()
        elif not algum_ativo and self.limpeza_periodica.is_running():
            self.limpeza_periodica.cancel()

    async def limpar_canal(self, canal: discord.TextChannel) -> int:
        total = 0
        while True:
            apagadas = await canal.purge(limit=200, reason="Chat de brigas: limpeza automática")
            total += len(apagadas)
            if len(apagadas) < 200:
                break
        return total

    @tasks.loop(minutes=15)
    async def limpeza_periodica(self) -> None:
        agora = time.time()
        for gid, cfg in list(state["guilds"].items()):
            if not cfg.get("ativo") or not cfg.get("canal_id"):
                continue
            intervalo_s = cfg.get("intervalo_horas", INTERVALO_PADRAO_H) * 3600
            ultima = cfg.get("ultima_limpeza") or 0
            if agora - ultima < intervalo_s:
                continue

            guild = self.bot.get_guild(int(gid))
            canal = guild.get_channel(cfg["canal_id"]) if guild else None
            if canal is None:
                continue
            try:
                await self.limpar_canal(canal)
            except discord.HTTPException as e:
                log.warning("não consegui limpar #%s: %s", canal.name, e)
                continue
            cfg["ultima_limpeza"] = agora
            _save()

    @limpeza_periodica.before_loop
    async def _antes_limpeza(self) -> None:
        await self.bot.wait_until_ready()

    @commands.hybrid_command(
        name="pbconfig",
        description="Configura o chat de brigas: canal, idade mínima e limpeza automática.",
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def pbconfig(self, ctx: commands.Context) -> None:
        await ctx.send(view=ConfigView(self, ctx.guild))


class _VerificarGenerico(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"poyo:briga:verificar:(?P<gid>[0-9]+)",
):
    """Deixa o botão de 'Confirmar idade' funcionar depois que o bot reinicia."""

    def __init__(self) -> None:
        super().__init__(discord.ui.Button(custom_id="poyo:briga:verificar:0"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match) -> "_VerificarGenerico":
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(IdadeModal(interaction.guild.id))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Briga(bot))
