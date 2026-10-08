from __future__ import annotations

import hashlib
import os
import pkgutil
import time
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# Prefixo antigo: só usado para converter os "uso" das cogs para "/".
PREFIXO = ","

EMOJI_INICIO = "<:axolote:1556443018557661234>"
EMOJI_FINAL = "<a:emoji_481:1556442987691647068>"

# Faz TODOS os @commands.command / @commands.group das cogs
# virarem comandos híbridos (prefixo + slash) automaticamente,
# sem precisar mexer nas cogs. Precisa vir antes de carregar as cogs.
commands.command = commands.hybrid_command
commands.group = commands.hybrid_group


# ============================================================
# GIFS / ASSETS
# ============================================================

GIFS = {
    "banner": {
        "url": (
            "https://raw.githubusercontent.com/"
            "Jorginho077/Poyo/main/assets/"
            "Tumblr-l-67143811701311.gif"
        ),
        # Nome novo para não reutilizar o GIF antigo.
        "arquivo": "poyo_banner_v2.gif",
    },
}


class Cartao(discord.ui.LayoutView):
    """Container V2 com banner acima e abaixo do texto."""

    def __init__(
        self,
        banner: str,
        *blocos: str,
    ) -> None:
        super().__init__(timeout=None)

        if blocos:
            blocos = list(blocos)

            blocos[0] = (
                f"{EMOJI_INICIO}  "
                f"{blocos[0]}  "
                f"{EMOJI_FINAL}"
            )

        self.add_item(
            discord.ui.Container(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(banner)
                ),
                *(
                    discord.ui.TextDisplay(bloco)
                    for bloco in blocos
                ),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(banner)
                ),
            )
        )


