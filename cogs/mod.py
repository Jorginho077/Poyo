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


TEMPO_DAS_RESPOSTAS = 25

EMOJI_INICIO = "<:axolote:1556443018557661234>"
EMOJI_FINAL = "<a:emoji_481:1556442987691647068>"

BANNER_URL = (
    "https://cdn.discordapp.com/attachments/"
    "1556052693511053397/1556449326891401307/"
    "GIF_image_3.gif?backend=b2&ex=6ac433e4&"
    "is=6ac2e264&hm=7bd09ac806e541b3edadc60c2c796f17"
    "dfcffe12b617209c6a735eb7e741ce9e&"
)


def analisar_duracao(
    texto: str,
) -> Optional[int]:
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

    for nome, divisor in (
        ("semana", 604800),
        ("dia", 86400),
        ("hora", 3600),
        ("minuto", 60),
        ("segundo", 1),
    ):
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


class Cartao(discord.ui.LayoutView):
    """Container V2 com banner, texto e banner."""

    def __init__(
        self,
        titulo: str,
        descricao: str,
    ) -> None:
        super().__init__(
            timeout=None
        )

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(
                        BANNER_URL
                    )
                ),

                discord.ui.TextDisplay(
                    (
                        f"{EMOJI_INICIO}  "
                        f"**{titulo}**  "
                        f"{EMOJI_FINAL}\n\n"
                        f"{descricao}"
                    )
                ),

                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(
                        BANNER_URL
                    )
                ),
            )
        )


async def responder(
    ctx: commands.Context,
    titulo: str,
    texto: str,
) -> discord.Message:
    return await ctx.send(
        view=Cartao(
            titulo,
            texto,
        ),
        delete_after=TEMPO_DAS_RESPOSTAS,
    )


