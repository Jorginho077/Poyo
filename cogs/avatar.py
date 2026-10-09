from __future__ import annotations

import asyncio
import io
import json
from dataclasses import dataclass
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import discord
from discord.ext import commands

try:
    from ._media import baixar_arquivo
except ModuleNotFoundError:
    async def baixar_arquivo(url: str, nome: str) -> discord.File:
        def baixar() -> bytes:
            pedido = Request(url, headers={"User-Agent": "Poyo-Discord-Bot/1.0"})
            with urlopen(pedido, timeout=20) as resposta:
                return resposta.read()

        dados = await asyncio.to_thread(baixar)
        return discord.File(io.BytesIO(dados), filename=nome)


BANNER_URL = (
    "https://raw.githubusercontent.com/"
    "Jorginho077/Poyo/main/assets/Tumblr-l-67143811701311.gif"
)
PAINEL_BANNER_ATTACHMENT = "attachment://poyo-skinview-banner.gif"
ROBLOX_LOGO_ID = 1509476102890979409
MINECRAFT_LOGO_ID = 1509476073904148582


@dataclass
class ConfiguracaoSkin:
    canal_id: Optional[int] = None
    ativo: bool = False
    painel: Optional[discord.Message] = None


async def requisicao_json(
    metodo: str,
    url: str,
    dados: Optional[dict] = None,
) -> dict:
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


async def buscar_roblox(nome: str) -> Optional[dict]:
    try:
        resultado = await requisicao_json(
            "POST",
            "https://users.roblox.com/v1/usernames/users",
            {"usernames": [nome], "excludeBannedUsers": False},
        )
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    usuarios = resultado.get("data", [])
    if not usuarios:
        return None

    usuario = usuarios[0]
    user_id = int(usuario["id"])
    try:
        miniatura = await requisicao_json(
            "GET",
            "https://thumbnails.roblox.com/v1/users/avatar"
            f"?userIds={user_id}&size=720x720&format=Png&isCircular=false",
        )
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    dados_miniatura = miniatura.get("data", [])
    if not dados_miniatura or not dados_miniatura[0].get("imageUrl"):
        return None

    nome_usuario = str(usuario.get("name", nome))
    return {
        "plataforma": "Roblox",
        "nome": nome_usuario,
        "exibicao": str(usuario.get("displayName", nome_usuario)),
        "imagem": str(dados_miniatura[0]["imageUrl"]),
        "perfil": f"https://www.roblox.com/users/{user_id}/profile",
        "logo_id": ROBLOX_LOGO_ID,
    }


async def buscar_minecraft(nome: str) -> Optional[dict]:
    try:
        perfil = await requisicao_json(
            "GET",
            f"https://api.mojang.com/users/profiles/minecraft/{quote(nome)}",
        )
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    uuid = perfil.get("id")
    nome_real = perfil.get("name")
    if not uuid or not nome_real:
        return None

    # O render FULL mantém a skin real em corpo inteiro, com a pose
    # isométrica do visualizador do NameMC, camadas externas e modelo slim ou
    # classic conforme o perfil do jogador.
    imagem = (
        f"https://mc-api.io/render/FULL/{quote(nome_real)}/JAVA?size=832"
    )
    imagem_fallback = (
        f"https://crafatar.com/renders/body/{uuid}"
        "?overlay&scale=4"
    )
    return {
        "plataforma": "Minecraft",
        "nome": str(nome_real),
        "exibicao": str(nome_real),
        "imagem": imagem,
        "imagem_fallback": imagem_fallback,
        "perfil": f"https://namemc.com/profile/{quote(nome_real)}",
        "logo_id": MINECRAFT_LOGO_ID,
    }


async def buscar_skin(entrada: str) -> Optional[dict]:
    valor = entrada.strip().lstrip("@")
    minusculo = valor.lower()

    if minusculo.startswith(("minecraft:", "mine:", "mc:")):
        return await buscar_minecraft(valor.split(":", 1)[1].strip())

    if minusculo.startswith(("roblox:", "rbx:")):
        return await buscar_roblox(valor.split(":", 1)[1].strip())

    # Sem prefixo, tenta Roblox primeiro e Minecraft em seguida.
    resultado = await buscar_roblox(valor)
    return resultado if resultado is not None else await buscar_minecraft(valor)


