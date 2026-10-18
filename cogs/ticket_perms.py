"""
Corrige o "O canal é somente leitura" nos tickets (threads privadas). discord.py 2.x

Causa: thread privada herda a permissão do canal pai (ex.: 🩸・suport). Lá, ninguém
tinha "Enviar mensagens em threads" (só overrides individuais do dono do ticket),
então o atendente via o ticket mas não conseguia escrever.

Correção: liberar "Enviar mensagens em threads" para @everyone NO CANAL PAI.
É seguro: thread privada só é vista/usada por quem foi adicionado a ela, e o
canal pai continua sem permissão de enviar mensagens normais.

Instalar:
    await bot.load_extension("ticket_perms")

Uso:
    1) /corrigir-tickets canal:#suport   (só ADM, roda uma vez e corrige o ticket aberto agora)
    2) Depois disso o listener on_thread_create garante a permissão em todo ticket novo.

No código que "assume" o ticket, para garantir que o atendente esteja na thread:
    from ticket_perms import adicionar_atendente
    await adicionar_atendente(thread, atendente)

E REMOVA do código de criação de ticket qualquer set_permissions por membro no canal
pai (o que dá send_messages_in_threads só pro dono): não é mais necessário e acumula
overrides (limite do Discord: 100 por canal).
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("ticket_perms")

_SO_THREADS = discord.Permissions(send_messages_in_threads=True).value


async def garantir_pai(parent, limpar=False):
    """Idempotente. Retorna lista de linhas descrevendo o que foi mudado."""
    feito = []
    if parent is None:
        return feito

    everyone = parent.guild.default_role
    ow = parent.overwrites_for(everyone)
    if ow.send_messages_in_threads is not True:
        ow.send_messages_in_threads = True
        await parent.set_permissions(
            everyone, overwrite=ow, reason="Tickets: liberar envio em threads privadas"
        )
        feito.append(f"@everyone: liberado 'enviar mensagens em threads' em #{parent.name}")

    if limpar:
        for alvo, o in list(parent.overwrites.items()):
            if isinstance(alvo, discord.Role):
                continue
            allow, deny = o.pair()
            # só remove a sobra típica: override individual que dá APENAS
            # send_messages_in_threads (criado pro dono do ticket).
            if allow.value == _SO_THREADS and deny.value == 0:
                await parent.set_permissions(
                    alvo, overwrite=None, reason="Tickets: limpar override individual antigo"
                )
                feito.append(f"removido override individual {getattr(alvo, 'name', alvo)} ({alvo.id})")
    return feito


async def adicionar_atendente(thread, membro):
    """Garante que o atendente esteja dentro da thread privada."""
    try:
        await thread.add_user(membro)
    except discord.HTTPException as e:
        log.warning("nao consegui adicionar %s na thread %s: %r", membro, thread.id, e)


class TicketPerms(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if thread.type is not discord.ChannelType.private_thread:
            return
        try:
            await garantir_pai(thread.parent)
        except discord.HTTPException as e:
            log.warning("falhou ao garantir permissao no pai da thread %s: %r", thread.id, e)

    @app_commands.command(
        name="corrigir-tickets",
        description="Libera o envio de mensagens nos tickets (threads) do canal informado",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.describe(
        canal="Canal onde os tickets (threads) são criados, ex.: #suport",
        limpar="Remover overrides individuais antigos que só davam envio em threads",
    )
    async def corrigir(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        limpar: bool = True,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Só ADM.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            feito = await garantir_pai(canal, limpar=limpar)
        except discord.HTTPException as e:
            return await interaction.followup.send(f"Falhou: `{e!r}`", ephemeral=True)
        msg = "\n".join(f"- {l}" for l in feito) if feito else "Nada a mudar: já estava correto."
        await interaction.followup.send(f"Pronto.\n{msg}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(TicketPerms(bot))
