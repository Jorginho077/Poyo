# cog de musica do poyo - sem youtube: soundcloud, bandcamp, links diretos e radios
# painel de configuracao: ,pmconfig (caixa de selecao minimalista)
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ._musica_config import ConfigMusica
from ._musica_fontes import (
    Faixa,
    FonteDesligada,
    FonteNaoSuportada,
    NadaEncontrado,
    buscar,
)
from ._musica_player import PlayerGuild

log = logging.getLogger("bot.musica")

NO_MENTIONS = discord.AllowedMentions.none()

EMOJI_FONTE = {
    "soundcloud": "🟧",
    "bandcamp": "🟦",
    "direto": "🔗",
    "radio": "📻",
    "desconhecida": "🎵",
}


def _cartao(*blocos: str) -> discord.ui.LayoutView:
    """Container V2 simples, no estilo do bot."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(*(discord.ui.TextDisplay(b) for b in blocos))
    )
    return view


async def _responder(
    ctx: commands.Context,
    *blocos: str,
    ephemeral: bool = False,
    view: discord.ui.LayoutView | None = None,
) -> None:
    if view is None:
        view = _cartao(*blocos)
    if ctx.interaction is not None:
        if ctx.interaction.response.is_done():
            await ctx.interaction.followup.send(view=view, ephemeral=ephemeral)
        else:
            await ctx.interaction.response.send_message(view=view, ephemeral=ephemeral)
    else:
        await ctx.send(view=view, allowed_mentions=NO_MENTIONS)


# ------------------------------------------------------------------ painel

class PainelMusicaSelect(discord.ui.Select):
    """Caixa de selecao low profile: cada opcao liga/desliga um ajuste."""

    def __init__(self, cog: "Musica", guild_id: int) -> None:
        self.cog = cog
        self.guild_id = guild_id
        cfg = cog.config.obter(guild_id)

        def marca(ligado: bool) -> str:
            return "✅" if ligado else "⬜"

        opcoes = [
            discord.SelectOption(
                label="Modo Slash",
                value="modo_slash",
                description="Usar comandos / em vez de comandos com ,",
                emoji=marca(cfg["modo_slash"]),
            ),
            discord.SelectOption(
                label="SoundCloud",
                value="soundcloud",
                description="Permitir buscas e links do SoundCloud",
                emoji=marca(cfg["soundcloud"]),
            ),
            discord.SelectOption(
                label="Bandcamp",
                value="bandcamp",
                description="Permitir buscas e links do Bandcamp",
                emoji=marca(cfg["bandcamp"]),
            ),
            discord.SelectOption(
                label="Modo 24/7",
                value="modo_247",
                description="Ficar no canal de voz mesmo com a fila vazia",
                emoji=marca(cfg["modo_247"]),
            ),
            discord.SelectOption(
                label="Cargo de DJ",
                value="dj_role",
                description="Definir ou remover o cargo que controla a música",
                emoji="🎧",
            ),
        ]
        super().__init__(
            placeholder="Toque em uma opção para ligar ou desligar…",
            min_values=1,
            max_values=1,
            options=opcoes,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        escolha = self.values[0]

        if escolha == "dj_role":
            await interaction.response.send_message(
                view=_cartao(
                    "## Cargo de DJ",
                    "Use **`pmconfig dj @cargo`** para definir o cargo de DJ "
                    "ou **`pmconfig dj off`** para desligar.",
                ),
                ephemeral=True,
            )
            return

        cfg = self.cog.config.obter(self.guild_id)
        novo = not cfg[escolha]
        cfg = self.cog.config.definir(self.guild_id, escolha, novo)

        nomes = {
            "modo_slash": "Modo Slash",
            "soundcloud": "SoundCloud",
            "bandcamp": "Bandcamp",
            "modo_247": "Modo 24/7",
        }
        await interaction.response.edit_message(view=painel_musica_view(self.cog, self.guild_id))
        await interaction.followup.send(
            view=_cartao(f"**{nomes[escolha]}** agora está **{'ligado' if novo else 'desligado'}**."),
            ephemeral=True,
        )


def painel_musica_view(cog: "Musica", guild_id: int) -> discord.ui.LayoutView:
    cfg = cog.config.obter(guild_id)

    def estado(ligado: bool) -> str:
        return "ligado" if ligado else "desligado"

    dj = f"<@&{cfg['dj_role']}>" if cfg["dj_role"] else "desligado"
    modo = "Slash (`/`)" if cfg["modo_slash"] else "Prefixo (`,`)"
    resumo = (
        "## Painel de música\n"
        f"-# Modo de comandos: **{modo}**\n"
        f"-# SoundCloud: **{estado(cfg['soundcloud'])}** · "
        f"Bandcamp: **{estado(cfg['bandcamp'])}**\n"
        f"-# 24/7: **{estado(cfg['modo_247'])}** · DJ: **{dj}** · "
        f"Volume: **{cfg['volume']}%**"
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(resumo)))
    view.add_item(discord.ui.ActionRow(PainelMusicaSelect(cog, guild_id)))
    return view


# ------------------------------------------------------------------ cog

class Musica(commands.Cog, name="Musica"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = ConfigMusica()
        self.players: dict[int, PlayerGuild] = {}
        self.canais_aviso: dict[int, int] = {}  # guild -> canal de texto do ultimo comando

    # ------------------------------------------------------------ helpers

    def player(self, guild_id: int) -> PlayerGuild:
        if guild_id not in self.players:
            self.players[guild_id] = PlayerGuild(self, guild_id)
        return self.players[guild_id]

    def _cfg(self, ctx: commands.Context) -> dict:
        return self.config.obter(ctx.guild.id)

    def _lembrar_canal(self, ctx: commands.Context) -> None:
        self.canais_aviso[ctx.guild.id] = ctx.channel.id

    async def _canal_aviso(self, guild_id: int) -> discord.abc.Messageable | None:
        canal_id = self.canais_aviso.get(guild_id)
        if canal_id is None:
            return None
        return self.bot.get_channel(canal_id)

    async def aviso_tocando(self, guild_id: int, faixa: Faixa) -> None:
        canal = await self._canal_aviso(guild_id)
        if canal is None:
            return
        emoji = EMOJI_FONTE.get(faixa.fonte, "🎵")
        titulo = f"[{faixa.titulo}]({faixa.url_pagina})" if faixa.url_pagina else faixa.titulo
        try:
            await canal.send(
                view=_cartao(
                    f"## {emoji} Tocando agora",
                    f"**{titulo}** · `{faixa.duracao_txt}`\n"
                    f"-# Pedido por {faixa.pedido_por or 'desconhecido'}",
                ),
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            pass

    async def aviso_falha(self, guild_id: int, faixa: Faixa) -> None:
        canal = await self._canal_aviso(guild_id)
        if canal is None:
            return
        try:
            await canal.send(
                view=_cartao(f"Não consegui tocar **{faixa.titulo}**. Pulando para a próxima."),
                allowed_mentions=NO_MENTIONS,
                delete_after=15,
            )
        except discord.HTTPException:
            pass

    def _e_dj_ou_staff(self, ctx: commands.Context) -> bool:
        cfg = self._cfg(ctx)
        if not cfg["dj_role"]:
            return True
        autor = ctx.author
        if autor.guild_permissions.manage_guild:
            return True
        return any(cargo.id == cfg["dj_role"] for cargo in autor.roles)

    async def _mesmo_canal(self, ctx: commands.Context) -> bool:
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await _responder(ctx, "Entre em um canal de voz primeiro.")
            return False
        voz = ctx.guild.voice_client
        if voz and voz.channel != ctx.author.voice.channel:
            await _responder(ctx, "Você precisa estar no mesmo canal de voz que eu.")
            return False
        return True

    async def _conectar(self, ctx: commands.Context) -> discord.VoiceClient | None:
        canal = ctx.author.voice.channel
        voz = ctx.guild.voice_client
        try:
            if voz is None:
                return await canal.connect(self_deaf=True)
            if voz.channel != canal:
                await voz.move_to(canal)
            return voz
        except (discord.ClientException, asyncio.TimeoutError, discord.HTTPException) as e:
            log.warning("falha ao conectar no canal de voz: %s", e)
            await _responder(ctx, "Não consegui entrar no canal de voz. Confira minhas permissões.")
            return None

    # ------------------------------------------------------------ checks

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        # modo slash: comandos de prefixo de musica sao ignorados
        # (o pmconfig continua aceitando prefixo pra nao trancar ninguem fora)
        cfg = self._cfg(ctx)
        if (
            cfg["modo_slash"]
            and ctx.interaction is None
            and ctx.command is not None
            and ctx.command.name != "pmconfig"
        ):
            raise commands.CheckFailure()
        return True

    # ------------------------------------------------------------ comandos

    @commands.hybrid_command(
        name="tocar",
        description="Toca uma música do SoundCloud, Bandcamp, link direto ou rádio.",
        aliases=("p", "play"),
        extras={
            "categoria": "Música",
            "uso": ",tocar <nome ou link>",
            "descricao": "Busca no SoundCloud/Bandcamp ou toca um link direto de áudio ou rádio.",
        },
    )
    @app_commands.describe(busca="Nome da música ou link (SoundCloud, Bandcamp, mp3, rádio)")
    async def tocar(self, ctx: commands.Context, *, busca: str) -> None:
        if not await self._mesmo_canal(ctx):
            return
        if await self._conectar(ctx) is None:
            return

        self._lembrar_canal(ctx)
        cfg = self._cfg(ctx)

        if ctx.interaction is None:
            async with ctx.typing():
                await self._buscar_e_enfileirar(ctx, busca, cfg)
        else:
            await ctx.defer()
            await self._buscar_e_enfileirar(ctx, busca, cfg)

    async def _buscar_e_enfileirar(self, ctx: commands.Context, busca: str, cfg: dict) -> None:
        try:
            faixas = await buscar(busca, cfg, ctx.author.display_name)
        except FonteNaoSuportada:
            await _responder(
                ctx,
                "## Fonte não suportada",
                "O Poyo não toca YouTube. Use SoundCloud, Bandcamp, "
                "um link direto de áudio ou uma rádio.",
            )
            return
        except FonteDesligada as e:
            await _responder(ctx, "## Fonte desligada", str(e))
            return
        except NadaEncontrado:
            await _responder(ctx, "Não encontrei nada com essa busca. Tente outro nome ou link.")
            return

        player = self.player(ctx.guild.id)
        player.volume = cfg["volume"] / 100
        await player.adicionar(faixas)

        if player.atual is not None and len(faixas) == 1:
            faixa = faixas[0]
            emoji = EMOJI_FONTE.get(faixa.fonte, "🎵")
            await _responder(
                ctx,
                f"## {emoji} Na fila",
                f"**{faixa.titulo}** · `{faixa.duracao_txt}` · posição **{len(player.fila)}**",
            )
        elif len(faixas) > 1:
            await _responder(ctx, f"## 🎶 Playlist adicionada", f"**{len(faixas)} faixas** entraram na fila.")

    @commands.hybrid_command(
        name="pular",
        description="Pula a música atual.",
        aliases=("s", "skip"),
        extras={"categoria": "Música", "uso": ",pular", "descricao": "Pula para a próxima música da fila."},
    )
    async def pular(self, ctx: commands.Context) -> None:
        if not await self._mesmo_canal(ctx):
            return
        if not self._e_dj_ou_staff(ctx):
            await _responder(ctx, "Só o cargo de DJ ou a staff pode pular músicas.")
            return
        player = self.player(ctx.guild.id)
        if player.atual is None:
            await _responder(ctx, "Não tem nada tocando agora.")
            return
        player.pular()
        await _responder(ctx, "⏭️ Música pulada.")

    @commands.hybrid_command(
        name="parar",
        description="Para a música, limpa a fila e sai do canal.",
        aliases=("stop", "sair", "leave"),
        extras={"categoria": "Música", "uso": ",parar", "descricao": "Para tudo, limpa a fila e sai do canal de voz."},
    )
    async def parar(self, ctx: commands.Context) -> None:
        if not self._e_dj_ou_staff(ctx):
            await _responder(ctx, "Só o cargo de DJ ou a staff pode parar a música.")
            return
        await self.player(ctx.guild.id).parar()
        await _responder(ctx, "⏹️ Música parada e fila limpa.")

    @commands.hybrid_command(
        name="pausar",
        description="Pausa a música atual.",
        aliases=("pause",),
        extras={"categoria": "Música", "uso": ",pausar", "descricao": "Pausa a música que está tocando."},
    )
    async def pausar(self, ctx: commands.Context) -> None:
        if not await self._mesmo_canal(ctx):
            return
        if self.player(ctx.guild.id).pausar():
            await _responder(ctx, "⏸️ Pausado.")
        else:
            await _responder(ctx, "Não tem nada tocando agora.")

    @commands.hybrid_command(
        name="voltar",
        description="Retoma a música pausada.",
        aliases=("resume", "continuar"),
        extras={"categoria": "Música", "uso": ",voltar", "descricao": "Volta a tocar a música que estava pausada."},
    )
    async def voltar(self, ctx: commands.Context) -> None:
        if not await self._mesmo_canal(ctx):
            return
        if self.player(ctx.guild.id).retomar():
            await _responder(ctx, "▶️ De volta!")
        else:
            await _responder(ctx, "A música não está pausada.")

    @commands.hybrid_command(
        name="fila",
        description="Mostra a fila de músicas.",
        aliases=("q", "queue"),
        extras={"categoria": "Música", "uso": ",fila", "descricao": "Lista as próximas músicas da fila."},
    )
    async def fila(self, ctx: commands.Context) -> None:
        player = self.player(ctx.guild.id)
        if player.atual is None and not player.fila:
            await _responder(ctx, "A fila está vazia. Use `,tocar` para adicionar algo.")
            return

        blocos = []
        if player.atual is not None:
            blocos.append(
                f"## 🎵 Tocando agora\n**{player.atual.titulo}** · `{player.atual.duracao_txt}`"
            )
        if player.fila:
            linhas = ["### A seguir"]
            for i, faixa in enumerate(list(player.fila)[:10], start=1):
                linhas.append(f"`{i}.` **{faixa.titulo}** · `{faixa.duracao_txt}`")
            if len(player.fila) > 10:
                linhas.append(f"-# …e mais {len(player.fila) - 10} música(s).")
            blocos.append("\n".join(linhas))
        await _responder(ctx, *blocos)

    @commands.hybrid_command(
        name="volume",
        description="Ajusta o volume da música (0 a 100).",
        aliases=("vol",),
        extras={"categoria": "Música", "uso": ",volume <0-100>", "descricao": "Muda o volume da reprodução."},
    )
    @app_commands.describe(nivel="Volume de 0 a 100")
    async def volume(self, ctx: commands.Context, nivel: app_commands.Range[int, 0, 100]) -> None:
        if not self._e_dj_ou_staff(ctx):
            await _responder(ctx, "Só o cargo de DJ ou a staff pode mudar o volume.")
            return
        self.player(ctx.guild.id).definir_volume(nivel)
        self.config.definir(ctx.guild.id, "volume", nivel)
        await _responder(ctx, f"🔊 Volume ajustado para **{nivel}%**.")

    # ------------------------------------------------------------ pmconfig

    @commands.hybrid_group(
        name="pmconfig",
        description="Painel de configuração do sistema de música.",
        invoke_without_command=True,
        extras={
            "categoria": "Música",
            "uso": ",pmconfig",
            "descricao": "Abre o painel de configuração da música (modo slash, fontes, DJ, 24/7).",
        },
    )
    @commands.has_permissions(manage_guild=True)
    async def pmconfig(self, ctx: commands.Context) -> None:
        await _responder(ctx, view=painel_musica_view(self, ctx.guild.id))

    @pmconfig.command(
        name="dj",
        description="Define ou remove o cargo de DJ.",
        extras={
            "categoria": "Música",
            "uso": ",pmconfig dj @cargo | off",
            "descricao": "Define o cargo de DJ ou desliga essa exigência.",
        },
    )
    @commands.has_permissions(manage_guild=True)
    async def pmconfig_dj(self, ctx: commands.Context, *, cargo: str) -> None:
        # texto livre pra funcionar tanto no comando de prefixo quanto no
        # slash: tenta ler como "off" primeiro, senão resolve como cargo
        # (menção, ID ou nome) com o conversor normal do discord.py
        if cargo.strip().lower() in ("off", "desligar", "nenhum"):
            self.config.definir(ctx.guild.id, "dj_role", None)
            await _responder(ctx, "🎧 Cargo de DJ **desligado**. Qualquer pessoa pode controlar a música.")
            return

        try:
            papel = await commands.RoleConverter().convert(ctx, cargo.strip())
        except commands.RoleNotFound:
            await _responder(ctx, "Não achei esse cargo. Mencione o cargo (@cargo) ou escreva `off`.")
            return

        self.config.definir(ctx.guild.id, "dj_role", papel.id)
        await _responder(ctx, f"🎧 Cargo de DJ definido: {papel.mention}")

    # sair do canal quando ficar sozinho
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        membro: discord.Member,
        antes: discord.VoiceState,
        depois: discord.VoiceState,
    ) -> None:
        if membro.guild is None:
            return
        voz = membro.guild.voice_client
        if voz is None or voz.channel is None:
            return
        cfg = self.config.obter(membro.guild.id)
        if cfg.get("modo_247"):
            return
        humanos = [m for m in voz.channel.members if not m.bot]
        if not humanos:
            await self.player(membro.guild.id).parar()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Musica(bot))
