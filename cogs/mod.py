from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Optional

import discord
from discord.ext import commands, tasks

from ._media import baixar_arquivo


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
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0") or "0")

EMOJI_INICIO = "<:axolote:1556443018557661234>"
EMOJI_FINAL = "<a:emoji_481:1556442987691647068>"

MOD_BANNER_URL = (
    "https://raw.githubusercontent.com/"
    "Jorginho077/Poyo/main/assets/Tumblr-l-67143811701311.gif"
)
MOD_BANNER_ATTACHMENT = "attachment://poyo-mod-banner.gif"


def analisar_duracao(texto: str) -> Optional[int]:
    correspondencia = DURACAO_RE.fullmatch(texto.lower().strip())
    if not correspondencia:
        return None

    valor = int(correspondencia.group("valor"))
    unidade = correspondencia.group("unidade").lower()
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
        quantidade, segundos = divmod(segundos, divisor)
        if quantidade:
            plural = "s" if quantidade != 1 else ""
            partes.append(f"{quantidade} {nome}{plural}")

    return ", ".join(partes)


class ModeracaoCartao(discord.ui.LayoutView):
    """Container V2 com banner dentro, acima e abaixo do texto."""

    def __init__(
        self,
        titulo: str,
        descricao: str,
        banner_url: str = MOD_BANNER_ATTACHMENT,
    ) -> None:
        super().__init__(timeout=None)

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(banner_url)
                ),
                discord.ui.TextDisplay(
                    f"{EMOJI_INICIO}  **{titulo}**  {EMOJI_FINAL}\n\n"
                    f"{descricao}"
                ),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(banner_url)
                ),
            )
        )


async def responder(
    ctx: commands.Context,
    titulo: str,
    texto: str,
) -> discord.Message:
    try:
        arquivo = await baixar_arquivo(
            MOD_BANNER_URL,
            "poyo-mod-banner.gif",
        )
    except (OSError, TimeoutError, discord.HTTPException):
        arquivo = None

    return await ctx.send(
        view=ModeracaoCartao(
            titulo,
            texto,
            MOD_BANNER_ATTACHMENT if arquivo is not None else MOD_BANNER_URL,
        ),
        file=arquivo,
        delete_after=TEMPO_DAS_RESPOSTAS,
    )


async def avisar_punicao(
    membro: discord.Member,
    texto: str,
) -> None:
    """Tenta avisar no privado sem impedir a punição."""
    try:
        await membro.send(texto)
    except (
        discord.Forbidden,
        discord.HTTPException,
        discord.NotFound,
    ):
        pass


