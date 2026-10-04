import os
import pkgutil

import discord
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()


TOKEN = os.getenv("DISCORD_TOKEN")
PREFIXO = ","


EMOJI_INICIO = "<:axolote:1556443018557661234>"
EMOJI_FINAL = "<a:emoji_481:1556442987691647068>"


BANNER_URL = (
    "https://cdn.discordapp.com/attachments/"
    "1556052693511053397/1556449326891401307/"
    "GIF_image_3.gif?backend=b2&ex=6ac433e4&"
    "is=6ac2e264&hm=7bd09ac806e541b3edadc60c2c796f17"
    "dfcffe12b617209c6a735eb7e741ce9e&"
)


TEMPO_DAS_RESPOSTAS = 25


class Cartao(discord.ui.LayoutView):
    """
    Container V2 com o banner dentro:

    Banner
    Texto
    Banner
    """

    def __init__(
        self,
        *blocos: str,
    ) -> None:
        super().__init__(
            timeout=None
        )

        blocos = list(blocos)

        if blocos:
            blocos[0] = (
                f"{EMOJI_INICIO}  "
                f"{blocos[0]}"
            )

            blocos[-1] = (
                f"{blocos[-1]}\n\n"
                f"{EMOJI_FINAL}"
            )

        componentes = [
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    BANNER_URL
                )
            )
        ]

        componentes.extend(
            discord.ui.TextDisplay(bloco)
            for bloco in blocos
        )

        componentes.append(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    BANNER_URL
                )
            )
        )

        self.add_item(
            discord.ui.Container(
                *componentes
            )
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
        """
        Carrega automaticamente todos os arquivos .py
        encontrados dentro da pasta cogs.

        Exemplos carregados automaticamente:

        cogs/mod.py
        cogs/geral.py
        cogs/diversao.py
        cogs/logs.py
        cogs/boas_vindas.py
        """

        import cogs

        modulos = sorted(
            pkgutil.iter_modules(
                cogs.__path__
            ),
            key=lambda item: item.name,
        )

        for modulo in modulos:
            # Ignora __init__.py e arquivos privados.
            if modulo.name.startswith("_"):
                continue

            nome_da_cog = (
                f"cogs.{modulo.name}"
            )

            await self.load_extension(
                nome_da_cog
            )

            print(
                f"Cog carregada: {nome_da_cog}"
            )

    async def on_ready(self) -> None:
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Game(
                name=f"{PREFIXO}comandos | moderação"
            ),
        )

        print("=" * 50)
        print(
            f"Bot conectado como: {self.user}"
        )
        print(
            f"ID: {self.user.id}"
        )
        print(
            f"Servidores: {len(self.guilds)}"
        )
        print(
            f"Prefixo: {PREFIXO}"
        )
        print(
            "Components V2 ativado."
        )
        print(
            "Todas as cogs foram carregadas."
        )
        print("=" * 50)


bot = BotModeracao()


@bot.command(
    name="comandos",
    aliases=(
        "ajuda",
        "help",
    ),
    extras={
        "categoria": "Informações",
        "uso": ",comandos",
        "descricao": (
            "Mostra os comandos disponíveis."
        ),
    },
)
async def comandos(
    ctx: commands.Context,
) -> None:
    """
    Mostra automaticamente todos os comandos
    encontrados nas cogs carregadas.
    """

    blocos = [
        (
            "## Central de comandos\n\n"
            "Confira os comandos disponíveis."
        )
    ]

    categorias: dict[
        str,
        list[commands.Command],
    ] = {}

    for comando in bot.commands:
        if comando.hidden:
            continue

        if comando.name == "comandos":
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
        "Diversão",
        "Outros",
    ]

    categorias_organizadas = sorted(
        categorias.items(),
        key=lambda item: (
            ordem.index(item[0])
            if item[0] in ordem
            else len(ordem),
            item[0],
        ),
    )

    for categoria, lista in categorias_organizadas:
        linhas = [
            f"### {categoria}"
        ]

        for comando in sorted(
            lista,
            key=lambda item: item.name,
        ):
            uso = comando.extras.get(
                "uso",
                f"{PREFIXO}{comando.qualified_name}",
            )

            descricao = comando.extras.get(
                "descricao",
                comando.help or "Sem descrição.",
            )

            linhas.append(
                f"**`{uso}`**\n"
                f"{descricao}"
            )

        blocos.append(
            "\n\n".join(linhas)
        )

    blocos.append(
        "-# As respostas somem em 25 segundos."
    )

    await ctx.send(
        view=Cartao(
            *blocos
        ),
        delete_after=TEMPO_DAS_RESPOSTAS,
    )


@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    """
    Mostra os erros usando Components V2.
    """

    if hasattr(
        ctx.command,
        "on_error",
    ):
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
        mensagem = (
            "Você não possui a permissão necessária."
        )

    elif isinstance(
        erro,
        commands.BotMissingPermissions,
    ):
        mensagem = (
            "Eu não possuo as permissões necessárias."
        )

    elif isinstance(
        erro,
        commands.MissingRequiredArgument,
    ):
        mensagem = (
            f"Está faltando `{erro.param.name}`.\n\n"
            f"Use `{PREFIXO}comandos`."
        )

    elif isinstance(
        erro,
        commands.BadArgument,
    ):
        mensagem = (
            "Confira a menção, o ID ou o tempo informado."
        )

    elif isinstance(
        erro,
        commands.CommandOnCooldown,
    ):
        mensagem = (
            f"Aguarde {erro.retry_after:.1f} segundos."
        )

    else:
        print(
            f"Erro no comando "
            f"{getattr(ctx.command, 'qualified_name', 'desconhecido')}: "
            f"{erro!r}"
        )

        mensagem = (
            "Não consegui concluir essa ação."
        )

    await ctx.send(
        view=Cartao(
            "Não foi possível concluir",
            mensagem,
        ),
        delete_after=TEMPO_DAS_RESPOSTAS,
    )


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "A variável DISCORD_TOKEN "
            "não foi encontrada."
        )

    bot.run(TOKEN)
