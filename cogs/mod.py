from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands


DURACAO_RE = re.compile(
    r"^(?P<valor>[1-9]\d*)(?P<unidade>s|m|h|d|w)$",
    re.IGNORECASE,
)


UNIDADES = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
}


def embed_sucesso(
    titulo: str,
    descricao: str,
) -> discord.Embed:
    """
    Cria embed sem definir cor.
    Dessa forma, o Discord não mostra a faixa lateral colorida.
    """

    return discord.Embed(
        title=titulo,
        description=descricao,
    )


def embed_erro(
    descricao: str,
) -> discord.Embed:
    """
    Embed padrão para mensagens de erro.
    """

    return discord.Embed(
        title="Ação não realizada",
        description=descricao,
    )


def analisar_duracao(
    texto: str,
) -> Optional[int]:
    """
    Converte formatos como:
    30s
    10m
    2h
    7d
    1w

    Para segundos.
    """

    correspondencia = DURACAO_RE.fullmatch(
        texto.lower().strip()
    )

    if not correspondencia:
        return None

    valor = int(
        correspondencia.group("valor")
    )

    unidade = correspondencia.group(
        "unidade"
    ).lower()

    return valor * UNIDADES[unidade]


def formatar_duracao(
    segundos: int,
) -> str:
    """
    Transforma segundos em um texto mais bonito.
    """

    partes = []

    unidades = (
        ("semana", 604800),
        ("dia", 86400),
        ("hora", 3600),
        ("minuto", 60),
        ("segundo", 1),
    )

    for nome, divisor in unidades:
        quantidade, segundos = divmod(
            segundos,
            divisor,
        )

        if quantidade:
            plural = "s" if quantidade != 1 else ""

            partes.append(
                f"{quantidade} {nome}{plural}"
            )

    return ", ".join(partes)


