"""Painel de configuração do Fogo (`,fogoconfig`).

- Ligar/desligar os avisos na DM e no servidor (só o dono do bot).
- Mudar o horário da virada do dia (só o dono do bot, vale para todos os
  servidores).
- Remover o Fogo de uma pessoa (administradores, só neste servidor).
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from . import _fogo_db as db
from . import _fogo_hora as hora
from . import _fogo_hora as conf
from . import _fogo_visual as visual


def _nome(guild: Optional[discord.Guild], user_id: int) -> str:
    membro = guild.get_member(user_id) if guild else None
    return membro.display_name if membro else str(user_id)


def _rotulo_fogo(guild: Optional[discord.Guild], f: sqlite3.Row) -> str:
    texto = (
        f"{_nome(guild, f['usuario_a'])} + {_nome(guild, f['usuario_b'])}"
        f" · {visual.dias(f['sequencia'])}"
    )
    return texto[:100]


async def _ativos_do_servidor(guild_id: int) -> int:
    lista = await asyncio.to_thread(db.fogos_ativos)
    return sum(1 for f in lista if f["guild_id"] == guild_id)


class _ViewDoAutor(discord.ui.LayoutView):
    """Só quem abriu o painel pode clicar nele."""

    def __init__(self, autor_id: int, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.autor_id = autor_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.autor_id:
            return True
        await interaction.response.send_message(
            "Esse painel não é seu.", ephemeral=True
        )
        return False


# ------------------------------------------------------------------ painel


class HorarioModal(discord.ui.Modal, title="Horário da virada"):
    horario = discord.ui.TextInput(
        label="Que horas o dia vira? (HH:MM)",
        placeholder="00:00",
        min_length=1,
        max_length=5,
    )

    def __init__(self, painel: "PainelConfig") -> None:
        super().__init__()
        self.painel = painel
        self.horario.default = hora.rotulo()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        valido, minutos = hora.interpretar(self.horario.value)
        if not valido:
            await interaction.response.send_message(
                "Horário inválido. Use HH:MM, por exemplo 06:00.",
                ephemeral=True,
            )
            return

        antigo = hora.hoje()
        await asyncio.to_thread(hora.definir, minutos)
        novo = hora.hoje()
        # O dia que está valendo continua valendo (ninguém apaga por causa
        # da troca de horário).
        await asyncio.to_thread(db.realinhar_dia, antigo, novo)

        ativos = await _ativos_do_servidor(self.painel.guild_id)
        await interaction.response.edit_message(
            **visual.edicao(PainelConfig(
                self.painel.bot,
                self.painel.autor_id,
                self.painel.guild_id,
                ativos,
            ))
        )


class PainelConfig(_ViewDoAutor):
    def __init__(
        self, bot: commands.Bot, autor_id: int, guild_id: int, ativos: int
    ) -> None:
        super().__init__(autor_id)
        self.bot = bot
        self.guild_id = guild_id

        dm, servidor = conf.dm_ativo(), conf.servidor_ativo()
        horario = discord.ui.Button(
            style=discord.ButtonStyle.primary,
            label="Mudar horário",
            emoji="🕛",
        )
        botao_dm = discord.ui.Button(
            style=discord.ButtonStyle.success if dm else discord.ButtonStyle.secondary,
            label=f"DM: {'ligada' if dm else 'desligada'}",
            emoji="💌",
        )
        botao_servidor = discord.ui.Button(
            style=(
                discord.ButtonStyle.success
                if servidor
                else discord.ButtonStyle.secondary
            ),
            label=f"Servidor: {'ligado' if servidor else 'desligado'}",
            emoji="📢",
        )
        remover = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Remover Fogo",
            emoji="🧯",
        )
        horario.callback = self._horario
        botao_dm.callback = self._alternar_dm
        botao_servidor.callback = self._alternar_servidor
        remover.callback = self._remover

        self.add_item(
            visual.container(
                discord.ui.TextDisplay("## ⚙️ Config do Fogo"),
                discord.ui.Separator(visible=False),
                discord.ui.TextDisplay(
                    f"**Virada do dia:** {hora.rotulo()}\n"
                    f"**Avisos na DM:** {'ligados' if dm else 'desligados'}\n"
                    f"**Avisos no servidor:** {'ligados' if servidor else 'desligados'}\n"
                    f"**Fogos ativos aqui:** {ativos}\n"
                    "-# Horário e avisos valem para todos os servidores."
                ),
                discord.ui.Separator(visible=False),
                discord.ui.ActionRow(horario, remover),
                discord.ui.ActionRow(botao_dm, botao_servidor),
            )
        )

    async def _so_dono(self, interaction: discord.Interaction, aviso: str) -> bool:
        if await self.bot.is_owner(interaction.user):
            return True
        await interaction.response.send_message(aviso, ephemeral=True)
        return False

    async def _horario(self, interaction: discord.Interaction) -> None:
        if not await self._so_dono(interaction, "Só o dono do bot muda o horário."):
            return
        await interaction.response.send_modal(HorarioModal(self))

    async def _alternar(self, interaction: discord.Interaction, campo: str) -> None:
        if not await self._so_dono(interaction, "Só o dono do bot muda isso."):
            return
        dm, servidor = conf.dm_ativo(), conf.servidor_ativo()
        if campo == "dm":
            dm = not dm
        else:
            servidor = not servidor
        if not dm and not servidor:
            await interaction.response.send_message(
                "Deixe pelo menos um ligado (DM ou servidor).", ephemeral=True
            )
            return
        await asyncio.to_thread(conf.definir_envio, dm, servidor)
        ativos = await _ativos_do_servidor(self.guild_id)
        await interaction.response.edit_message(
            **visual.edicao(PainelConfig(self.bot, self.autor_id, self.guild_id, ativos))
        )

    async def _alternar_dm(self, interaction: discord.Interaction) -> None:
        await self._alternar(interaction, "dm")

    async def _alternar_servidor(self, interaction: discord.Interaction) -> None:
        await self._alternar(interaction, "servidor")

    async def _remover(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            **visual.envio(EscolherPessoa(self.autor_id, self.guild_id)),
            ephemeral=True,
        )


# ----------------------------------------------------------------- remover


class EscolherPessoa(_ViewDoAutor):
    def __init__(self, autor_id: int, guild_id: int) -> None:
        super().__init__(autor_id)
        self.guild_id = guild_id
        self.seletor = discord.ui.UserSelect(
            placeholder="Escolha a pessoa", min_values=1, max_values=1
        )
        self.seletor.callback = self._escolheu
        self.add_item(
            visual.container(
                discord.ui.TextDisplay(
                    "## 🧯 Remover Fogo\nEscolha a pessoa."
                ),
                discord.ui.Separator(visible=False),
                discord.ui.ActionRow(self.seletor),
            )
        )

    async def _escolheu(self, interaction: discord.Interaction) -> None:
        pessoa = self.seletor.values[0]
        lista = await asyncio.to_thread(
            db.fogos_do_membro, self.guild_id, pessoa.id
        )
        if not lista:
            await interaction.response.edit_message(
                **visual.edicao(visual.cartao_aviso(
                    "Nada a remover",
                    f"<@{pessoa.id}> não tem Fogo ativo aqui.",
                ))
            )
            return
        if len(lista) == 1:
            view: discord.ui.LayoutView = ConfirmarRemocao(
                self.autor_id, lista[0]
            )
        else:
            view = EscolherFogo(
                self.autor_id, pessoa.id, lista, interaction.guild
            )
        await interaction.response.edit_message(**visual.edicao(view))


class EscolherFogo(_ViewDoAutor):
    def __init__(
        self,
        autor_id: int,
        pessoa_id: int,
        lista: list[sqlite3.Row],
        guild: Optional[discord.Guild],
    ) -> None:
        super().__init__(autor_id)
        self.seletor = discord.ui.Select(
            placeholder="Qual Fogo?",
            options=[
                discord.SelectOption(
                    label=_rotulo_fogo(guild, f), value=str(f["id"])
                )
                for f in lista[:25]
            ],
        )
        self.seletor.callback = self._escolheu
        self.add_item(
            visual.container(
                discord.ui.TextDisplay(
                    f"## 🧯 Remover Fogo\n<@{pessoa_id}> tem "
                    f"{len(lista)} Fogos. Qual remover?"
                ),
                discord.ui.Separator(visible=False),
                discord.ui.ActionRow(self.seletor),
            )
        )

    async def _escolheu(self, interaction: discord.Interaction) -> None:
        fogo = await asyncio.to_thread(
            db.obter_fogo, int(self.seletor.values[0])
        )
        if fogo is None or not fogo["ativo"]:
            await interaction.response.edit_message(
                **visual.edicao(visual.cartao_aviso(
                    "Já encerrado", "Esse Fogo já não estava ativo."
                ))
            )
            return
        await interaction.response.edit_message(
            **visual.edicao(ConfirmarRemocao(self.autor_id, fogo))
        )


class ConfirmarRemocao(_ViewDoAutor):
    def __init__(self, autor_id: int, fogo: sqlite3.Row) -> None:
        super().__init__(autor_id)
        self.fogo = fogo

        confirmar = discord.ui.Button(
            style=discord.ButtonStyle.danger, label="Remover", emoji="🧯"
        )
        cancelar = discord.ui.Button(
            style=discord.ButtonStyle.secondary, label="Cancelar"
        )
        confirmar.callback = self._confirmar
        cancelar.callback = self._cancelar

        nome = f"\n{visual.EMOJI_FOGO} **Fogo:** {fogo['nome']}" if fogo["nome"] else ""
        self.add_item(
            visual.container(
                discord.ui.TextDisplay("## 🧯 Remover Fogo"),
                discord.ui.Separator(visible=False),
                discord.ui.TextDisplay(
                    f"<@{fogo['usuario_a']}> + <@{fogo['usuario_b']}>{nome}\n"
                    f"**Sequência:** {visual.dias(fogo['sequencia'])}\n\n"
                    "Isso encerra o Fogo agora. Tem certeza?"
                ),
                discord.ui.Separator(visible=False),
                discord.ui.ActionRow(confirmar, cancelar),
            )
        )

    async def _cancelar(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            **visual.edicao(visual.cartao_aviso("Cancelado", "Nada foi removido."))
        )
        self.stop()

    async def _confirmar(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        fogo = await asyncio.to_thread(db.remover_fogo, self.fogo["id"])
        if fogo is None:
            aviso = visual.cartao_aviso(
                "Já encerrado", "Esse Fogo já não estava ativo."
            )
        else:
            cog = interaction.client.get_cog("Fogo")
            if cog is not None:
                try:
                    await cog.encerrar_removido(fogo)
                except Exception as erro:  # o Fogo já foi removido
                    print(f"Fogo {fogo['id']}: erro ao limpar painéis: {erro!r}")
            aviso = visual.cartao_aviso(
                "Fogo removido",
                f"<@{fogo['usuario_a']}> + <@{fogo['usuario_b']}> "
                "não têm mais um Fogo. Eles podem abrir outro com `,fogo`.",
            )
        await interaction.edit_original_response(**visual.edicao(aviso))
        self.stop()


# --------------------------------------------------------------------- cog


class FogoConfig(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="fogoconfig",
        description="(Admin) Painel de configuração do Fogo.",
        extras={
            "categoria": "Fogo",
            "uso": ",fogoconfig",
            "descricao": (
                "Para administradores: hora da virada do dia, avisos na DM/servidor "
                "e remover o Fogo de alguém."
            ),
        },
    )
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def fogoconfig(self, ctx: commands.Context) -> None:
        ativos = await _ativos_do_servidor(ctx.guild.id)
        view = PainelConfig(self.bot, ctx.author.id, ctx.guild.id, ativos)
        if ctx.interaction is not None:
            await ctx.send(**visual.envio(view), ephemeral=True)
        else:
            await ctx.send(**visual.envio(view), delete_after=300)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FogoConfig(bot))