class Moderacao(commands.Cog):
    """Comandos de moderação do servidor."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.calados: dict[int, dict[int, float]] = {}
        self.barreiras_calado: dict[
            int,
            dict[int, dict[int, Optional[discord.PermissionOverwrite]]],
        ] = {}
        self.banimentos_temporarios: dict[int, dict[int, float]] = {}
        self.verificar_calados.start()

    def cog_unload(self) -> None:
        self.verificar_calados.cancel()

    async def registrar_log(
        self,
        guild: discord.Guild,
        acao: str,
        detalhes: str,
    ) -> None:
        """Registra uma ação no canal configurado, sem quebrar a moderação."""
        if LOG_CHANNEL_ID <= 0:
            return

        canal = guild.get_channel(LOG_CHANNEL_ID)

        if canal is None or not hasattr(canal, "send"):
            return

        try:
            await canal.send(
                (
                    f"**{acao}**\n"
                    f"{detalhes}\n"
                    f"<t:{int(time.time())}:F>"
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (
            discord.Forbidden,
            discord.HTTPException,
            discord.NotFound,
        ):
            pass

    @tasks.loop(seconds=5)
    async def verificar_calados(self) -> None:
        """Libera calados e banimentos temporários expirados."""
        agora = time.time()

        for guild_id, membros in list(self.calados.items()):
            guild = self.bot.get_guild(guild_id)

            for membro_id, expiracao in list(membros.items()):
                if agora < expiracao:
                    continue

                membros.pop(membro_id, None)

                if guild is not None:
                    await self._remover_barreira_calado(guild, membro_id)
                    membro = guild.get_member(membro_id)

                    if membro is not None:
                        await avisar_punicao(
                            membro,
                            (
                                f"Seu calado no servidor "
                                f"**{guild.name}** terminou. "
                                "Você pode falar novamente."
                            ),
                        )
                        await self.registrar_log(
                            guild,
                            "CALADO ENCERRADO",
                            (
                                f"Membro: {membro} (`{membro.id}`)\n"
                                "O tempo terminou automaticamente."
                            ),
                        )

            if not membros:
                self.calados.pop(guild_id, None)

        for guild_id, banimentos in list(
            self.banimentos_temporarios.items()
        ):
            guild = self.bot.get_guild(guild_id)

            if guild is None:
                continue

            for membro_id, expiracao in list(banimentos.items()):
                if agora < expiracao:
                    continue

                try:
                    await guild.unban(
                        discord.Object(id=membro_id),
                        reason="Banimento temporário encerrado.",
                    )
                except discord.NotFound:
                    banimentos.pop(membro_id, None)
                    continue
                except (
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    continue

                banimentos.pop(membro_id, None)

                try:
                    usuario = self.bot.get_user(membro_id)

                    if usuario is None:
                        usuario = await self.bot.fetch_user(membro_id)

                    await avisar_punicao(
                        usuario,
                        (
                            f"Seu banimento temporário no servidor "
                            f"**{guild.name}** terminou. "
                            "Você pode voltar ao servidor."
                        ),
                    )
                    await self.registrar_log(
                        guild,
                        "BANIMENTO TEMPORÁRIO ENCERRADO",
                        (
                            f"Membro: {usuario} (`{membro_id}`)\n"
                            "O banimento terminou e o usuário foi desbanido."
                        ),
                    )
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    pass

            if not banimentos:
                self.banimentos_temporarios.pop(guild_id, None)

    @verificar_calados.before_loop
    async def aguardar_bot(self) -> None:
        await self.bot.wait_until_ready()

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()

        guild_calados = self.calados.get(ctx.guild.id, {})
        expiracao = guild_calados.get(ctx.author.id)

        if expiracao is not None:
            if time.time() < expiracao:
                # O membro calado não pode executar comandos do Poyo.
                raise commands.CheckFailure()

            guild_calados.pop(ctx.author.id, None)
            if not guild_calados:
                self.calados.pop(ctx.guild.id, None)

        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Apaga imediatamente mensagens novas de membros calados."""
        if message.guild is None or message.author.bot:
            return

        if not self._esta_calado(message.guild.id, message.author.id):
            return

        await self._apagar_mensagem_calada(message)

    @commands.Cog.listener()
    async def on_message_edit(
        self,
        antes: discord.Message,
        depois: discord.Message,
    ) -> None:
        """Apaga uma mensagem que foi editada enquanto o membro está calado."""
        if depois.guild is None or depois.author.bot:
            return

        if self._esta_calado(depois.guild.id, depois.author.id):
            await self._apagar_mensagem_calada(depois)

    def _esta_calado(self, guild_id: int, membro_id: int) -> bool:
        """Retorna se o calado ainda está ativo e limpa entradas vencidas."""
        guild_calados = self.calados.get(guild_id)
        if not guild_calados:
            return False

        expiracao = guild_calados.get(membro_id)
        if expiracao is None:
            return False

        if time.time() < expiracao:
            return True

        guild_calados.pop(membro_id, None)
        if not guild_calados:
            self.calados.pop(guild_id, None)
        return False

    async def _apagar_mensagem_calada(
        self,
        mensagem: discord.Message,
    ) -> None:
        """Tenta apagar a mensagem, incluindo uma tentativa após rate limit."""
        try:
            # PartialMessage.delete não aceita o parâmetro `reason`.
            await mensagem.delete()
        except discord.NotFound:
            # Ela já foi removida; o objetivo do mute foi cumprido.
            return
        except discord.Forbidden:
            # Sem Manage Messages, não há como apagar mensagens nesse canal.
            return
        except discord.HTTPException as erro:
            # Em spam, o Discord pode aplicar rate limit temporário.
            # Aguarda apenas quando a API informa um prazo seguro e tenta uma vez.
            retry_after = getattr(erro, "retry_after", None)
            if retry_after is None or retry_after > 3:
                return

            await asyncio.sleep(retry_after)
            try:
                await mensagem.delete()
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                return

    async def _aplicar_barreira_calado(
        self,
        guild: discord.Guild,
        membro: discord.Member,
    ) -> None:
        """Impede o envio em canais de texto enquanto o calado estiver ativo.

        O listener continua apagando mensagens como segunda camada. A barreira
        reduz a janela em que uma mensagem de spam poderia aparecer antes da
        exclusão e preserva o overwrite anterior de cada canal.
        """
        bot_membro = guild.me
        if bot_membro is None:
            return

        por_canal = self.barreiras_calado.setdefault(
            guild.id,
            {},
        ).setdefault(membro.id, {})

        canais = tuple(
            canal
            for canal in guild.channels
            if isinstance(
                canal,
                (discord.TextChannel, discord.ForumChannel),
            )
        )

        for canal in canais:
            permissao_bot = canal.permissions_for(bot_membro)
            if not permissao_bot.manage_channels:
                continue

            if canal.id not in por_canal:
                tinha_overwrite = any(
                    alvo.id == membro.id
                    for alvo in canal.overwrites
                )
                por_canal[canal.id] = (
                    canal.overwrites_for(membro)
                    if tinha_overwrite
                    else None
                )

            overwrite = canal.overwrites_for(membro)
            overwrite.send_messages = False
            overwrite.send_messages_in_threads = False

            try:
                await canal.set_permissions(
                    membro,
                    overwrite=overwrite,
                    reason="Barreira temporária de membro calado.",
                )
            except (
                discord.Forbidden,
                discord.HTTPException,
                discord.NotFound,
            ):
                continue

    async def _remover_barreira_calado(
        self,
        guild: discord.Guild,
        membro_id: int,
    ) -> None:
        """Restaura os overwrites que existiam antes do calado."""
        por_guild = self.barreiras_calado.get(guild.id)
        if not por_guild:
            return

        por_canal = por_guild.pop(membro_id, {})
        for canal_id, overwrite_anterior in por_canal.items():
            canal = guild.get_channel(canal_id)
            membro = guild.get_member(membro_id)
            if canal is None or membro is None:
                continue

            try:
                await canal.set_permissions(
                    membro,
                    overwrite=overwrite_anterior,
                    reason="Fim do calado; permissões restauradas.",
                )
            except (
                discord.Forbidden,
                discord.HTTPException,
                discord.NotFound,
            ):
                continue

        if not por_guild:
            self.barreiras_calado.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        membro: discord.Member,
        antes: discord.VoiceState,
        depois: discord.VoiceState,
    ) -> None:
        """Desconecta membros calados que estejam ou tentem entrar em call."""
        guild_calados = self.calados.get(membro.guild.id, {})
        expiracao = guild_calados.get(membro.id)

        if expiracao is None:
            return

        if time.time() >= expiracao:
            guild_calados.pop(membro.id, None)
            if not guild_calados:
                self.calados.pop(membro.guild.id, None)
            await self._remover_barreira_calado(membro.guild, membro.id)
            return

        if depois.channel is None:
            return

        try:
            await membro.move_to(
                None,
                reason="Membro calado não pode permanecer em call.",
            )
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    @commands.hybrid_command(
        name="calado",
        description="Impede mensagens e voz durante o tempo informado.",
        aliases=("silenciar",),
        extras={
            "categoria": "Moderação",
            "uso": ",calado @membro 1h",
            "descricao": "Impede mensagens durante o tempo informado.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(
        manage_messages=True,
        manage_channels=True,
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

        if segundos is None or not 1 <= segundos <= 28 * 86400:
            await responder(ctx, "Tempo inválido", "Use `1m`, `1h` ou `28d`.")
            return

        if membro == ctx.author:
            await responder(ctx, "Ação negada", "Você não pode se calar.")
            return

        if membro == ctx.guild.owner:
            await responder(ctx, "Ação negada", "O dono não pode ser calado.")
            return

        if membro.top_role >= ctx.author.top_role:
            await responder(ctx, "Ação negada", "Cargo acima do seu.")
            return

        bot_membro = ctx.guild.me
        if bot_membro is None or membro.top_role >= bot_membro.top_role:
            await responder(ctx, "Ação negada", "Meu cargo precisa estar acima do dele.")
            return

        self.calados.setdefault(ctx.guild.id, {})[membro.id] = time.time() + segundos
        await self._aplicar_barreira_calado(ctx.guild, membro)

        await avisar_punicao(
            membro,
            (
                f"Você foi calado no servidor **{ctx.guild.name}** "
                f"por **{formatar_duracao(segundos)}**."
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
                await responder(
                    ctx,
                    "Call não removida",
                    "Dê ao Poyo `Mover membros` nesta call.",
                )
                return

        await responder(
            ctx,
            "Calado com sucesso",
            f"{membro.mention} não pode falar por **{formatar_duracao(segundos)}**.",
        )

        await self.registrar_log(
            ctx.guild,
            "MEMBRO CALADO",
            (
                f"Moderador: {ctx.author} (`{ctx.author.id}`)\n"
                f"Membro: {membro} (`{membro.id}`)\n"
                f"Duração: {formatar_duracao(segundos)}"
            ),
        )

    @commands.hybrid_command(
        name="nchoraxx",
        description="Libera um membro que está calado.",
        aliases=("rcalado", "rsilenciar"),
        extras={
            "categoria": "Moderação",
            "uso": ",nchoraxx @membro",
            "descricao": "Libera um membro calado.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(
        manage_messages=True,
        manage_channels=True,
    )
    async def nchoraxx(self, ctx: commands.Context, membro: discord.Member) -> None:
        guild_calados = self.calados.get(ctx.guild.id, {})

        if membro.id not in guild_calados:
            await responder(ctx, "Ação negada", f"{membro.mention} não está calado.")
            return

        guild_calados.pop(membro.id, None)
        await self._remover_barreira_calado(ctx.guild, membro.id)
        if not guild_calados:
            self.calados.pop(ctx.guild.id, None)

        await avisar_punicao(
            membro,
            (
                f"Seu calado no servidor **{ctx.guild.name}** "
                "foi removido. Você pode falar novamente."
            ),
        )

        await responder(ctx, "Calado removido", f"{membro.mention} pode falar novamente.")

        await self.registrar_log(
            ctx.guild,
            "CALADO REMOVIDO",
            (
                f"Moderador: {ctx.author} (`{ctx.author.id}`)\n"
                f"Membro: {membro} (`{membro.id}`)"
            ),
        )

    @commands.hybrid_command(
        name="sai",
        description="Bane um membro permanentemente ou por tempo definido.",
        aliases=("banir",),
        extras={
            "categoria": "Moderação",
            "uso": ",sai @membro [tempo]",
            "descricao": "Bane permanentemente ou por tempo definido.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def sai(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        tempo: Optional[str] = None,
        *,
        motivo: str = "Nenhum motivo informado",
    ) -> None:
        if not await self._pode_punir(ctx, membro, "remover"):
            return

        segundos = None

        if tempo is not None:
            segundos = analisar_duracao(tempo)

            if segundos is None or segundos < 1:
                await responder(
                    ctx,
                    "Tempo inválido",
                    "Use `,sai @membro 7d` ou deixe sem tempo para ser permanente.",
                )
                return

        texto_tempo = (
            f" por **{formatar_duracao(segundos)}**"
            if segundos is not None
            else " permanentemente"
        )

        await avisar_punicao(
            membro,
            (
                f"Você foi banido do servidor **{ctx.guild.name}**"
                f"{texto_tempo}."
            ),
        )

        try:
            await membro.ban(
                reason=f"{ctx.author} — {motivo}",
                delete_message_seconds=86400,
            )
        except discord.Forbidden:
            await responder(ctx, "Ação negada", "Não posso remover esse membro.")
            return

        if segundos is not None:
            self.banimentos_temporarios.setdefault(
                ctx.guild.id,
                {},
            )[membro.id] = time.time() + segundos

        await responder(
            ctx,
            "Membro banido",
            (
                f"**{membro}** foi banido"
                f"{texto_tempo}."
            ),
        )

        await self.registrar_log(
            ctx.guild,
            "MEMBRO BANIDO",
            (
                f"Moderador: {ctx.author} (`{ctx.author.id}`)\n"
                f"Membro: {membro} (`{membro.id}`)\n"
                f"Tipo: {'temporário' if segundos is not None else 'permanente'}\n"
                f"Duração: {formatar_duracao(segundos) if segundos is not None else 'permanente'}\n"
                f"Motivo: {motivo}"
            ),
        )

    @commands.hybrid_command(
        name="rban",
        description="Remove o banimento de um usuário.",
        extras={
            "categoria": "Moderação",
            "uso": ",rban ID",
            "descricao": "Remove um banimento pelo ID.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def rban(self, ctx: commands.Context, usuario_id: int) -> None:
        try:
            banimento = await ctx.guild.fetch_ban(discord.Object(id=usuario_id))
            await ctx.guild.unban(
                banimento.user,
                reason=f"Banimento removido por {ctx.author}",
            )
        except discord.NotFound:
            await responder(ctx, "Ação negada", "Esse ID não está banido.")
            return
        except discord.Forbidden:
            await responder(ctx, "Ação negada", "Não posso remover esse banimento.")
            return

        await responder(ctx, "Banimento removido", f"**{banimento.user}** pode voltar.")

        await self.registrar_log(
            ctx.guild,
            "BANIMENTO REMOVIDO",
            (
                f"Moderador: {ctx.author} (`{ctx.author.id}`)\n"
                f"Usuário: {banimento.user} (`{banimento.user.id}`)"
            ),
        )

    @commands.hybrid_command(
        name="expulsar",
        description="Expulsa um membro do servidor.",
        aliases=("kick",),
        extras={
            "categoria": "Moderação",
            "uso": ",expulsar @membro",
            "descricao": "Expulsa um membro do servidor.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def expulsar(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        *,
        motivo: str = "Nenhum motivo informado",
    ) -> None:
        if not await self._pode_punir(ctx, membro, "expulsar"):
            return

        await avisar_punicao(
            membro,
            f"Você foi expulso do servidor **{ctx.guild.name}**.",
        )

        try:
            await membro.kick(reason=f"{ctx.author} — {motivo}")
        except discord.Forbidden:
            await responder(ctx, "Ação negada", "Não posso expulsar esse membro.")
            return

        await responder(ctx, "Membro expulso", f"**{membro}** foi expulso.")

        await self.registrar_log(
            ctx.guild,
            "MEMBRO EXPULSO",
            (
                f"Moderador: {ctx.author} (`{ctx.author.id}`)\n"
                f"Membro: {membro} (`{membro.id}`)\n"
                f"Motivo: {motivo}"
            ),
        )

    async def _pode_punir(
        self,
        ctx: commands.Context,
        membro: discord.Member,
        acao: str,
    ) -> bool:
        if membro == ctx.guild.owner:
            await responder(ctx, "Ação negada", f"O dono não pode ser {acao}.")
            return False

        if membro.top_role >= ctx.author.top_role:
            await responder(ctx, "Ação negada", "Cargo acima do seu.")
            return False

        bot_membro = ctx.guild.me
        if bot_membro is None or membro.top_role >= bot_membro.top_role:
            await responder(ctx, "Ação negada", "Meu cargo precisa estar acima do dele.")
            return False

        return True

    @commands.hybrid_command(
        name="trancar",
        description="Tranca o canal atual para mensagens.",
        extras={
            "categoria": "Moderação",
            "uso": ",trancar",
            "descricao": "Tranca o canal atual.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def trancar(self, ctx: commands.Context) -> None:
        permissao = ctx.channel.overwrites_for(ctx.guild.default_role)
        permissao.send_messages = False

        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=permissao,
            reason=f"Canal trancado por {ctx.author}",
        )

        await responder(ctx, "Canal trancado", "Canal fechado.")

        await self.registrar_log(
            ctx.guild,
            "CANAL TRANCADO",
            (
                f"Moderador: {ctx.author} (`{ctx.author.id}`)\n"
                f"Canal: {ctx.channel.mention} (`{ctx.channel.id}`)"
            ),
        )

    @commands.hybrid_command(
        name="destrancar",
        description="Destranca o canal atual para mensagens.",
        extras={
            "categoria": "Moderação",
            "uso": ",destrancar",
            "descricao": "Destranca o canal atual.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def destrancar(self, ctx: commands.Context) -> None:
        permissao = ctx.channel.overwrites_for(ctx.guild.default_role)
        permissao.send_messages = None

        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=permissao,
            reason=f"Canal destrancado por {ctx.author}",
        )

        await responder(ctx, "Canal destrancado", "Canal liberado.")

        await self.registrar_log(
            ctx.guild,
            "CANAL DESTRANCADO",
            (
                f"Moderador: {ctx.author} (`{ctx.author.id}`)\n"
                f"Canal: {ctx.channel.mention} (`{ctx.channel.id}`)"
            ),
        )

    @commands.hybrid_command(
        name="limpar",
        description="Apaga uma quantidade de mensagens do canal.",
        aliases=("clear",),
        extras={
            "categoria": "Moderação",
            "uso": ",limpar 20",
            "descricao": "Apaga de 1 a 100 mensagens.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(
        manage_messages=True,
        read_message_history=True,
    )
    async def limpar(self, ctx: commands.Context, quantidade: int) -> None:
        if not 1 <= quantidade <= 100:
            await responder(ctx, "Ação negada", "Use um número entre 1 e 100.")
            return

        apagadas = await ctx.channel.purge(limit=quantidade + 1)
        total = max(len(apagadas) - 1, 0)
        await responder(ctx, "Limpeza concluída", f"{total} mensagens apagadas.")

        await self.registrar_log(
            ctx.guild,
            "MENSAGENS APAGADAS",
            (
                f"Moderador: {ctx.author} (`{ctx.author.id}`)\n"
                f"Canal: {ctx.channel.mention} (`{ctx.channel.id}`)\n"
                f"Quantidade: {total}"
            ),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderacao(bot))
