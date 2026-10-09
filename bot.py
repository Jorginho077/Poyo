import os
import pkgutil
import time
import asyncio
import io
from urllib.request import Request, urlopen

import discord
from discord.ext import commands
from dotenv import load_dotenv

try:
    from cogs._media import baixar_arquivo
except ModuleNotFoundError:
    async def baixar_arquivo(url: str, nome: str) -> discord.File:
        def baixar() -> bytes:
            pedido = Request(
                url,
                headers={"User-Agent": "Poyo-Discord-Bot/1.0"},
            )
            with urlopen(pedido, timeout=20) as resposta:
                return resposta.read()

        dados = await asyncio.to_thread(baixar)
        return discord.File(io.BytesIO(dados), filename=nome)


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIXO = ","

EMOJI_INICIO = "<:axolote:1556443018557661234>"
EMOJI_FINAL = "<a:emoji_481:1556442987691647068>"

NOVO_BANNER = (
    "https://raw.githubusercontent.com/"
    "Jorginho077/Poyo/main/assets/Tumblr-l-67143811701311.gif"
)
NOVO_BANNER_ATTACHMENT = "attachment://poyo-bot-banner.gif"


class Cartao(discord.ui.LayoutView):
    """Container V2 com banner dentro, acima e abaixo do texto."""

    def __init__(self, *blocos: str, usar_anexo: bool = True) -> None:
        super().__init__(timeout=None)
        banner_url = NOVO_BANNER_ATTACHMENT if usar_anexo else NOVO_BANNER

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
                    discord.MediaGalleryItem(banner_url)
                ),
                *(
                    discord.ui.TextDisplay(bloco)
                    for bloco in blocos
                ),
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(banner_url)
                ),
            )
        )


async def enviar_cartao(
    ctx: commands.Context,
    *blocos: str,
    delete_after: float | None = None,
) -> discord.Message:
    try:
        arquivo = await baixar_arquivo(
            NOVO_BANNER,
            "poyo-bot-banner.gif",
        )
    except (OSError, TimeoutError, discord.HTTPException):
        arquivo = None

    return await ctx.send(
        view=Cartao(*blocos, usar_anexo=arquivo is not None),
        file=arquivo,
        delete_after=delete_after,
    )


class BotModeracao(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=PREFIXO,
            intents=intents,
            case_insensitive=True,
            strip_after_prefix=True,
            help_command=None,
        )

    async def setup_hook(self) -> None:
        """Carrega automaticamente todos os arquivos Python de cogs/."""
        import cogs

        modulos = sorted(
            pkgutil.iter_modules(cogs.__path__),
            key=lambda item: item.name,
        )

        for modulo in modulos:
            if modulo.name.startswith("_"):
                continue

            nome_da_cog = f"cogs.{modulo.name}"
            await self.load_extension(nome_da_cog)
            print(f"Cog carregada: {nome_da_cog}")

        comandos_slash = await self.tree.sync()
        print(f"Comandos slash sincronizados: {len(comandos_slash)}")

    async def on_ready(self) -> None:
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Game(name="SIX SEVENNNN ⁶🤷‍♂️⁷"),
        )

        print(f"Conectado como {self.user} | ID: {self.user.id}")
        print(f"Servidores: {len(self.guilds)} | Prefixo: {PREFIXO}")
        print("Sistema iniciado com Components V2.")


bot = BotModeracao()


@bot.before_invoke
async def apagar_mensagem_do_comando(
    ctx: commands.Context,
) -> None:
    """Prepara slash commands e apaga mensagens apenas quando elas existem."""
    if ctx.interaction is not None:
        if not ctx.interaction.response.is_done():
            await ctx.defer()
        return

    if ctx.message is None:
        return

    try:
        await ctx.message.delete()
    except (
        discord.NotFound,
        discord.Forbidden,
        discord.HTTPException,
    ):
        pass