class Moderacao(commands.Cog):
    """
    Comandos de moderação e proteção do servidor.
    """

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    async def cog_check(
        self,
        ctx: commands.Context,
    ) -> bool:
        """
        Impede que os comandos sejam usados em mensagens privadas.
        """

        if ctx.guild is None:
            raise commands.NoPrivateMessage()

        return True

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ):
        """
        Apaga mensagens de membros que estiverem em timeout.

        O timeout continua sendo aplicado normalmente pelo Discord,
        mas qualquer mensagem enviada durante o período é removida.
        """

        if message.guild is None:
            return

        if message.author.bot:
            return

        membro = message.author

        if not isinstance(membro, discord.Member):
            return

        tempo_final = membro.timed_out_until

        if (
            tempo_final
            and tempo_final > discord.utils.utcnow()
        ):
            try:
                await message.delete()

            except discord.HTTPException:
                pass

    @commands.command(
        name="silenciar",
        extras={
            "categoria": "Moderação",
            "uso": ",silenciar @membro 1h",
            "descricao": (
                "Aplica timeout e apaga as mensagens enviadas "
                "durante o período."
            ),
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        moderate_members=True,
    )
    @commands.bot_has_permissions(
        moderate_members=True,
        manage_messages=True,
    )
    async def silenciar(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        tempo: str,
        *,
        motivo: str = "Nenhum motivo informado",
    ):
        """
        Silencia um membro usando timeout.
        """

        segundos = analisar_duracao(tempo)

        if (
            segundos is None
            or segundos < 1
            or segundos > 28 * 86400
        ):
            await ctx.send(
                embed=embed_erro(
                    "Informe um tempo válido entre `1s` e `28d`.\n\n"
                    "Exemplos: `30m`, `1h`, `12h`, `7d`."
                ),
                delete_after=10,
            )

            return

        if membro == ctx.author:
            await ctx.send(
                embed=embed_erro(
                    "Você não pode aplicar essa ação em si mesmo."
                ),
                delete_after=10,
            )

            return

        if membro == ctx.guild.owner:
            await ctx.send(
                embed=embed_erro(
                    "O dono do servidor não pode receber essa punição."
                ),
                delete_after=10,
            )

            return

        if membro.top_role >= ctx.author.top_role:
            await ctx.send(
                embed=embed_erro(
                    "Esse membro está acima de você "
                    "na hierarquia de cargos."
                ),
                delete_after=10,
            )

            return

        if membro.top_role >= ctx.guild.me.top_role:
            await ctx.send(
                embed=embed_erro(
                    "Meu cargo precisa estar acima do cargo "
                    "desse membro."
                ),
                delete_after=10,
            )

            return

        await membro.timeout(
            timedelta(seconds=segundos),
            reason=f"{ctx.author} — {motivo}",
        )

        await ctx.send(
            embed=embed_sucesso(
                "Membro silenciado",
                (
                    f"{membro.mention} ficará em timeout por "
                    f"**{formatar_duracao(segundos)}**.\n\n"
                    f"**Motivo:** {motivo}"
                ),
            )
        )

    @commands.command(
        name="rsilenciar",
        extras={
            "categoria": "Moderação",
            "uso": ",rsilenciar @membro",
            "descricao": "Remove o timeout de um membro.",
        },
    )
    @commands.has_permissions(
        moderate_members=True,
    )
    @commands.bot_has_permissions(
        moderate_members=True,
    )
    async def rsilenciar(
        self,
        ctx: commands.Context,
        membro: discord.Member,
    ):
        """
        Remove o timeout de um membro.
        """

        await membro.timeout(
            None,
            reason=f"Timeout removido por {ctx.author}",
        )

        await ctx.send(
            embed=embed_sucesso(
                "Silenciamento removido",
                (
                    f"O timeout de {membro.mention} foi removido.\n"
                    "O membro já pode falar novamente."
                ),
            )
        )

    @commands.command(
        name="banir",
        extras={
            "categoria": "Moderação",
            "uso": ",banir @membro [motivo]",
            "descricao": (
                "Bane um membro e remove as mensagens recentes dele."
            ),
        },
    )
    @commands.has_permissions(
        ban_members=True,
    )
    @commands.bot_has_permissions(
        ban_members=True,
    )
    async def banir(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        *,
        motivo: str = "Nenhum motivo informado",
    ):
        """
        Bane um membro do servidor.
        """

        if membro == ctx.guild.owner:
            await ctx.send(
                embed=embed_erro(
                    "O dono do servidor não pode ser banido."
                ),
                delete_after=10,
            )

            return

        if membro.top_role >= ctx.author.top_role:
            await ctx.send(
                embed=embed_erro(
                    "Esse membro está acima de você "
                    "na hierarquia de cargos."
                ),
                delete_after=10,
            )

            return

        if membro.top_role >= ctx.guild.me.top_role:
            await ctx.send(
                embed=embed_erro(
                    "Meu cargo precisa estar acima do cargo "
                    "desse membro."
                ),
                delete_after=10,
            )

            return

        await membro.ban(
            reason=f"{ctx.author} — {motivo}",
            delete_message_seconds=86400,
        )

        await ctx.send(
            embed=embed_sucesso(
                "Banimento aplicado",
                (
                    f"**{membro}** foi banido do servidor.\n\n"
                    f"**Motivo:** {motivo}"
                ),
            )
        )

    @commands.command(
        name="rban",
        extras={
            "categoria": "Moderação",
            "uso": ",rban ID",
            "descricao": "Remove o banimento de um usuário pelo ID.",
        },
    )
    @commands.has_permissions(
        ban_members=True,
    )
    @commands.bot_has_permissions(
        ban_members=True,
    )
    async def rban(
        self,
        ctx: commands.Context,
        usuario_id: int,
    ):
        """
        Remove o banimento usando o ID do usuário.
        """

        try:
            ban_entry = await ctx.guild.fetch_ban(
                discord.Object(id=usuario_id)
            )

            await ctx.guild.unban(
                ban_entry.user,
                reason=f"Banimento removido por {ctx.author}",
            )

        except discord.NotFound:
            await ctx.send(
                embed=embed_erro(
                    "Não encontrei um banimento ativo para esse ID."
                ),
                delete_after=10,
            )

            return

        await ctx.send(
            embed=embed_sucesso(
                "Banimento removido",
                (
                    f"O usuário **{ban_entry.user}** "
                    "pode entrar no servidor novamente."
                ),
            )
        )

    @commands.command(
        name="expulsar",
        aliases=("kick",),
        extras={
            "categoria": "Moderação",
            "uso": ",expulsar @membro [motivo]",
            "descricao": (
                "Expulsa um membro sem impedir que ele "
                "volte ao servidor."
            ),
        },
    )
    @commands.has_permissions(
        kick_members=True,
    )
    @commands.bot_has_permissions(
        kick_members=True,
    )
    async def expulsar(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        *,
        motivo: str = "Nenhum motivo informado",
    ):
        """
        Expulsa um membro do servidor.
        """

        if membro == ctx.guild.owner:
            await ctx.send(
                embed=embed_erro(
                    "O dono do servidor não pode ser expulso."
                ),
                delete_after=10,
            )

            return

        if membro.top_role >= ctx.author.top_role:
            await ctx.send(
                embed=embed_erro(
                    "Esse membro está acima de você "
                    "na hierarquia de cargos."
                ),
                delete_after=10,
            )

            return

        if membro.top_role >= ctx.guild.me.top_role:
            await ctx.send(
                embed=embed_erro(
                    "Meu cargo precisa estar acima do cargo "
                    "desse membro."
                ),
                delete_after=10,
            )

            return

        await membro.kick(
            reason=f"{ctx.author} — {motivo}",
        )

        await ctx.send(
            embed=embed_sucesso(
                "Membro expulso",
                (
                    f"**{membro}** foi removido do servidor.\n\n"
                    f"**Motivo:** {motivo}"
                ),
            )
        )

    @commands.command(
        name="trancar",
        extras={
            "categoria": "Moderação",
            "uso": ",trancar",
            "descricao": (
                "Impede membros comuns de enviar mensagens "
                "no canal atual."
            ),
        },
    )
    @commands.has_permissions(
        manage_channels=True,
    )
    @commands.bot_has_permissions(
        manage_channels=True,
    )
    async def trancar(
        self,
        ctx: commands.Context,
    ):
        """
        Tranca o canal atual.
        """

        permissao = ctx.channel.overwrites_for(
            ctx.guild.default_role
        )

        permissao.send_messages = False

        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=permissao,
            reason=f"Canal trancado por {ctx.author}",
        )

        await ctx.send(
            embed=embed_sucesso(
                "Canal trancado",
                (
                    "Este canal foi trancado.\n"
                    "Apenas membros com permissão para ignorar "
                    "a restrição poderão enviar mensagens."
                ),
            )
        )

    @commands.command(
        name="destrancar",
        extras={
            "categoria": "Moderação",
            "uso": ",destrancar",
            "descricao": (
                "Libera novamente o envio de mensagens "
                "no canal atual."
            ),
        },
    )
    @commands.has_permissions(
        manage_channels=True,
    )
    @commands.bot_has_permissions(
        manage_channels=True,
    )
    async def destrancar(
        self,
        ctx: commands.Context,
    ):
        """
        Destranca o canal atual.
        """

        permissao = ctx.channel.overwrites_for(
            ctx.guild.default_role
        )

        permissao.send_messages = None

        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=permissao,
            reason=f"Canal destrancado por {ctx.author}",
        )

        await ctx.send(
            embed=embed_sucesso(
                "Canal destrancado",
                "O envio de mensagens foi liberado novamente neste canal.",
            )
        )

    @commands.command(
        name="limpar",
        aliases=("clear",),
        extras={
            "categoria": "Moderação",
            "uso": ",limpar 20",
            "descricao": (
                "Apaga de 1 a 100 mensagens recentes do canal."
            ),
        },
    )
    @commands.has_permissions(
        manage_messages=True,
    )
    @commands.bot_has_permissions(
        manage_messages=True,
        read_message_history=True,
    )
    async def limpar(
        self,
        ctx: commands.Context,
        quantidade: int,
    ):
        """
        Limpa mensagens recentes do canal atual.
        """

        if quantidade < 1 or quantidade > 100:
            await ctx.send(
                embed=embed_erro(
                    "Escolha uma quantidade entre 1 e 100."
                ),
                delete_after=8,
            )

            return

        apagadas = await ctx.channel.purge(
            limit=quantidade + 1
        )

        aviso = await ctx.send(
            embed=embed_sucesso(
                "Limpeza concluída",
                (
                    f"{len(apagadas) - 1} mensagens foram "
                    "removidas deste canal."
                ),
            )
        )

        await aviso.delete(delay=5)

    @commands.command(
        name="aviso",
        extras={
            "categoria": "Moderação",
            "uso": ",aviso @membro motivo",
            "descricao": (
                "Registra uma advertência pública e organizada "
                "para um membro."
            ),
        },
    )
    @commands.has_permissions(
        manage_messages=True,
    )
    async def aviso(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        *,
        motivo: str,
    ):
        """
        Envia uma advertência organizada.
        """

        await ctx.send(
            embed=embed_sucesso(
                "Advertência registrada",
                (
                    f"{membro.mention} recebeu uma advertência de "
                    f"{ctx.author.mention}.\n\n"
                    f"**Motivo:** {motivo}"
                ),
            )
        )


async def setup(
    bot: commands.Bot,
):
    await bot.add_cog(
        Moderacao(bot)
    )