class BotModeracao(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            # Sem prefixo de texto: só slash (ou menção ao bot).
            command_prefix=commands.when_mentioned,
            intents=intents,
            case_insensitive=True,
            strip_after_prefix=True,
            help_command=None,
        )

        self.assets_dir = (
            Path(__file__).resolve().parent / "assets"
        )

        self.assets_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.gifs: dict[str, Path] = {}

    # ========================================================
    # DOWNLOAD DOS GIFS
    # ========================================================

    async def baixar_gifs(self) -> None:
        """
        Baixa os GIFs configurados.

        O download é feito novamente para garantir que o bot
        não fique preso usando uma cópia antiga/ruim.
        """

        timeout = aiohttp.ClientTimeout(total=30)

        headers = {
            "User-Agent": "PoyoBot/1.0"
        }

        async with aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
        ) as session:

            for nome, dados in GIFS.items():
                url = dados["url"]
                nome_arquivo = dados["arquivo"]

                caminho = self.assets_dir / nome_arquivo
                temporario = (
                    self.assets_dir
                    / f".{nome_arquivo}.tmp"
                )

                try:
                    print(
                        f"Baixando GIF atualizado: "
                        f"{nome_arquivo}"
                    )

                    async with session.get(url) as resposta:
                        resposta.raise_for_status()
                        conteudo = await resposta.read()

                    if not conteudo:
                        raise RuntimeError(
                            "O servidor retornou um arquivo vazio."
                        )

                    if not (
                        conteudo.startswith(b"GIF87a")
                        or conteudo.startswith(b"GIF89a")
                    ):
                        raise RuntimeError(
                            "O arquivo baixado não é um GIF válido."
                        )

                    # Primeiro grava em temporário.
                    temporario.write_bytes(conteudo)

                    # Só substitui o arquivo depois do download completo.
                    temporario.replace(caminho)

                    self.gifs[nome] = caminho

                    print(
                        f"GIF atualizado com sucesso: "
                        f"{nome_arquivo} "
                        f"({len(conteudo):,} bytes)"
                    )

                except (
                    aiohttp.ClientError,
                    TimeoutError,
                    OSError,
                    RuntimeError,
                ) as erro:

                    try:
                        if temporario.exists():
                            temporario.unlink()
                    except OSError:
                        pass

                    # Se já existir uma cópia válida, usa ela.
                    if (
                        caminho.exists()
                        and caminho.stat().st_size > 0
                    ):
                        self.gifs[nome] = caminho

                        print(
                            f"Não foi possível atualizar "
                            f"{nome_arquivo}: {erro}"
                        )
                        print(
                            "Usando a cópia local existente."
                        )

                    else:
                        raise RuntimeError(
                            f"Não foi possível carregar "
                            f"o GIF '{nome_arquivo}'."
                        ) from erro

    # ========================================================
    # SISTEMA CENTRAL DE ASSETS
    # ========================================================

    def asset_path(self, nome: str) -> Path:
        caminho = self.gifs.get(nome)

        if caminho is None:
            raise RuntimeError(
                f"O asset '{nome}' não foi carregado."
            )

        return caminho

    def asset_file(self, nome: str) -> discord.File:
        caminho = self.asset_path(nome)

        dados = GIFS.get(nome)

        if dados is None:
            raise KeyError(
                f"Asset desconhecido: {nome}"
            )

        return discord.File(
            caminho,
            filename=dados["arquivo"],
        )

    def asset_url(self, nome: str) -> str:
        """
        Retorna a referência attachment:// usada pelo Components V2.
        """

        dados = GIFS.get(nome)

        if dados is None:
            raise KeyError(
                f"Asset desconhecido: {nome}"
            )

        self.asset_path(nome)

        return f"attachment://{dados['arquivo']}"

    # ========================================================
    # SYNC DOS SLASH (global, só quando algo mudou)
    # ========================================================

    def _assinatura_slash(self) -> str:
        """Hash dos slash atuais, para saber se algo mudou."""

        partes = []

        for cmd in self.tree.get_commands():
            try:
                dados = cmd.to_dict(self.tree)
            except TypeError:
                dados = cmd.to_dict()

            partes.append(repr(sorted(dados.items())))

        texto = "|".join(sorted(partes))

        return hashlib.sha256(texto.encode()).hexdigest()

    async def sincronizar_slash(self) -> None:
        """
        Sincroniza de forma global e apenas se os comandos
        mudaram desde o último sync. Assim o bot não fica
        sincronizando a cada inicialização.
        """

        arquivo = self.assets_dir / ".slash_hash"
        atual = self._assinatura_slash()

        anterior = ""
        if arquivo.exists():
            anterior = arquivo.read_text().strip()

        if atual == anterior:
            print("Slash sem alterações, sync ignorado.")
            return

        try:
            sincronizados = await self.tree.sync()

        except discord.HTTPException as erro:
            print(f"Falha ao sincronizar slash: {erro!r}")
            return

        arquivo.write_text(atual)

        print(f"Slash sincronizados: {len(sincronizados)}")

    # ========================================================
    # CARREGAMENTO DOS COGS
    # ========================================================

    async def setup_hook(self) -> None:
        print("Preparando assets...")

        await self.baixar_gifs()

        print(
            f"Assets carregados: "
            f"{len(self.gifs)}/{len(GIFS)}"
        )

        import cogs

        modulos = sorted(
            pkgutil.iter_modules(cogs.__path__),
            key=lambda item: item.name,
        )

        for modulo in modulos:
            if modulo.name.startswith("_"):
                continue

            nome_da_cog = f"cogs.{modulo.name}"

            await self.load_extension(
                nome_da_cog
            )

            print(
                f"Cog carregada: "
                f"{nome_da_cog}"
            )

        await self.sincronizar_slash()

    async def on_ready(self) -> None:
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Game(
                name="/comandos | moderação"
            ),
        )

        print(
            f"Conectado como "
            f"{self.user} | ID: {self.user.id}"
        )

        print(
            f"Servidores: "
            f"{len(self.guilds)} | "
            f"Modo: slash"
        )

        print(
            "Sistema iniciado com Components V2."
        )


bot = BotModeracao()


# ============================================================
# EVENTOS GERAIS
# ============================================================

@bot.before_invoke
async def apagar_mensagem_do_comando(
    ctx: commands.Context,
) -> None:
    # Slash não tem mensagem para apagar.
    if ctx.interaction is not None:
        return

    try:
        await ctx.message.delete()

    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException,
    ):
        pass


async def negar_em_silencio(
    ctx: commands.Context,
) -> None:
    """
    No slash é obrigatório responder a interação,
    senão aparece "o aplicativo não respondeu".
    """

    if (
        ctx.interaction is not None
        and not ctx.interaction.response.is_done()
    ):
        await ctx.send(
            "Você não pode usar este comando.",
            ephemeral=True,
        )


# ============================================================
# COMANDO DE AJUDA
# ============================================================

