from __future__ import annotations

import re
import time
from typing import Optional

import discord
from discord.ext import commands


DURACAO_RE = re.compile(
    r"^(?P<valor>[1-9]\d*)(?P<unidade>s|m|h|d|w)$",
    re.IGNORECASE,
)


UNIDADES_EM_SEGUNDOS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
}


def criar_embed(
    titulo: str,
    descricao: str,
) -> discord.Embed:
    """
    Cria um embed sem definir cor.
    Assim, ele não possui a borda/faixa colorida lateral.
    """

    return discord.Embed(
        title=titulo,
        description=descricao,
    )


def criar_erro(
    descricao: str,
) -> discord.Embed:
    return criar_embed(
        "Ação não realizada",
        descricao,
    )


def converter_tempo(
    texto: str,
) -> Optional[int]:
    """
    Converte:

    30s = 30 segundos
    10m = 10 minutos
    2h  = 2 horas
    7d  = 7 dias
    1w  = 1 semana
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

    return valor * UNIDADES_EM_SEGUNDOS[unidade]


def formatar_tempo(
    segundos: int,
) -> str:
    """
    Transforma segundos em um texto amigável.
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
    Comandos de moderação.

    O silenciamento deste bot NÃO usa o timeout nativo
    do Discord.

    O sistema funciona de forma personalizada:
    - O membro continua podendo enviar mensagens.
    - O bot identifica as mensagens dele.
    - O bot apaga essas mensagens automaticamente.
    - Quando o tempo termina, as mensagens deixam de ser apagadas.
    """

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

        # Estrutura:
        #
        # {
        #     id_do_servidor: {
        #         id_do_membro: timestamp_de_expiracao
        #     }
        # }
        #
        self.silenciados: dict[int, dict[int, float]] = {}

    async def cog_check(
        self,
        ctx: commands.Context,
    ) -> bool:
        """
        Os comandos só podem ser usados dentro de servidores.
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
        Apaga automaticamente as mensagens de membros
        que estejam dentro do período de silenciamento.

        Este sistema não utiliza:
        - timeout nativo;
        - cargo de silenciado;
        - alteração de permissões do membro.
        """

        if message.guild is None:
            return

        if message.author.bot:
            return

        guild_id = message.guild.id
        membro_id = message.author.id

        silenciados_do_servidor = self.silenciados.get(
            guild_id,
            {},
        )

        expiracao = silenciados_do_servidor.get(
            membro_id
        )

        if expiracao is None:
            return

        agora = time.time()

        # Quando o período acaba, remove o membro
        # da lista de silenciados.
        if agora >= expiracao:
            silenciados_do_servidor.pop(
                membro_id,
                None,
            )

            if not silenciados_do_servidor:
                self.silenciados.pop(
                    guild_id,
                    None,
                )

            return

        # O período ainda está ativo.
        # A mensagem é apagada imediatamente.
        try:
            await message.delete()

        except discord.NotFound:
            # A mensagem já foi apagada.
            pass

        except discord.Forbidden:
            # O bot não tem Gerenciar mensagens no canal.
            pass

        except discord.HTTPException:
            # Evita que um erro da API derrube o bot.
            pass

    @commands.command(
        name="silenciar",
        extras={
            "categoria": "Moderação",
            "uso": ",silenciar @membro 1h",
            "descricao": (
                "Silencia um membro apagando automaticamente "
                "as mensagens dele durante o período."
            ),
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_messages=True,
    )
    @commands.bot_has_permissions(
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
        Silencia um membro sem usar o timeout nativo do Discord.
        """

        segundos = converter_tempo(tempo)

        if segundos is None:
            await ctx.send(
                embed=criar_erro(
                    "O tempo informado é inválido.\n\n"
                    "Use um formato como:\n"
                    "`30s`, `10m`, `2h`, `7d` ou `1w`."
                ),
                delete_after=10,
            )

            return

        # Limite definido pelo bot.
        # Como não é timeout nativo, você pode alterar este valor.
        limite_maximo = 28 * 24 * 60 * 60

        if segundos > limite_maximo:
            await ctx.send(
                embed=criar_erro(
                    "O silenciamento não pode ultrapassar 28 dias."
                ),
                delete_after=10,
            )

            return

        if membro == ctx.author:
            await ctx.send(
                embed=criar_erro(
                    "Você não pode silenciar a si mesmo."
                ),
                delete_after=10,
            )

            return

        if membro == ctx.guild.owner:
            await ctx.send(
                embed=criar_erro(
                    "O dono do servidor não pode ser silenciado."
                ),
                delete_after=10,
            )

            return

        if membro.top_role >= ctx.author.top_role:
            await ctx.send(
                embed=criar_erro(
                    "Esse membro possui um cargo igual ou superior "
                    "ao seu cargo mais alto."
                ),
                delete_after=10,
            )

            return

        bot_membro = ctx.guild.me

        if bot_membro is None:
            await ctx.send(
                embed=criar_erro(
                    "Não consegui verificar a hierarquia do servidor."
                ),
                delete_after=10,
            )

            return

        if membro.top_role >= bot_membro.top_role:
            await ctx.send(
                embed=criar_erro(
                    "Meu cargo precisa estar acima do cargo "
                    "desse membro."
                ),
                delete_after=10,
            )

            return

        guild_id = ctx.guild.id
        membro_id = membro.id
        expiracao = time.time() + segundos

        if guild_id not in self.silenciados:
            self.silenciados[guild_id] = {}

        # Se ele já estiver silenciado, o novo comando
        # atualiza o tempo para o novo período.
        self.silenciados[guild_id][membro_id] = expiracao

        await ctx.send(
            embed=criar_embed(
                "Membro silenciado",
                (
                    f"{membro.mention} foi silenciado por "
                    f"**{formatar_tempo(segundos)}**.\n\n"
                    "Durante esse período, qualquer mensagem nova "
                    "enviada por ele será apagada automaticamente.\n\n"
                    f"**Motivo:** {motivo}"
                ),
            )
        )

    @commands.command(
        name="rsilenciar",
        extras={
            "categoria": "Moderação",
            "uso": ",rsilenciar @membro",
            "descricao": (
                "Remove o silenciamento personalizado de um membro."
            ),
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_messages=True,
    )
    @commands.bot_has_permissions(
        manage_messages=True,
    )
    async def rsilenciar(
        self,
        ctx: commands.Context,
        membro: discord.Member,
    ):
        """
        Remove o membro da lista de silenciados.
        """

        guild_id = ctx.guild.id
        membro_id = membro.id

        silenciados_do_servidor = self.silenciados.get(
            guild_id,
            {},
        )

        if membro_id not in silenciados_do_servidor:
            await ctx.send(
                embed=criar_erro(
                    f"{membro.mention} não está silenciado pelo bot."
                ),
                delete_after=10,
            )

            return

        silenciados_do_servidor.pop(
            membro_id,
            None,
        )

        if not silenciados_do_servidor:
            self.silenciados.pop(
                guild_id,
                None,
            )

        await ctx.send(
            embed=criar_embed(
                "Silenciamento removido",
                (
                    f"O silenciamento de {membro.mention} "
                    "foi removido.\n"
                    "As mensagens dele não serão mais apagadas."
                ),
            )
        )

    @commands.command(
        name="banir",
        extras={
            "categoria": "Moderação",
            "uso": ",banir @membro [motivo]",
            "descricao": "Bane um membro do servidor.",
        },
    )
    @commands.guild_only()
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
                embed=criar_erro(
                    "O dono do servidor não pode ser banido."
                ),
                delete_after=10,
            )

            return

        bot_membro = ctx.guild.me

        if (
            membro.top_role >= ctx.author.top_role
            or bot_membro is None
            or membro.top_role >= bot_membro.top_role
        ):
            await ctx.send(
                embed=criar_erro(
                    "Não posso banir esse membro por causa "
                    "da hierarquia de cargos."
                ),
                delete_after=10,
            )

            return

        try:
            await membro.ban(
                reason=f"{ctx.author} — {motivo}",
                delete_message_seconds=86400,
            )

        except discord.Forbidden:
            await ctx.send(
                embed=criar_erro(
                    "Não tenho permissão para banir esse membro."
                ),
                delete_after=10,
            )

            return

        await ctx.send(
            embed=criar_embed(
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
            "descricao": "Remove o banimento de um usuário usando o ID.",
        },
    )
    @commands.guild_only()
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
        Remove um banimento através do ID do usuário.
        """

        try:
            banimento = await ctx.guild.fetch_ban(
                discord.Object(id=usuario_id)
            )

            await ctx.guild.unban(
                banimento.user,
                reason=f"Banimento removido por {ctx.author}",
            )

        except discord.NotFound:
            await ctx.send(
                embed=criar_erro(
                    "Não encontrei um banimento ativo para esse ID."
                ),
                delete_after=10,
            )

            return

        except discord.Forbidden:
            await ctx.send(
                embed=criar_erro(
                    "Não tenho permissão para remover banimentos."
                ),
                delete_after=10,
            )

            return

        await ctx.send(
            embed=criar_embed(
                "Banimento removido",
                (
                    f"O usuário **{banimento.user}** "
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
            "descricao": "Expulsa um membro do servidor.",
        },
    )
    @commands.guild_only()
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
                embed=criar_erro(
                    "O dono do servidor não pode ser expulso."
                ),
                delete_after=10,
            )

            return

        bot_membro = ctx.guild.me

        if (
            membro.top_role >= ctx.author.top_role
            or bot_membro is None
            or membro.top_role >= bot_membro.top_role
        ):
            await ctx.send(
                embed=criar_erro(
                    "Não posso expulsar esse membro por causa "
                    "da hierarquia de cargos."
                ),
                delete_after=10,
            )

            return

        try:
            await membro.kick(
                reason=f"{ctx.author} — {motivo}",
            )

        except discord.Forbidden:
            await ctx.send(
                embed=criar_erro(
                    "Não tenho permissão para expulsar esse membro."
                ),
                delete_after=10,
            )

            return

        await ctx.send(
            embed=criar_embed(
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
    @commands.guild_only()
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
            embed=criar_embed(
                "Canal trancado",
                (
                    "Este canal foi trancado.\n"
                    "Membros comuns não poderão enviar mensagens "
                    "até que ele seja destrancado."
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
    @commands.guild_only()
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
            embed=criar_embed(
                "Canal destrancado",
                "O envio de mensagens foi liberado novamente.",
            )
        )

    @commands.command(
        name="limpar",
        aliases=("clear",),
        extras={
            "categoria": "Moderação",
            "uso": ",limpar 20",
            "descricao": "Apaga de 1 a 100 mensagens recentes.",
        },
    )
    @commands.guild_only()
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
        Apaga mensagens recentes do canal.
        """

        if quantidade < 1 or quantidade > 100:
            await ctx.send(
                embed=criar_erro(
                    "Escolha uma quantidade entre 1 e 100."
                ),
                delete_after=8,
            )

            return

        apagadas = await ctx.channel.purge(
            limit=quantidade + 1
        )

        aviso = await ctx.send(
            embed=criar_embed(
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
            "descricao": "Registra uma advertência no canal.",
        },
    )
    @commands.guild_only()
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
        Registra uma advertência pública.
        """

        await ctx.send(
            embed=criar_embed(
                "Advertência registrada",
                (
                    f"{membro.mention} recebeu uma advertência "
                    f"de {ctx.author.mention}.\n\n"
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
