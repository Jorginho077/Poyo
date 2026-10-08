from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands


BANNER_URL = (
    "https://raw.githubusercontent.com/"
    "Jorginho077/Poyo/main/assets/Tumblr-l-67143811701311.gif"
)
EMOJI_INICIO = "<:axolote:1556443018557661234>"
EMOJI_FINAL = "<a:emoji_481:1556442987691647068>"
DATABASE_PATH = Path(__file__).resolve().parent.parent / "poyo.sqlite3"


@dataclass
class ConfiguracaoJoin:
    guild_id: int
    autor_id: Optional[int] = None
    canal_id: Optional[int] = None
    titulo: str = "Boas-vindas"
    descricao: str = "Olá {user}, seja bem-vindo(a)!"
    gif_url: Optional[str] = None
    ativo: bool = False
    painel: Optional[discord.Message] = None
    painel_id: Optional[int] = None
    painel_canal_id: Optional[int] = None


def substituir_marcadores(
    texto: str,
    membro: discord.Member,
) -> str:
    """Substitui {user} pela menção do membro, sem alterar o restante do texto."""
    return texto.replace("{user}", membro.mention)


class WelcomeCard(discord.ui.LayoutView):
    def __init__(self, config: ConfiguracaoJoin, membro: discord.Member) -> None:
        super().__init__(timeout=None)
        titulo = substituir_marcadores(config.titulo, membro)
        descricao = substituir_marcadores(config.descricao, membro)

        conteudo: list[discord.ui.Item] = [
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(BANNER_URL)
            ),
            discord.ui.TextDisplay(
                f"{EMOJI_INICIO}  **{titulo}**  {EMOJI_FINAL}\n\n"
                f"{descricao}"
            ),
        ]

        if config.gif_url:
            conteudo.append(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(config.gif_url)
                )
            )

        conteudo.append(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(BANNER_URL)
            )
        )

        self.add_item(
            discord.ui.Container(*conteudo)
        )


