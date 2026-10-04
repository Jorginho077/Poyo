import os

import discord
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIXO = ","


class Cartao(discord.ui.LayoutView):
    """
    Cartão criado com Components V2.

    Não utiliza discord.Embed e não define accent_color,
    portanto não possui a barra lateral colorida dos embeds.
    """

    def __init__(
        self,
        *blocos: str,
    ) -> None:
        super().__init__(
            timeout=None
        )

        self.add_item(
            discord.ui.Container(
                *(
                    discord.ui.TextDisplay(bloco)
                    for bloco in blocos
                )
            )
        )


class BotModeracao(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()

        # Necessário para o bot receber mensagens.
        intents.message_content = True

        # Necessário para trabalhar com membros.
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
        Carrega automaticamente a cog:

        cogs/mod.py
        """

        await self.load_extension(
            "cogs.mod"
        )

    async def on_ready(self) -> None:
        """
        Executado quando o bot termina de conectar.
        """

        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Game(
                name=f"{PREFIXO}comandos | moderação"
            ),
        )

        print("=" * 50)
        print(f"Bot conectado como: {self.user}")
        print(f"ID do bot: {self.user.id}")
        print(f"Servidores: {len(self.guilds)}")
        print(f"Prefixo: {PREFIXO}")
        print("Components V2 ativado.")
        print("Sistema iniciado com sucesso.")
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
            "Mostra todos os comandos disponíveis "
            "e explica a função de cada um."
        ),
    },
)
async def comandos(
    ctx: commands.Context,
) -> None:
    """
    Mostra a central de comandos usando Components V2.

    A lista é criada automaticamente a partir
    dos comandos registrados nas cogs.
    """

    blocos = [
        (
            "## Central de comandos\n\n"
            "Confira todos os comandos disponíveis "
            "neste servidor.\n\n"
            "A lista é atualizada automaticamente quando "
            "novos comandos são adicionados."
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

        if categoria not in categorias:
            categorias[categoria] = []

        categorias[categoria].append(
            comando
        )

    ordem_das_categorias = [
        "Moderação",
        "Utilidades",
        "Informações",
        "Outros",
    ]

    categorias_organizadas = sorted(
        categorias.items(),
        key=lambda item: (
            ordem_das_categorias.index(item[0])
            if item[0] in ordem_das_categorias
            else len(ordem_das_categorias),
            item[0],
        ),
    )

    for categoria, lista_de_comandos in categorias_organizadas:
        linhas = [
            f"### {categoria}"
        ]

        for comando in sorted(
            lista_de_comandos,
            key=lambda item: item.name,
        ):
            uso = comando.extras.get(
                "uso",
                f"{PREFIXO}{comando.qualified_name}",
            )

            descricao = comando.extras.get(
                "descricao",
                comando.help or "Sem descrição disponível.",
            )

            linhas.append(
                f"**`{uso}`**\n"
                f"{descricao}"
            )

        blocos.append(
            "\n\n".join(linhas)
        )

    blocos.append(
        "-# As mensagens de moderação desaparecem automaticamente após 5 segundos."
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
    """
    Trata erros dos comandos usando Components V2.

    Os avisos de erro desaparecem após 5 segundos.
    """

    if hasattr(ctx.command, "on_error"):
        return

    erro = getattr(
        error,
        "original",
        error,
    )

    # Não envia mensagem para comandos inexistentes.
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
            "Você não possui a permissão necessária "
            "para usar este comando."
        )

    elif isinstance(
        erro,
        commands.BotMissingPermissions,
    ):
        mensagem = (
            "Eu não possuo as permissões necessárias "
            "para executar esta ação."
        )

    elif isinstance(
        erro,
        commands.MissingRequiredArgument,
    ):
        nome_do_argumento = erro.param.name

        mensagem = (
            f"Está faltando o argumento "
            f"`{nome_do_argumento}`.\n\n"
            f"Use `{PREFIXO}comandos` para consultar "
            "o formato correto."
        )

    elif isinstance(
        erro,
        commands.BadArgument,
    ):
        mensagem = (
            "Não consegui identificar algum argumento.\n"
            "Confira a menção, o ID ou o tempo informado "
            "e tente novamente."
        )

    elif isinstance(
        erro,
        commands.CommandOnCooldown,
    ):
        mensagem = (
            f"Aguarde {erro.retry_after:.1f} segundos "
            "antes de tentar novamente."
        )

    else:
        print(
            "Erro no comando "
            f"{getattr(ctx.command, 'qualified_name', 'desconhecido')}: "
            f"{erro!r}"
        )

        mensagem = (
            "Não consegui concluir esta ação agora.\n"
            "Verifique minhas permissões e tente novamente."
        )

    await ctx.send(
        view=Cartao(
            "## Não foi possível concluir",
            mensagem,
        ),
        delete_after=5,
    )


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "A variável DISCORD_TOKEN não foi encontrada "
            "na hospedagem."
        )

    bot.run(TOKEN)