def texto_curtidas(total: int) -> str:
    return f"{total} {'Curtida' if total == 1 else 'Curtidas'}"


class PainelSkin(discord.ui.LayoutView):
    def __init__(
        self,
        config: ConfiguracaoSkin,
        autor_id: int,
        banner_url: str = PAINEL_BANNER_ATTACHMENT,
    ) -> None:
        super().__init__(timeout=None)
        self.config = config
        self.autor_id = autor_id

        canal = (
            f"<#{config.canal_id}>"
            if config.canal_id is not None
            else "não definido"
        )
        estado = "ativado" if config.ativo else "desativado"

        selecionar = discord.ui.Button(
            label="Canal",
            style=discord.ButtonStyle.primary,
            custom_id=f"skinview:canal:{autor_id}",
        )
        ativar = discord.ui.Button(
            label="Ativar",
            style=discord.ButtonStyle.success,
            custom_id=f"skinview:ativar:{autor_id}",
        )
        selecionar.callback = self.selecionar_canal
        ativar.callback = self.ativar

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(discord.MediaGalleryItem(banner_url)),
                discord.ui.TextDisplay(
                    "## Skin View\n\n"
                    f"Canal: {canal}\n"
                    f"Status: {estado}\n\n"
                    "Escolha o canal e clique em **Ativar**."
                ),
                discord.ui.ActionRow(selecionar, ativar),
                discord.ui.MediaGallery(discord.MediaGalleryItem(banner_url)),
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
            "Mencione o canal em que as skins serão enviadas.",
            ephemeral=True,
        )
        if interaction.guild is None or interaction.channel is None:
            return

        def conferir(mensagem: discord.Message) -> bool:
            return (
                mensagem.author.id == self.autor_id
                and mensagem.guild is not None
                and mensagem.guild.id == interaction.guild.id
                and mensagem.channel.id == interaction.channel.id
            )

        try:
            mensagem = await interaction.client.wait_for(
                "message", timeout=120, check=conferir
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
                "Não encontrei uma menção de canal.", ephemeral=True
            )
            return

        canal = mensagem.channel_mentions[0]
        if not isinstance(canal, discord.TextChannel):
            await interaction.followup.send(
                "Escolha um canal de texto.", ephemeral=True
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
            f"Canal definido: {canal.mention}", ephemeral=True
        )
        await self.atualizar()

    async def ativar(self, interaction: discord.Interaction) -> None:
        if not await self.autorizado(interaction):
            return
        if self.config.canal_id is None:
            await interaction.response.send_message(
                "Escolha um canal antes de ativar.", ephemeral=True
            )
            return

        self.config.ativo = True
        await interaction.response.send_message(
            f"Skin View ativado em <#{self.config.canal_id}>.",
            ephemeral=True,
        )
        await self.atualizar()

    async def atualizar(self) -> None:
        if self.config.painel is None:
            return
        try:
            await self.config.painel.edit(
                view=PainelSkin(self.config, self.autor_id)
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


class SkinView(discord.ui.LayoutView):
    def __init__(self, skin: dict, imagem_url: Optional[str] = None) -> None:
        super().__init__(timeout=None)
        self.skin = skin
        self.imagem_url = imagem_url or skin["imagem"]
        self.curtidas: set[int] = set()
        self.lock = asyncio.Lock()
        self.montar()

    def montar(self) -> None:
        self.clear_items()
        logo = discord.PartialEmoji(
            name=("RobloxLogo" if self.skin["plataforma"] == "Roblox" else "minecraftlogopng2"),
            id=self.skin["logo_id"],
        )
        perfil = discord.ui.Button(
            emoji=logo,
            style=discord.ButtonStyle.link,
            url=self.skin["perfil"],
        )
        coracao = discord.ui.Button(
            emoji="🤍",
            style=discord.ButtonStyle.secondary,
            custom_id=f"skinview:curtir:{self.skin['plataforma']}:{self.skin['nome']}",
        )
        coracao.callback = self.curtir

        self.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    discord.ui.TextDisplay(
                        f"## [{self.skin['exibicao']}]({self.skin['perfil']})\n"
                        f"`@{self.skin['nome']}` - "
                        f"{texto_curtidas(len(self.curtidas))}"
                    ),
                    accessory=perfil,
                ),
                discord.ui.Separator(),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(self.imagem_url)
                ),
                discord.ui.Separator(),
                discord.ui.ActionRow(coracao),
            )
        )

    async def curtir(self, interaction: discord.Interaction) -> None:
        async with self.lock:
            if interaction.user.id in self.curtidas:
                await interaction.response.defer()
                return
            self.curtidas.add(interaction.user.id)
            self.montar()
            await interaction.response.defer()
            try:
                await interaction.message.edit(view=self)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return


