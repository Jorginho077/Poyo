from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import discord
from discord.ext import commands


BANNER_URL = (
    "https://raw.githubusercontent.com/"
    "Jorginho077/Poyo/main/assets/Tumblr-l-67143811701311.gif"
)


@dataclass
class ConfiguracaoAvatar:
    canal_id: Optional[int] = None
    ativo: bool = False
    painel: Optional[discord.Message] = None


async def requisicao_json(
    metodo: str,
    url: str,
    dados: Optional[dict] = None,
) -> dict:
    """Faz uma requisição JSON sem bloquear o event loop do Discord."""
    corpo = None
    cabecalhos = {"User-Agent": "Poyo-Discord-Bot/1.0"}

    if dados is not None:
        corpo = json.dumps(dados).encode("utf-8")
        cabecalhos["Content-Type"] = "application/json"

    def solicitar() -> dict:
        pedido = Request(
            url,
            data=corpo,
            headers=cabecalhos,
            method=metodo,
        )
        with urlopen(pedido, timeout=15) as resposta:
            return json.loads(resposta.read().decode("utf-8"))

    return await asyncio.to_thread(solicitar)


async def buscar_usuario_roblox(nome: str) -> Optional[dict]:
    """Busca ID, username e display name pelo endpoint oficial do Roblox."""
    try:
        resultado = await requisicao_json(
            "POST",
            "https://users.roblox.com/v1/usernames/users",
            {
                "usernames": [nome],
                "excludeBannedUsers": False,
            },
        )
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    usuarios = resultado.get("data", [])
    if not usuarios:
        return None

    usuario = usuarios[0]
    return {
        "id": int(usuario["id"]),
        "name": str(usuario.get("name", nome)),
        "display_name": str(usuario.get("displayName", usuario.get("name", nome))),
    }


async def buscar_avatar_roblox(user_id: int) -> Optional[str]:
    """Obtém a miniatura renderizada do avatar do usuário."""
    url = (
        "https://thumbnails.roblox.com/v1/users/avatar"
        f"?userIds={user_id}&size=720x720&format=Png&isCircular=false"
    )

    try:
        resultado = await requisicao_json("GET", url)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    dados = resultado.get("data", [])
    if not dados:
        return None

    imagem = dados[0].get("imageUrl")
    return str(imagem) if imagem else None


class CartaoConfiguracao(discord.ui.LayoutView):
    def __init__(self, config: ConfiguracaoAvatar, autor_id: int) -> None:
        super().__init__(timeout=None)
        self.config = config
        self.autor_id = autor_id

        canal = (
            f"<#{config.canal_id}>"
            if config.canal_id is not None
            else "não definido"
        )
        estado = "ativado" if config.ativo else "desativado"

        botao_canal = discord.ui.Button(
            label="Canal",
            style=discord.ButtonStyle.primary,
            custom_id=f"robloxavatar:canal:{autor_id}",
        )
        botao_ativar = discord.ui.Button(
            label="Ativar",
            style=discord.ButtonStyle.success,
            custom_id=f"robloxavatar:ativar:{autor_id}",
        )
        botao_canal.callback = self.selecionar_canal
        botao_ativar.callback = self.ativar

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(BANNER_URL)
                ),
                discord.ui.TextDisplay(
                    "## Configuração do Roblox Avatar\n\n"
                    f"Canal: {canal}\n"
                    f"Status: {estado}\n"
                    "Escolha o canal e depois ative o sistema."
                ),
                discord.ui.ActionRow(botao_canal, botao_ativar),
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

        await interaction.response.send_message(
            "Mencione o canal em que os avatares serão enviados.",
            ephemeral=True,
        )

        canal_interacao = interaction.channel
        if canal_interacao is None or interaction.guild is None:
            return

        def conferir(mensagem: discord.Message) -> bool:
            return (
                mensagem.author.id == self.autor_id
                and mensagem.guild is not None
                and mensagem.guild.id == interaction.guild.id
                and mensagem.channel.id == canal_interacao.id
            )

        try:
            mensagem = await interaction.client.wait_for(
                "message",
                timeout=120,
                check=conferir,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Tempo esgotado. Clique em Canal para tentar novamente.",
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
        await interaction.followup.send(
            f"Canal definido: {canal.mention}",
            ephemeral=True,
        )
        await self.atualizar()

    async def ativar(self, interaction: discord.Interaction) -> None:
        if not await self.autorizado(interaction):
            return

        if self.config.canal_id is None:
            await interaction.response.send_message(
                "Escolha um canal antes de ativar.",
                ephemeral=True,
            )
            return

        self.config.ativo = True
        await interaction.response.send_message(
            f"Roblox Avatar ativado em <#{self.config.canal_id}>.",
            ephemeral=True,
        )
        await self.atualizar()

    async def atualizar(self) -> None:
        if self.config.painel is None:
            return

        try:
            await self.config.painel.edit(
                view=CartaoConfiguracao(self.config, self.autor_id)
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


class AvatarView(discord.ui.LayoutView):
    def __init__(self, usuario: dict, imagem_url: str) -> None:
        super().__init__(timeout=None)
        self.usuario = usuario

        coracao = discord.ui.Button(
            emoji="🤍",
            style=discord.ButtonStyle.secondary,
            custom_id=f"robloxavatar:curtir:{usuario['id']}",
        )
        coracao.callback = self.curtir

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"## {usuario['display_name']}\n"
                    f"`@{usuario['name']}`"
                ),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(imagem_url)
                ),
                discord.ui.ActionRow(coracao),
            )
        )

    async def curtir(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "você curtiu o avatar",
            ephemeral=True,
        )


class RobloxAvatar(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.configuracoes: dict[int, ConfiguracaoAvatar] = {}

    @commands.command(
        name="configravatar",
        extras={
            "categoria": "Utilidades",
            "uso": ",configravatar",
            "descricao": "Configura o canal do Roblox Avatar.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(
        send_messages=True,
        read_message_history=True,
    )
    async def configravatar(self, ctx: commands.Context) -> None:
        try:
            await ctx.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        config = self.configuracoes.setdefault(
            ctx.guild.id,
            ConfiguracaoAvatar(),
        )
        config.painel = await ctx.send(
            view=CartaoConfiguracao(config, ctx.author.id)
        )

    @commands.command(
        name="robloxavatar",
        extras={
            "categoria": "Utilidades",
            "uso": ",robloxavatar nome_do_usuário",
            "descricao": "Mostra o avatar de um usuário do Roblox.",
        },
    )
    @commands.guild_only()
    @commands.bot_has_permissions(
        send_messages=True,
        read_message_history=True,
    )
    async def robloxavatar(
        self,
        ctx: commands.Context,
        *,
        nome: str,
    ) -> None:
        config = self.configuracoes.get(ctx.guild.id)
        if config is None or config.canal_id is None or not config.ativo:
            return

        if ctx.channel.id != config.canal_id:
            return

        try:
            await ctx.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        usuario = await buscar_usuario_roblox(nome.strip().lstrip("@"))
        if usuario is None:
            await ctx.send(
                "Não encontrei esse usuário do Roblox.",
                delete_after=10,
            )
            return

        imagem_url = await buscar_avatar_roblox(usuario["id"])
        if imagem_url is None:
            await ctx.send(
                "Não consegui carregar o avatar desse usuário agora.",
                delete_after=10,
            )
            return

        await ctx.send(view=AvatarView(usuario, imagem_url))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RobloxAvatar(bot))