@bot.hybrid_command(
    name="comandos",
    description="Mostra a lista atualizada de comandos disponíveis.",
    aliases=("ajuda", "help"),
    extras={
        "categoria": "Informações",
        "uso": ",comandos",
        "descricao": "Mostra todos os comandos disponíveis e explica cada função.",
    },
)
async def comandos(ctx: commands.Context) -> None:
    """Exibe ajuda dinâmica usando Container e TextDisplay."""
    blocos = [
        "## Central de comandos\n\nConfira os comandos disponíveis neste servidor.",
    ]

    categorias: dict[str, list[commands.Command]] = {}

    for comando in bot.commands:
        if comando.hidden or comando.name == "comandos":
            continue

        categoria = comando.extras.get("categoria", "Outros")
        categorias.setdefault(categoria, []).append(comando)

    ordem = ["Moderação", "Utilidades", "Informações", "Outros"]
    organizadas = sorted(
        categorias.items(),
        key=lambda item: (
            ordem.index(item[0]) if item[0] in ordem else len(ordem),
            item[0],
        ),
    )

    for categoria, lista in organizadas:
        linhas = [f"### {categoria}"]

        for comando in sorted(lista, key=lambda item: item.name):
            uso = comando.extras.get(
                "uso",
                f"{PREFIXO}{comando.qualified_name}",
            )
            descricao = comando.extras.get(
                "descricao",
                comando.help or "Sem descrição disponível.",
            )
            linhas.append(f"**`{uso}`**\n{descricao}")

        blocos.append("\n\n".join(linhas))

    blocos.append(
        "-# As respostas de moderação somem em 25 segundos."
    )

    await enviar_cartao(ctx, *blocos)


@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    """Responde aos erros sem usar embeds e remove o aviso após 5 segundos."""
    if hasattr(ctx.command, "on_error"):
        return

    erro = getattr(error, "original", error)

    if isinstance(erro, commands.CommandNotFound):
        return

    # Membro calado: ignora o comando sem enviar container ou aviso.
    if ctx.guild is not None:
        moderacao = bot.get_cog("Moderacao")

        if moderacao is not None:
            guild_calados = getattr(
                moderacao,
                "calados",
                {},
            ).get(ctx.guild.id, {})

            expiracao = guild_calados.get(ctx.author.id)

            if expiracao is not None:
                if time.time() < expiracao:
                    return

                guild_calados.pop(ctx.author.id, None)

    if isinstance(erro, commands.NoPrivateMessage):
        mensagem = "Esse comando só pode ser usado dentro de um servidor."
    elif isinstance(erro, commands.MissingPermissions):
        # Apaga silenciosamente o comando de quem não tem permissão.
        if ctx.message is not None:
            try:
                await ctx.message.delete()
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                pass

        return
    elif isinstance(erro, commands.CheckFailure):
        # Inclui comandos de membros calados: não responde nem executa ação.
        return
    elif isinstance(erro, commands.BotMissingPermissions):
        mensagem = "Eu não possuo as permissões necessárias para executar essa ação."
    elif isinstance(erro, commands.MissingRequiredArgument):
        mensagem = (
            f"Está faltando o argumento `{erro.param.name}`.\n\n"
            f"Use `{PREFIXO}comandos` para consultar o formato correto."
        )
    elif isinstance(erro, commands.BadArgument):
        mensagem = (
            "Não consegui identificar algum argumento. Confira a menção, "
            "o ID ou o tempo informado e tente novamente."
        )
    elif isinstance(erro, commands.CommandOnCooldown):
        mensagem = f"Aguarde {erro.retry_after:.1f} segundos antes de tentar novamente."
    else:
        print(
            f"Erro no comando {getattr(ctx.command, 'qualified_name', 'desconhecido')}: {erro!r}"
        )
        mensagem = "Não consegui concluir esta ação agora. Verifique minhas permissões e tente novamente."

    await enviar_cartao(
        ctx,
            "## Não foi possível concluir",
            mensagem,
        delete_after=25,
    )


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "A variável DISCORD_TOKEN não foi encontrada na hospedagem."
        )

    bot.run(TOKEN)
