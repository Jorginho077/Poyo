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


def analisar_duracao(texto: str) -> Optional[int]:
    resultado = DURACAO_RE.fullmatch(
        texto.lower().strip()
    )

    if not resultado:
        return None

    valor = int(resultado.group("valor"))
    unidade = resultado.group("unidade").lower()

    return valor * UNIDADES[unidade]


def formatar_duracao(segundos: int) -> str:
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
                f"{quantidade} {nome}{plural}"
            )

    return ", ".join(partes)


class Cartao(discord.ui.LayoutView):
    def __init__(
        self,
        titulo: str,
        descricao: str,
    ) -> None:
        super().__init__(timeout=None)

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(BANNER_URL)
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
                    discord.MediaGalleryItem(BANNER_URL)
                ),
            )
        )


async def responder(
    ctx: commands.Context,
    titulo: str,
    texto: str,
) -> discord.Message:
    return await ctx.send(
        view=Cartao(titulo, texto),
        delete_after=TEMPO_DAS_RESPOSTAS,
    )


async def avisar_punicao(
    membro: discord.Member,
    texto: str,
) -> None:
    """
    Tenta enviar o aviso no privado.
    Se a DM estiver fechada, a punição continua normalmente.
    """

    try:
        await membro.send(texto)

    except (
        discord.Forbidden,
        discord.HTTPException,
        discord.NotFound,
    ):
        pass


class Moderacao(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
    ) -> None:
        self.bot = bot

        self.calados: dict[
            int,
            dict[int, float],
        ] = {}

    def _calado_ate(
        self,
        guild_id: int,
        membro_id: int,
    ) -> Optional[float]:
        return self.calados.get(
            guild_id,
            {},
        ).get(membro_id)

    def _limpar_calado_expirado(
        self,
        guild_id: int,
        membro_id: int,
    ) -> None:
        membros = self.calados.get(
            guild_id,
            {},
        )

        membros.pop(
            membro_id,
            None,
        )

        if not membros:
            self.calados.pop(
                guild_id,
                None,
            )

    async def cog_check(
        self,
        ctx: commands.Context,
    ) -> bool:
        """
        Impede qualquer pessoa calada de usar
        comandos, inclusive administradores.
        """

        if ctx.guild is None:
            raise commands.NoPrivateMessage()

        expiracao = self._calado_ate(
            ctx.guild.id,
            ctx.author.id,
        )

        if expiracao is None:
            return True

        if time.time() < expiracao:
            raise commands.CheckFailure()

        self._limpar_calado_expirado(
            ctx.guild.id,
            ctx.author.id,
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

        expiracao = self._calado_ate(
            message.guild.id,
            message.author.id,
        )

        if expiracao is None:
            return

        if time.time() >= expiracao:
            self._limpar_calado_expirado(
                message.guild.id,
                message.author.id,
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
        Remove o membro calado da call e impede
        que ele entre novamente durante o período.
        """

        expiracao = self._calado_ate(
            membro.guild.id,
            membro.id,
        )

        if expiracao is None:
            return

        if time.time() >= expiracao:
            self._limpar_calado_expirado(
                membro.guild.id,
                membro.id,
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
        aliases=("silenciar",),
        extras={
            "categoria": "Moderação",
            "uso": ",calado @membro 1h",
            "descricao": "Apaga as mensagens durante o tempo informado.",
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
        segundos = analisar_duracao(tempo)

        if (
            segundos is None
            or segundos < 1
            or segundos > 28 * 86400
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
                "O cargo dele é igual ou superior ao seu.",
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
        )[membro.id] = time.time() + segundos

        await avisar_punicao(
            membro,
            (
                f"Você foi calado no servidor "
                f"**{ctx.guild.name}** por "
                f"**{formatar_duracao(segundos)}**."
            ),
        )

        if membro.voice is not None:
            try:
                await membro.move_to(
                    None,
                    reason="Membro calado removido da call.",
                )

            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                pass

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
        aliases=("rcalado", "rsilenciar"),
        extras={
            "categoria": "Moderação",
            "uso": ",nchoraxx @membro",
            "descricao": "Remove o calado de um membro.",
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
        membros = self.calados.get(
            ctx.guild.id,
            {},
        )

        if membro.id not in membros:
            await responder(
                ctx,
                "Ação negada",
                f"{membro.mention} não está calado.",
            )
            return

        self._limpar_calado_expirado(
            ctx.guild.id,
            membro.id,
        )

        await responder(
            ctx,
            "Calado removido",
            f"{membro.mention} pode falar novamente.",
        )

    @commands.command(
        name="sai",
        aliases=("banir",),
        extras={
            "categoria": "Moderação",
            "uso": ",sai @membro",
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
            "banido",
        ):
            return

        await avisar_punicao(
            membro,
            (
                f"Você foi banido do servidor "
                f"**{ctx.guild.name}**."
            ),
        )

        try:
            await membro.ban(
                reason=f"{ctx.author} — {motivo}",
                delete_message_seconds=86400,
            )

        except discord.Forbidden:
            await responder(
                ctx,
                "Ação negada",
                "Não posso banir esse membro.",
            )
            return

        await responder(
            ctx,
            "Membro banido",
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
        aliases=("kick",),
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
            "expulso",
        ):
            return

        await avisar_punicao(
            membro,
            (
                f"Você foi expulso do servidor "
                f"**{ctx.guild.name}**."
            ),
        )

        try:
            await membro.kick(
                reason=f"{ctx.author} — {motivo}",
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
                "O cargo dele é igual ou superior ao seu.",
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
            reason=f"Canal trancado por {ctx.author}",
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
            reason=f"Canal destrancado por {ctx.author}",
        )

        await responder(
            ctx,
            "Canal destrancado",
            "Canal liberado.",
        )

    @commands.command(
        name="limpar",
        aliases=("clear",),
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
        if quantidade < 1 or quantidade > 100:
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