class Avatar(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.configuracoes: dict[int, ConfiguracaoSkin] = {}

    @commands.hybrid_command(
        name="skinviewpainel",
        description="Configura o canal de consulta de skins.",
        extras={
            "categoria": "Utilidades",
            "uso": ",skinviewpainel",
            "descricao": "Configura o canal para consultar skins.",
        },
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(send_messages=True, read_message_history=True)
    async def skinviewpainel(self, ctx: commands.Context) -> None:
        try:
            await ctx.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        config = self.configuracoes.setdefault(
            ctx.guild.id, ConfiguracaoSkin()
        )
        try:
            arquivo = await baixar_arquivo(
                BANNER_URL,
                "poyo-skinview-banner.gif",
            )
            banner_url = PAINEL_BANNER_ATTACHMENT
        except (OSError, TimeoutError, discord.HTTPException):
            arquivo = None
            banner_url = BANNER_URL

        config.painel = await ctx.send(
            view=PainelSkin(config, ctx.author.id, banner_url),
            file=arquivo,
        )

    async def _enviar_skin(
        self,
        ctx: commands.Context,
        nome: str,
        buscar: callable,
    ) -> None:
        config = self.configuracoes.get(ctx.guild.id)
        if config is None or not config.ativo or config.canal_id is None:
            return
        if ctx.channel.id != config.canal_id:
            return

        try:
            await ctx.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        resultado = await buscar(nome.strip().lstrip("@"))
        if resultado is None:
            await ctx.send(
                "Não encontrei esse usuário nessa plataforma.",
                delete_after=10,
            )
            return

        arquivo = None
        imagem_url = resultado["imagem"]
        urls_imagem = [
            resultado["imagem"],
            resultado.get("imagem_fallback"),
        ]
        for url in urls_imagem:
            if not url:
                continue
            try:
                arquivo = await baixar_arquivo(url, "poyo-avatar.png")
                imagem_url = "attachment://poyo-avatar.png"
                break
            except (OSError, TimeoutError, discord.HTTPException):
                continue

        await ctx.send(
            view=SkinView(resultado, imagem_url),
            file=arquivo,
        )

    @commands.hybrid_command(
        name="avatar_roblox",
        description="Mostra o avatar de um usuário do Roblox.",
        extras={
            "categoria": "Utilidades",
            "uso": ",avatar_roblox nome",
            "descricao": "Mostra o avatar de um usuário do Roblox.",
        },
    )
    @commands.guild_only()
    @commands.bot_has_permissions(send_messages=True, read_message_history=True)
    async def avatar_roblox(self, ctx: commands.Context, *, nome: str) -> None:
        await self._enviar_skin(ctx, nome, buscar_roblox)

    @commands.hybrid_command(
        name="avatar_minecraft",
        description="Mostra a skin de um usuário do Minecraft.",
        extras={
            "categoria": "Utilidades",
            "uso": ",avatar_minecraft nome",
            "descricao": "Mostra a skin de um usuário do Minecraft.",
        },
    )
    @commands.guild_only()
    @commands.bot_has_permissions(send_messages=True, read_message_history=True)
    async def avatar_minecraft(self, ctx: commands.Context, *, nome: str) -> None:
        await self._enviar_skin(ctx, nome, buscar_minecraft)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Avatar(bot))
