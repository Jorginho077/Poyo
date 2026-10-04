import os

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


class Cartao(discord.ui.LayoutView):
    """Container Components V2 com banner acima e abaixo."""

    def __init__(
        self,
        *blocos: str,
    ) -> None:
        super().__init__(
            timeout=None
        )

        banner_cima = discord.ui.MediaGallery(
            discord.MediaGalleryItem(BANNER_URL)
        )

        banner_baixo = discord.ui.MediaGallery(
            discord.MediaGalleryItem(BANNER_URL)
        )

        if blocos:
            blocos = list(blocos)

            blocos[0] = (
                f"{EMOJI_INICIO}  "
                f"{blocos[0]}"
            )

            blocos[-1] = (
                f"{blocos[-1]}\n\n"
                f"{EMOJI_FINAL}"
            )

        self.add_item(banner_cima)

        self.add_item(
            discord.ui.Container(
                *(
                    discord.ui.TextDisplay(bloco)
                    for bloco in blocos
                ),
            )
        )

        self.add_item(banner_baixo)


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
        await self.load_extension(
            "cogs.mod"
        )

    async def on_ready(self) -> None:
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Game(
                name=f"{PREFIXO}comandos | moderação"
            ),
        )

        print(f"Conectado como: {self.user}")
        print(f"ID: {self.user.id}")
        print(f"Servidores: {len(self.guilds)}")
        print(f"Prefixo: {PREFIXO}")
        print("Components V2 ativado.")
        print("Bot iniciado com sucesso.")


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
    """Mostra a central de comandos."""

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
        "-# Mensagens de moderação somem em 25 segundos."
    )

    await ctx.send(
        view=Cartao(
            *blocos
        )
    )


@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    """Trata os erros dos comandos."""

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
            "## Não foi possível concluir",
            mensagem,
        ),
        delete_after=25,
    )


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "A variável DISCORD_TOKEN não foi encontrada."
        )

    bot.run(TOKEN)