@bot.hybrid_command(
    name="comandos",
    aliases=("ajuda", "help"),
    description="Mostra todos os comandos disponíveis.",
    extras={
        "categoria": "Informações",
        "uso": "/comandos",
        "descricao": (
            "Mostra todos os comandos disponíveis "
            "e explica cada função."
        ),
    },
)
async def comandos(
    ctx: commands.Context,
) -> None:
    blocos = [
        (
            "Central de comandos\n\n"
            "Confira os comandos disponíveis neste servidor."
        ),
    ]

    categorias: dict[
        str,
        list[commands.Command],
    ] = {}

    for comando in bot.commands:
        if comando.hidden or comando.name == "comandos":
            continue

        categoria = comando.extras.get(
            "categoria",
            "Outros",
        )

        categorias.setdefault(
            categoria,
            [],
        ).append(comando)

    ordem = [
        "Moderação",
        "Utilidades",
        "Informações",
        "Outros",
    ]

    organizadas = sorted(
        categorias.items(),
        key=lambda item: (
            ordem.index(item[0])
            if item[0] in ordem
            else len(ordem),
            item[0],
        ),
    )

    for categoria, lista in organizadas:
        linhas = [
            f"### {categoria}"
        ]

        for comando in sorted(
            lista,
            key=lambda item: item.name,
        ):
            uso = str(
                comando.extras.get(
                    "uso",
                    f"/{comando.qualified_name}",
                )
            )

            # Converte usos antigos (",ban @user") para "/ban @user".
            if uso.startswith(PREFIXO):
                uso = "/" + uso[len(PREFIXO):]

            descricao = comando.extras.get(
                "descricao",
                comando.help
                or "Sem descrição disponível.",
            )

            linhas.append(
                f"**`{uso}`**\n"
                f"{descricao}"
            )

        blocos.append(
            "\n\n".join(linhas)
        )

    blocos.append(
        "-# As respostas de moderação "
        "somem em 25 segundos."
    )

    await ctx.send(
        view=Cartao(
            bot.asset_url("banner"),
            *blocos,
        ),
        file=bot.asset_file("banner"),
        delete_after=25,
    )


# ============================================================
# ERROS
# ============================================================

@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:

    if hasattr(ctx.command, "on_error"):
        return

    erro = getattr(
        error,
        "original",
        error,
    )

    if isinstance(
        erro,
        commands.CommandNotFound,
    ):
        return

    if ctx.guild is not None:
        moderacao = bot.get_cog("Moderacao")

        if moderacao is not None:
            guild_calados = getattr(
                moderacao,
                "calados",
                {},
            ).get(
                ctx.guild.id,
                {},
            )

            expiracao = guild_calados.get(
                ctx.author.id
            )

            if expiracao is not None:
                if time.time() < expiracao:
                    await negar_em_silencio(ctx)
                    return

                guild_calados.pop(
                    ctx.author.id,
                    None,
                )

    if isinstance(
        erro,
        commands.NoPrivateMessage,
    ):
        mensagem = (
            "Esse comando só pode ser usado "
            "dentro de um servidor."
        )

    elif isinstance(
        erro,
        commands.MissingPermissions,
    ):
        await negar_em_silencio(ctx)
        return

    elif isinstance(
        erro,
        commands.CheckFailure,
    ) and not isinstance(
        erro,
        commands.BotMissingPermissions,
    ):
        await negar_em_silencio(ctx)
        return

    elif isinstance(
        erro,
        commands.BotMissingPermissions,
    ):
        mensagem = (
            "Eu não possuo as permissões "
            "necessárias para executar essa ação."
        )

    elif isinstance(
        erro,
        commands.MissingRequiredArgument,
    ):
        mensagem = (
            f"Está faltando o argumento "
            f"{erro.param.name}.\n\n"
            f"Use /comandos para "
            "consultar o formato correto."
        )

    elif isinstance(
        erro,
        commands.BadArgument,
    ):
        mensagem = (
            "Não consegui identificar algum "
            "argumento. Confira a menção, "
            "o ID ou o tempo informado e "
            "tente novamente."
        )

    elif isinstance(
        erro,
        commands.CommandOnCooldown,
    ):
        mensagem = (
            f"Aguarde {erro.retry_after:.1f} "
            "segundos antes de tentar novamente."
        )

    else:
        print(
            "Erro no comando "
            f"{getattr(ctx.command, 'qualified_name', 'desconhecido')}: "
            f"{erro!r}"
        )

        mensagem = (
            "Não consegui concluir esta ação agora. "
            "Verifique minhas permissões e tente novamente."
        )

    await ctx.send(
        view=Cartao(
            bot.asset_url("banner"),
            "Não foi possível concluir",
            mensagem,
        ),
        file=bot.asset_file("banner"),
        delete_after=25,
    )


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "A variável DISCORD_TOKEN "
            "não foi encontrada na hospedagem."
        )

    bot.run(TOKEN)