class Moderacao(commands.Cog):
    """Comandos de moderação do servidor."""

    def __init__(
        self,
        bot: commands.Bot,
    ) -> None:
        self.bot = bot

        self.calados: dict[
            int,
            dict[int, float],
        ] = {}

    async def cog_check(
        self,
        ctx: commands.Context,
    ) -> bool:
        """
        Impede membros calados de usar
        qualquer comando do Poyo.
        """

        if ctx.guild is None:
            raise commands.NoPrivateMessage()

        guild_calados = self.calados.get(
            ctx.guild.id,
            {},
        )

        expiracao = guild_calados.get(
            ctx.author.id,
        )

        if expiracao is not None:
            if time.time() < expiracao:
                raise commands.CheckFailure()

            guild_calados.pop(
                ctx.author.id,
                None,
            )

            if not guild_calados:
                self.calados.pop(
                    ctx.guild.id,
                    None,
                )

        return True

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ) -> None:
        """
        Apaga mensagens de membros calados.
        """

        if message.guild is None:
            return

        if message.author.bot:
            return

        guild_calados = self.calados.get(
            message.guild.id,
            {},
        )

        expiracao = guild_calados.get(
            message.author.id,
        )

        if expiracao is None:
            return

        if time.time() >= expiracao:
            guild_calados.pop(
                message.author.id,
                None,
            )

            if not guild_calados:
                self.calados.pop(
                    message.guild.id,
                    None,
                )

            return

        try:
            await message.delete()

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        membro: discord.Member,
        antes: discord.VoiceState,
        depois: discord.VoiceState,
    ) -> None:
        """
        Remove membros calados da call.
        Também impede que entrem novamente.
        """

        guild_calados = self.calados.get(
            membro.guild.id,
            {},
        )

        expiracao = guild_calados.get(
            membro.id,
        )

        if expiracao is None:
            return

        if time.time() >= expiracao:
            guild_calados.pop(
                membro.id,
                None,
            )

            if not guild_calados:
                self.calados.pop(
                    membro.guild.id,
                    None,
                )

            return

        if depois.channel is None:
            return

        try:
            await membro.move_to(
                None,
                reason=(
                    "Membro calado não pode "
                    "permanecer em call."
                ),
            )

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    @commands.command(
        name="calado",
        aliases=(
            "silenciar",
        ),
        extras={
            "categoria": "Moderação",
            "uso": ",calado @membro 1h",
            "descricao": (
                "Impede mensagens durante "
                "o tempo informado."
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
    async def calado(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        tempo: str,
        *,
        motivo: str = "Nenhum motivo informado",
    ) -> None:
        segundos = analisar_duracao(
            tempo,
        )

        if (
            segundos is None
            or not 1 <= segundos <= 28 * 86400
        ):
            await responder(
                ctx,
                "Tempo inválido",
                "Use `1m`, `1h` ou `28d`.",
            )

            return

        if membro == ctx.author:
            await responder(
                ctx,
                "Ação negada",
                "Você não pode se calar.",
            )

            return

        if membro == ctx.guild.owner:
            await responder(
                ctx,
                "Ação negada",
                "O dono não pode ser calado.",
            )

            return

        if membro.top_role >= ctx.author.top_role:
            await responder(
                ctx,
                "Ação negada",
                "Cargo acima do seu.",
            )

            return

        bot_membro = ctx.guild.me

        if (
            bot_membro is None
            or membro.top_role >= bot_membro.top_role
        ):
            await responder(
                ctx,
                "Ação negada",
                "Meu cargo precisa estar acima do dele.",
            )

            return

        self.calados.setdefault(
            ctx.guild.id,
            {},
        )[membro.id] = (
            time.time() + segundos
        )

        if membro.voice is not None:
            try:
                await membro.move_to(
                    None,
                    reason=(
                        "Membro calado removido "
                        "da call."
                    ),
                )

            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                await responder(
                    ctx,
                    "Call não removida",
                    "Dê ao Poyo `Mover membros` nesta call.",
                )

                return

        await responder(
            ctx,
            "Calado com sucesso",
            (
                f"{membro.mention} não pode falar por "
                f"**{formatar_duracao(segundos)}**."
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
            "descricao": "Libera um membro calado.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        manage_messages=True,
    )
    @commands.bot_has_permissions(
        manage_messages=True,
    )
    async def nchoraxx(
        self,
        ctx: commands.Context,
        membro: discord.Member,
    ) -> None:
        guild_calados = self.calados.get(
            ctx.guild.id,
            {},
        )

        if membro.id not in guild_calados:
            await responder(
                ctx,
                "Ação negada",
                f"{membro.mention} não está calado.",
            )

            return

        guild_calados.pop(
            membro.id,
            None,
        )

        if not guild_calados:
            self.calados.pop(
                ctx.guild.id,
                None,
            )

        await responder(
            ctx,
            "Calado removido",
            f"{membro.mention} pode falar novamente.",
        )

    @commands.command(
        name="sai",
        aliases=(
            "banir",
        ),
        extras={
            "categoria": "Moderação",
            "uso": ",sai @membro",
            "descricao": "Remove um membro do servidor.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(
        ban_members=True,
    )
    @commands.bot_has_permissions(
        ban_members=True,
    )
    async def sai(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        *,
        motivo: str = "Nenhum motivo informado",
    ) -> None:
        if not await self._pode_punir(
            ctx,
            membro,
            "remover",
        ):
            return

        try:
            await membro.ban(
                reason=(
                    f"{ctx.author} — {motivo}"
                ),
                delete_message_seconds=86400,
            )

        except discord.Forbidden:
            await responder(
                ctx,
                "Ação negada",
                "Não posso remover esse membro.",
            )

            return

        await responder(
            ctx,
            "Membro removido",
            f"**{membro}** foi banido.",
        )

    @commands.command(
        name="rban",
        extras={
            "categoria": "Moderação",
            "uso": ",rban ID",
            "descricao": "Remove um banimento pelo ID.",
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
    ) -> None:
        try:
            banimento = await ctx.guild.fetch_ban(
                discord.Object(
                    id=usuario_id,
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
                "Ação negada",
                "Esse ID não está banido.",
            )

            return

        except discord.Forbidden:
            await responder(
                ctx,
                "Ação negada",
                "Não posso remover esse banimento.",
            )

            return

        await responder(
            ctx,
            "Banimento removido",
            f"**{banimento.user}** pode voltar.",
        )

    @commands.command(
        name="expulsar",
        aliases=(
            "kick",
        ),
        extras={
            "categoria": "Moderação",
            "uso": ",expulsar @membro",
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
    ) -> None:
        if not await self._pode_punir(
            ctx,
            membro,
            "expulsar",
        ):
            return

        try:
            await membro.kick(
                reason=(
                    f"{ctx.author} — {motivo}"
                ),
            )

        except discord.Forbidden:
            await responder(
                ctx,
                "Ação negada",
                "Não posso expulsar esse membro.",
            )

            return

        await responder(
            ctx,
            "Membro expulso",
            f"**{membro}** foi expulso.",
        )

    async def _pode_punir(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        acao: str,
    ) -> bool:
        if membro == ctx.guild.owner:
            await responder(
                ctx,
                "Ação negada",
                f"O dono não pode ser {acao}.",
            )

            return False

        if membro.top_role >= ctx.author.top_role:
            await responder(
                ctx,
                "Ação negada",
                "Cargo acima do seu.",
            )

            return False

        bot_membro = ctx.guild.me

        if (
            bot_membro is None
            or membro.top_role >= bot_membro.top_role
        ):
            await responder(
                ctx,
                "Ação negada",
                "Meu cargo precisa estar acima do dele.",
            )

            return False

        return True

    @commands.command(
        name="trancar",
        extras={
            "categoria": "Moderação",
            "uso": ",trancar",
            "descricao": "Tranca o canal atual.",
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
    ) -> None:
        permissao = ctx.channel.overwrites_for(
            ctx.guild.default_role,
        )

        permissao.send_messages = False

        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=permissao,
            reason=(
                f"Canal trancado por "
                f"{ctx.author}"
            ),
        )

        await responder(
            ctx,
            "Canal trancado",
            "Canal fechado.",
        )

    @commands.command(
        name="destrancar",
        extras={
            "categoria": "Moderação",
            "uso": ",destrancar",
            "descricao": "Destranca o canal atual.",
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
    ) -> None:
        permissao = ctx.channel.overwrites_for(
            ctx.guild.default_role,
        )

        permissao.send_messages = None

        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=permissao,
            reason=(
                f"Canal destrancado por "
                f"{ctx.author}"
            ),
        )

        await responder(
            ctx,
            "Canal destrancado",
            "Canal liberado.",
        )

    @commands.command(
        name="limpar",
        aliases=(
            "clear",
        ),
        extras={
            "categoria": "Moderação",
            "uso": ",limpar 20",
            "descricao": "Apaga de 1 a 100 mensagens.",
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
    ) -> None:
        if not 1 <= quantidade <= 100:
            await responder(
                ctx,
                "Ação negada",
                "Use um número entre 1 e 100.",
            )

            return

        apagadas = await ctx.channel.purge(
            limit=quantidade + 1,
        )

        total = max(
            len(apagadas) - 1,
            0,
        )

        await responder(
            ctx,
            "Limpeza concluída",
            f"{total} mensagens apagadas.",
        )


async def setup(
    bot: commands.Bot,
) -> None:
    await bot.add_cog(
        Moderacao(bot)
    )
