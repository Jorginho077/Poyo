import os

import discord
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = ","


class ModerationBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()

        # Necessário para detectar membros e mensagens.
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix=PREFIX,
            intents=intents,
            case_insensitive=True,
            help_command=None,
            strip_after_prefix=True,
        )

    async def setup_hook(self):
        await self.load_extension("cogs.mod")

    async def on_ready(self):
        atividade = discord.Game(name=f"{PREFIX}comandos | moderação")

        await self.change_presence(
            status=discord.Status.online,
            activity=atividade,
        )

        print(f"Conectado como {self.user} | ID: {self.user.id}")
        print(f"Servidores conectados: {len(self.guilds)}")
        print(f"Prefixo atual: {PREFIX}")


bot = ModerationBot()


@bot.command(
    name="comandos",
    aliases=("ajuda", "help"),
    extras={
        "categoria": "Informações",
        "uso": ",comandos",
        "descricao": "Mostra todos os comandos disponíveis e explica como usar cada um.",
    },
)
async def comandos(ctx: commands.Context):
    """
    Mostra uma lista dinâmica de todos os comandos registrados no bot.
    """

    embed = discord.Embed(
        title="Central de comandos",
        description=(
            "Aqui está tudo o que eu consigo fazer neste servidor.\n"
            "Use os comandos exatamente como aparecem abaixo."
        ),
    )

    categorias = {}

    for command in bot.commands:
        if command.hidden:
            continue

        if command.name == "comandos":
            continue

        categoria = command.extras.get(
            "categoria",
            "Outros",
        )

        if categoria not in categorias:
            categorias[categoria] = []

        categorias[categoria].append(command)

    ordem_categorias = [
        "Moderação",
        "Utilidades",
        "Informações",
        "Outros",
    ]

    categorias_ordenadas = sorted(
        categorias.items(),
        key=lambda item: (
            ordem_categorias.index(item[0])
            if item[0] in ordem_categorias
            else len(ordem_categorias),
            item[0],
        ),
    )

    for categoria, lista_comandos in categorias_ordenadas:
        linhas = []

        for command in sorted(
            lista_comandos,
            key=lambda comando: comando.name,
        ):
            descricao = command.extras.get(
                "descricao",
                command.help or "Sem descrição disponível.",
            )

            uso = command.extras.get(
                "uso",
                f"{PREFIX}{command.qualified_name}",
            )

            linhas.append(
                f"`{uso}`\n"
                f"{descricao}"
            )

        if linhas:
            embed.add_field(
                name=categoria,
                value="\n\n".join(linhas),
                inline=False,
            )

    embed.set_footer(
        text="Dica: mencione um membro sempre que o comando pedir @membro."
    )

    await ctx.send(embed=embed)


@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
):
    """
    Trata os erros dos comandos com mensagens claras e profissionais.
    """

    if hasattr(ctx.command, "on_error"):
        return

    original_error = getattr(
        error,
        "original",
        error,
    )

    if isinstance(
        original_error,
        commands.CommandNotFound,
    ):
        return

    if isinstance(
        original_error,
        commands.NoPrivateMessage,
    ):
        mensagem = (
            "Esse comando só pode ser usado dentro de um servidor."
        )

    elif isinstance(
        original_error,
        commands.MissingPermissions,
    ):
        mensagem = (
            "Você não tem a permissão necessária para usar este comando."
        )

    elif isinstance(
        original_error,
        commands.BotMissingPermissions,
    ):
        mensagem = (
            "Eu não tenho as permissões necessárias para executar essa ação."
        )

    elif isinstance(
        original_error,
        commands.MissingRequiredArgument,
    ):
        mensagem = (
            f"Está faltando o argumento "
            f"`{original_error.param.name}`.\n\n"
            f"Use `{PREFIX}comandos` para consultar o formato correto."
        )

    elif isinstance(
        original_error,
        commands.BadArgument,
    ):
        mensagem = (
            "Não consegui entender algum argumento.\n"
            "Confira a menção, o ID ou o tempo informado e tente novamente."
        )

    elif isinstance(
        original_error,
        commands.CommandOnCooldown,
    ):
        mensagem = (
            f"Aguarde {original_error.retry_after:.1f} segundos "
            "antes de usar esse comando novamente."
        )

    else:
        print(
            f"Erro no comando "
            f"{getattr(ctx.command, 'qualified_name', 'desconhecido')}: "
            f"{original_error!r}"
        )

        mensagem = (
            "Não consegui concluir essa ação agora.\n"
            "Verifique minhas permissões e tente novamente."
        )

    embed = discord.Embed(
        title="Não foi possível concluir",
        description=mensagem,
    )

    await ctx.send(
        embed=embed,
        delete_after=10,
    )


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "A variável DISCORD_TOKEN não foi encontrada."
        )

    bot.run(TOKEN)
