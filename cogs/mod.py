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


UNIDADES = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
}


# Tempo que os containers de resposta ficam visíveis.
TEMPO_DAS_RESPOSTAS = 25


class Cartao(discord.ui.LayoutView):
    """
    Cartão criado com Components V2.

    Não utiliza discord.Embed.
    Não define accent_color, então não possui
    a barra lateral colorida dos embeds tradicionais.
    """

    def __init__(
        self,
        titulo: str,
        descricao: str,
    ) -> None:
        super().__init__(
            timeout=None
        )

        texto = (
            f"## {titulo}\n\n"
            f"{descricao}"
        )

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(texto)
            )
        )


def cartao_sucesso(
    titulo: str,
    descricao: str,
) -> Cartao:
    return Cartao(
        titulo,
        descricao,
    )


def cartao_erro(
    descricao: str,
) -> Cartao:
    return Cartao(
        "Ação não realizada",
        descricao,
    )


async def responder(
    ctx: commands.Context,
    view: Cartao,
) -> discord.Message:
    """
    Envia o container e remove a resposta depois de 25 segundos.
    """

    return await ctx.send(
        view=view,
        delete_after=TEMPO_DAS_RESPOSTAS,
    )


def analisar_duracao(
    texto: str,
) -> Optional[int]:
    """
    Converte formatos como:

    30s = 30 segundos
    1m  = 1 minuto
    1h  = 1 hora
    2h  = 2 horas
    28d = 28 dias
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

    return valor * UNIDADES[unidade]


def formatar_duracao(
    segundos: int,
) -> str:
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
                f"{quantidade} "
                f"{nome}{plural}"
            )

    return ", ".join(partes)


class Moderacao(commands.Cog):
    """
    Comandos de moderação do servidor.

    O comando ,calado não utiliza o timeout nativo
    do Discord.

    Enquanto o membro estiver calado, qualquer mensagem
    nova enviada por ele será apagada automaticamente.
    """

    def __init__(
        self,
        bot: commands.Bot,
    ) -> None:
        self.bot = bot

        # Estrutura:
        #
        # {
        #     id_do_servidor: {
        #         id_do_membro: timestamp_de_expiracao
        #     }
        # }
        #
        self.calados: dict[
            int,
            dict[int, float],
        ] = {}

    async def cog_check(
        self,
        ctx: commands.Context,
    ) -> bool:
        """
        Permite o uso dos comandos somente dentro de servidores.
        """

        if ctx.guild is None:
            raise commands.NoPrivateMessage()

        return True

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ) -> None:
        """
        Apaga mensagens de membros calados
        até o tempo terminar.
        """

        if message.guild is None:
            return

        if message.author.bot:
            return

        guild_id = message.guild.id
        membro_id = message.author.id

        calados_do_servidor = self.calados.get(
            guild_id,
            {},
        )

        expiracao = calados_do_servidor.get(
            membro_id,
        )

        if expiracao is None:
            return

        agora = time.time()

        if agora >= expiracao:
            calados_do_servidor.pop(
                membro_id,
                None,
            )

            if not calados_do_servidor:
                self.calados.pop(
                    guild_id,
                    None,
                )

            return

        try:
            await message.delete()

        except discord.NotFound:
            pass

        except discord.Forbidden:
            pass

        except discord.HTTPException:
            pass

    @commands.command(
        name="calado",
        aliases=("silenciar",),
        extras={
            "categoria": "Moderação",
            "uso": ",calado @membro 1h",
            "descricao": (
                "Apaga as mensagens de um membro durante "
                "o período informado, sem usar timeout nativo."
            ),
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_messages=True
    )
    @commands.bot_has_permissions(
        manage_messages=True
    )
    async def calado(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        tempo: str,
        *,
        motivo: str = "Nenhum motivo informado",
    ) -> None:
        """
        Deixa um membro calado sem utilizar
        o timeout nativo do Discord.
        """

        segundos = analisar_duracao(
            tempo
        )

        if (
            segundos is None
            or segundos < 1
            or segundos > 28 * 86400
        ):
            await responder(
                ctx,
                cartao_erro(
                    (
                        "Informe um tempo válido entre `1s` e `28d`.\n"
                        "Exemplos: `1m`, `1h`, `2h` ou `28d`."
                    )
                ),
            )

            return

        if membro == ctx.author:
            await responder(
                ctx,
                cartao_erro(
                    "Você não pode deixar a si mesmo calado."
                ),
            )

            return

        if membro == ctx.guild.owner:
            await responder(
                ctx,
                cartao_erro(
                    "O dono do servidor não pode ser deixado calado."
                ),
            )

            return

        bot_membro = ctx.guild.me

        if membro.top_role >= ctx.author.top_role:
            await responder(
                ctx,
                cartao_erro(
                    (
                        "Esse membro possui um cargo igual ou "
                        "superior ao seu cargo mais alto."
                    )
                ),
            )

            return

        if (
            bot_membro is None
            or membro.top_role >= bot_membro.top_role
        ):
            await responder(
                ctx,
                cartao_erro(
                    (
                        "Meu cargo precisa estar acima do cargo "
                        "desse membro."
                    )
                ),
            )

            return

        expiracao = time.time() + segundos

        self.calados.setdefault(
            ctx.guild.id,
            {},
        )[membro.id] = expiracao

        await responder(
            ctx,
            cartao_sucesso(
                "Membro calado",
                (
                    f"{membro.mention} ficará calado por "
                    f"**{formatar_duracao(segundos)}**.\n\n"
                    "Durante esse período, todas as mensagens "
                    "novas enviadas por ele serão apagadas "
                    "automaticamente.\n\n"
                    f"**Motivo:** {motivo}"
                ),
            ),
        )

    @commands.command(
        name="nchoraxx",
        aliases=(
            "rcalado",
            "rsilenciar",
        ),
        extras={
            "categoria": "Moderação",
            "uso": ",nchoraxx @membro",
            "descricao": (
                "Remove o modo calado personalizado de um membro."
            ),
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_messages=True
    )
    @commands.bot_has_permissions(
        manage_messages=True
    )
    async def nchoraxx(
        self,
        ctx: commands.Context,
        membro: discord.Member,
    ) -> None:
        """
        Remove o modo calado de um membro.
        """

        calados_do_servidor = self.calados.get(
            ctx.guild.id,
            {},
        )

        if membro.id not in calados_do_servidor:
            await responder(
                ctx,
                cartao_erro(
                    f"{membro.mention} não está calado pelo bot."
                ),
            )

            return

        calados_do_servidor.pop(
            membro.id,
            None,
        )

        if not calados_do_servidor:
            self.calados.pop(
                ctx.guild.id,
                None,
            )

        await responder(
            ctx,
            cartao_sucesso(
                "Modo calado removido",
                (
                    f"{membro.mention} já pode enviar "
                    "mensagens normalmente."
                ),
            ),
        )

    @commands.command(
        name="banir",
        extras={
            "categoria": "Moderação",
            "uso": ",banir @membro [motivo]",
            "descricao": (
                "Bane um membro e remove as mensagens "
                "recentes dele."
            ),
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        ban_members=True
    )
    @commands.bot_has_permissions(
        ban_members=True
    )
    async def banir(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        *,
        motivo: str = "Nenhum motivo informado",
    ) -> None:
        if not await self._pode_punir(
            ctx,
            membro,
            "banir",
        ):
            return

        try:
            await membro.ban(
                reason=f"{ctx.author} — {motivo}",
                delete_message_seconds=86400,
            )

        except discord.Forbidden:
            await responder(
                ctx,
                cartao_erro(
                    "Não tenho permissão para banir esse membro."
                ),
            )

            return

        await responder(
            ctx,
            cartao_sucesso(
                "Banimento aplicado",
                (
                    f"**{membro}** foi banido do servidor.\n"
                    f"**Motivo:** {motivo}"
                ),
            ),
        )

    @commands.command(
        name="rban",
        extras={
            "categoria": "Moderação",
            "uso": ",rban ID",
            "descricao": (
                "Remove o banimento de um usuário pelo ID."
            ),
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        ban_members=True
    )
    @commands.bot_has_permissions(
        ban_members=True
    )
    async def rban(
        self,
        ctx: commands.Context,
        usuario_id: int,
    ) -> None:
        try:
            banimento = await ctx.guild.fetch_ban(
                discord.Object(
                    id=usuario_id
                )
            )

            await ctx.guild.unban(
                banimento.user,
                reason=(
                    f"Banimento removido por "
                    f"{ctx.author}"
                ),
            )

        except discord.NotFound:
            await responder(
                ctx,
                cartao_erro(
                    (
                        "Não encontrei um banimento ativo "
                        "para esse ID."
                    )
                ),
            )

            return

        except discord.Forbidden:
            await responder(
                ctx,
                cartao_erro(
                    (
                        "Não tenho permissão para remover "
                        "banimentos."
                    )
                ),
            )

            return

        await responder(
            ctx,
            cartao_sucesso(
                "Banimento removido",
                (
                    f"**{banimento.user}** pode entrar no "
                    "servidor novamente."
                ),
            ),
        )

    @commands.command(
        name="expulsar",
        aliases=("kick",),
        extras={
            "categoria": "Moderação",
            "uso": ",expulsar @membro [motivo]",
            "descricao": (
                "Expulsa um membro sem impedir que ele volte."
            ),
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        kick_members=True
    )
    @commands.bot_has_permissions(
        kick_members=True
    )
    async def expulsar(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        *,
        motivo: str = "Nenhum motivo informado",
    ) -> None:
        if not await self._pode_punir(
            ctx,
            membro,
            "expulsar",
        ):
            return

        try:
            await membro.kick(
                reason=f"{ctx.author} — {motivo}"
            )

        except discord.Forbidden:
            await responder(
                ctx,
                cartao_erro(
                    (
                        "Não tenho permissão para "
                        "expulsar esse membro."
                    )
                ),
            )

            return

        await responder(
            ctx,
            cartao_sucesso(
                "Membro expulso",
                (
                    f"**{membro}** foi removido do servidor.\n"
                    f"**Motivo:** {motivo}"
                ),
            ),
        )

    async def _pode_punir(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        acao: str,
    ) -> bool:
        """
        Verifica se o autor e o bot podem punir o membro.
        """

        if membro == ctx.guild.owner:
            await responder(
                ctx,
                cartao_erro(
                    f"O dono do servidor não pode ser {acao}."
                ),
            )

            return False

        if membro.top_role >= ctx.author.top_role:
            await responder(
                ctx,
                cartao_erro(
                    (
                        "Esse membro possui um cargo igual "
                        "ou superior ao seu cargo mais alto."
                    )
                ),
            )

            return False

        bot_membro = ctx.guild.me

        if (
            bot_membro is None
            or membro.top_role >= bot_membro.top_role
        ):
            await responder(
                ctx,
                cartao_erro(
                    (
                        "Meu cargo precisa estar acima do cargo "
                        "desse membro."
                    )
                ),
            )

            return False

        return True

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
        manage_channels=True
    )
    @commands.bot_has_permissions(
        manage_channels=True
    )
    async def trancar(
        self,
        ctx: commands.Context,
    ) -> None:
        permissao = ctx.channel.overwrites_for(
            ctx.guild.default_role
        )

        permissao.send_messages = False

        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=permissao,
            reason=f"Canal trancado por {ctx.author}",
        )

        await responder(
            ctx,
            cartao_sucesso(
                "Canal trancado",
                (
                    "Membros comuns não poderão enviar "
                    "mensagens até ele ser destrancado."
                ),
            ),
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
        manage_channels=True
    )
    @commands.bot_has_permissions(
        manage_channels=True
    )
    async def destrancar(
        self,
        ctx: commands.Context,
    ) -> None:
        permissao = ctx.channel.overwrites_for(
            ctx.guild.default_role
        )

        permissao.send_messages = None

        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=permissao,
            reason=f"Canal destrancado por {ctx.author}",
        )

        await responder(
            ctx,
            cartao_sucesso(
                "Canal destrancado",
                "O envio de mensagens foi liberado novamente.",
            ),
        )

    @commands.command(
        name="limpar",
        aliases=("clear",),
        extras={
            "categoria": "Moderação",
            "uso": ",limpar 20",
            "descricao": (
                "Apaga de 1 a 100 mensagens recentes."
            ),
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_messages=True
    )
    @commands.bot_has_permissions(
        manage_messages=True,
        read_message_history=True,
    )
    async def limpar(
        self,
        ctx: commands.Context,
        quantidade: int,
    ) -> None:
        if quantidade < 1 or quantidade > 100:
            await responder(
                ctx,
                cartao_erro(
                    "Escolha uma quantidade entre 1 e 100."
                ),
            )

            return

        apagadas = await ctx.channel.purge(
            limit=quantidade + 1
        )

        await responder(
            ctx,
            cartao_sucesso(
                "Limpeza concluída",
                (
                    f"{max(len(apagadas) - 1, 0)} mensagens "
                    "foram removidas deste canal."
                ),
            ),
        )

    @commands.command(
        name="aviso",
        extras={
            "categoria": "Moderação",
            "uso": ",aviso @membro motivo",
            "descricao": (
                "Registra uma advertência pública e organizada."
            ),
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_messages=True
    )
    async def aviso(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        *,
        motivo: str,
    ) -> None:
        await responder(
            ctx,
            cartao_sucesso(
                "Advertência registrada",
                (
                    f"{membro.mention} recebeu uma advertência.\n"
                    f"**Motivo:** {motivo}"
                ),
            ),
        )


async def setup(
    bot: commands.Bot,
) -> None:
    await bot.add_cog(
        Moderacao(bot)
    )