class JoinPanel(discord.ui.LayoutView):
    def __init__(
        self,
        config: ConfiguracaoJoin,
        autor_id: int,
        cog: "JoinSystem",
    ) -> None:
        super().__init__(timeout=None)
        self.config = config
        self.autor_id = autor_id
        self.cog = cog

        canal = (
            f"<#{config.canal_id}>"
            if config.canal_id is not None
            else "não definido"
        )
        status = "ativado" if config.ativo else "desativado"
        preview = config.descricao[:180]
        if len(config.descricao) > 180:
            preview += "..."
        gif_status = "definido" if config.gif_url else "não definido"

        botao_canal = discord.ui.Button(
            label="Canal da mensagem",
            style=discord.ButtonStyle.primary,
            custom_id=f"join:canal:{autor_id}",
        )
        botao_mensagem = discord.ui.Button(
            label="Mensagem",
            style=discord.ButtonStyle.primary,
            custom_id=f"join:mensagem:{autor_id}",
        )
        botao_ativar = discord.ui.Button(
            label="Ativar Join System",
            style=discord.ButtonStyle.success,
            custom_id=f"join:ativar:{autor_id}",
        )
        botao_testar = discord.ui.Button(
            label="Testar",
            style=discord.ButtonStyle.secondary,
            custom_id=f"join:testar:{autor_id}",
        )

        botao_canal.callback = self.selecionar_canal
        botao_mensagem.callback = self.abrir_modal
        botao_ativar.callback = self.ativar
        botao_testar.callback = self.testar

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(BANNER_URL)
                ),
                discord.ui.TextDisplay(
                    f"{EMOJI_INICIO}  **Join System**  {EMOJI_FINAL}\n\n"
                    f"Canal: {canal}\n"
                    f"Status: {status}\n"
                    f"Título: {config.titulo}\n"
                    f"Mensagem: {preview}\n"
                    f"GIF: {gif_status}"
                ),
                discord.ui.ActionRow(botao_canal, botao_mensagem),
                discord.ui.ActionRow(botao_ativar, botao_testar),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(BANNER_URL)
                ),
            )
        )

    async def autorizado(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.autor_id:
            return True

        await interaction.response.send_message(
            "Somente quem abriu este painel pode configurá-lo.",
            ephemeral=True,
        )
        return False

    async def selecionar_canal(self, interaction: discord.Interaction) -> None:
        if not await self.autorizado(interaction):
            return
        if interaction.guild is None or interaction.channel is None:
            return

        await interaction.response.send_message(
            "Mencione o canal em que as boas-vindas serão enviadas.",
            ephemeral=True,
        )

        def conferir(mensagem: discord.Message) -> bool:
            return (
                mensagem.author.id == self.autor_id
                and mensagem.guild is not None
                and mensagem.guild.id == self.config.guild_id
                and mensagem.channel.id == interaction.channel.id
            )

        try:
            mensagem = await interaction.client.wait_for(
                "message",
                timeout=120,
                check=conferir,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Tempo esgotado. Clique novamente em Canal da mensagem.",
                ephemeral=True,
            )
            return

        try:
            await mensagem.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        if not mensagem.channel_mentions:
            await interaction.followup.send(
                "Não encontrei uma menção de canal.",
                ephemeral=True,
            )
            return

        canal = mensagem.channel_mentions[0]
        if not isinstance(canal, discord.TextChannel):
            await interaction.followup.send(
                "Escolha um canal de texto.",
                ephemeral=True,
            )
            return

        bot_membro = interaction.guild.me
        if bot_membro is None:
            return

        permissoes = canal.permissions_for(bot_membro)
        if not permissoes.view_channel or not permissoes.send_messages:
            await interaction.followup.send(
                "Não consigo ver ou enviar mensagens nesse canal.",
                ephemeral=True,
            )
            return

        self.config.canal_id = canal.id
        self.config.ativo = False
        await self.cog.salvar(self.config)
        await interaction.followup.send(
            f"Canal definido: {canal.mention}",
            ephemeral=True,
        )
        await self.atualizar()

    async def abrir_modal(self, interaction: discord.Interaction) -> None:
        if await self.autorizado(interaction):
            await interaction.response.send_modal(
                MensagemJoinModal(self)
            )

    async def ativar(self, interaction: discord.Interaction) -> None:
        if not await self.autorizado(interaction):
            return
        if self.config.canal_id is None:
            await interaction.response.send_message(
                "Escolha o canal antes de ativar o Join System.",
                ephemeral=True,
            )
            return
        if not self.config.descricao.strip():
            await interaction.response.send_message(
                "Configure uma descrição antes de ativar.",
                ephemeral=True,
            )
            return

        self.config.ativo = True
        await self.cog.salvar(self.config)
        await interaction.response.send_message(
            f"Join System ativado em <#{self.config.canal_id}>.",
            ephemeral=True,
        )
        await self.atualizar()

    async def testar(self, interaction: discord.Interaction) -> None:
        if not await self.autorizado(interaction):
            return
        if self.config.canal_id is None:
            await interaction.response.send_message(
                "Escolha o canal antes de testar.",
                ephemeral=True,
            )
            return

        canal = interaction.guild.get_channel(self.config.canal_id) if interaction.guild else None
        if not isinstance(canal, discord.TextChannel):
            await interaction.response.send_message(
                "O canal configurado não está disponível.",
                ephemeral=True,
            )
            return

        try:
            await canal.send(
                view=WelcomeCard(self.config, interaction.user),
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=True,
                    everyone=False,
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Não consegui enviar o teste nesse canal.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Boas-vindas de teste enviadas.",
            ephemeral=True,
        )

    async def atualizar(self) -> None:
        if self.config.painel is None:
            if (
                self.config.painel_id is None
                or self.config.painel_canal_id is None
            ):
                return

            canal = self.cog.bot.get_channel(self.config.painel_canal_id)
            if not isinstance(canal, discord.TextChannel):
                return

            try:
                self.config.painel = await canal.fetch_message(
                    self.config.painel_id
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        try:
            await self.config.painel.edit(
                view=JoinPanel(self.config, self.autor_id, self.cog)
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


class MensagemJoinModal(discord.ui.Modal, title="Mensagem de boas-vindas"):
    titulo = discord.ui.TextInput(
        label="Título",
        placeholder="Ex.: Bem-vindo(a) ao servidor!",
        max_length=256,
        required=True,
    )
    descricao = discord.ui.TextInput(
        label="Descrição",
        placeholder="Use {user} para mencionar quem entrou.",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )
    gif_url = discord.ui.TextInput(
        label="Link do GIF (opcional)",
        placeholder="https://.../boas-vindas.gif",
        max_length=500,
        required=False,
    )

    def __init__(self, painel: JoinPanel) -> None:
        super().__init__(timeout=120)
        self.painel = painel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        gif_url = str(self.gif_url.value).strip()
        if gif_url and not gif_url.lower().startswith(("http://", "https://")):
            await interaction.response.send_message(
                "O link do GIF precisa começar com `http://` ou `https://`.",
                ephemeral=True,
            )
            return

        self.painel.config.titulo = str(self.titulo.value)
        self.painel.config.descricao = str(self.descricao.value)
        self.painel.config.gif_url = gif_url or None
        await self.painel.cog.salvar(self.painel.config)
        await interaction.response.send_message(
            "Mensagem de boas-vindas salva.",
            ephemeral=True,
        )
        await self.painel.atualizar()


class JoinSystem(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.configuracoes: dict[int, ConfiguracaoJoin] = {}

    def _preparar_banco(self) -> None:
        with sqlite3.connect(DATABASE_PATH) as banco:
            banco.execute(
                """
                CREATE TABLE IF NOT EXISTS join_config (
                    guild_id INTEGER PRIMARY KEY,
                    autor_id INTEGER,
                    canal_id INTEGER,
                    titulo TEXT NOT NULL,
                    descricao TEXT NOT NULL,
                    gif_url TEXT,
                    ativo INTEGER NOT NULL DEFAULT 0,
                    painel_id INTEGER,
                    painel_canal_id INTEGER
                )
                """
            )
            colunas = {
                linha[1]
                for linha in banco.execute(
                    "PRAGMA table_info(join_config)"
                ).fetchall()
            }
            if "gif_url" not in colunas:
                banco.execute(
                    "ALTER TABLE join_config ADD COLUMN gif_url TEXT"
                )
            banco.commit()

    def _ler_banco(self) -> dict[int, ConfiguracaoJoin]:
        configuracoes: dict[int, ConfiguracaoJoin] = {}
        with sqlite3.connect(DATABASE_PATH) as banco:
            linhas = banco.execute(
                """
                SELECT guild_id, autor_id, canal_id, titulo, descricao,
                       gif_url, ativo, painel_id, painel_canal_id
                FROM join_config
                """
            ).fetchall()

        for linha in linhas:
            (
                guild_id,
                autor_id,
                canal_id,
                titulo,
                descricao,
                gif_url,
                ativo,
                painel_id,
                painel_canal_id,
            ) = linha
            configuracoes[int(guild_id)] = ConfiguracaoJoin(
                guild_id=int(guild_id),
                autor_id=int(autor_id) if autor_id is not None else None,
                canal_id=int(canal_id) if canal_id is not None else None,
                titulo=str(titulo),
                descricao=str(descricao),
                gif_url=str(gif_url) if gif_url else None,
                ativo=bool(ativo),
                painel_id=(
                    int(painel_id) if painel_id is not None else None
                ),
                painel_canal_id=(
                    int(painel_canal_id)
                    if painel_canal_id is not None
                    else None
                ),
            )
        return configuracoes

    def _salvar_banco(self, config: ConfiguracaoJoin) -> None:
        with sqlite3.connect(DATABASE_PATH) as banco:
            banco.execute(
                """
                INSERT INTO join_config (
                    guild_id, autor_id, canal_id, titulo, descricao, gif_url,
                    ativo, painel_id, painel_canal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    autor_id=excluded.autor_id,
                    canal_id=excluded.canal_id,
                    titulo=excluded.titulo,
                    descricao=excluded.descricao,
                    gif_url=excluded.gif_url,
                    ativo=excluded.ativo,
                    painel_id=excluded.painel_id,
                    painel_canal_id=excluded.painel_canal_id
                """,
                (
                    config.guild_id,
                    config.autor_id,
                    config.canal_id,
                    config.titulo,
                    config.descricao,
                    config.gif_url,
                    int(config.ativo),
                    config.painel_id,
                    config.painel_canal_id,
                ),
            )
            banco.commit()

    async def salvar(self, config: ConfiguracaoJoin) -> None:
        await asyncio.to_thread(self._salvar_banco, config)

    async def cog_load(self) -> None:
        await asyncio.to_thread(self._preparar_banco)
        self.configuracoes = await asyncio.to_thread(self._ler_banco)

        for config in self.configuracoes.values():
            if config.autor_id is not None:
                self.bot.add_view(
                    JoinPanel(config, config.autor_id, self)
                )

    @commands.command(
        name="joinsetup",
        extras={
            "categoria": "Utilidades",
            "uso": ",joinsetup",
            "descricao": "Configura o sistema de boas-vindas do servidor.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(send_messages=True, read_message_history=True)
    async def joinsetup(self, ctx: commands.Context) -> None:
        try:
            await ctx.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        config = self.configuracoes.setdefault(
            ctx.guild.id,
            ConfiguracaoJoin(guild_id=ctx.guild.id),
        )
        config.autor_id = ctx.author.id
        config.painel_canal_id = ctx.channel.id
        config.painel = await ctx.send(
            view=JoinPanel(config, ctx.author.id, self)
        )
        config.painel_id = config.painel.id
        await self.salvar(config)

    @commands.Cog.listener()
    async def on_member_join(self, membro: discord.Member) -> None:
        if membro.bot:
            return

        config = self.configuracoes.get(membro.guild.id)
        if config is None or not config.ativo or config.canal_id is None:
            return

        canal = membro.guild.get_channel(config.canal_id)
        if not isinstance(canal, discord.TextChannel):
            return

        try:
            await canal.send(
                view=WelcomeCard(config, membro),
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=True,
                    everyone=False,
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JoinSystem(bot))
