"""Consulta segura de informações da hospedagem do bot.

Comando slash: /hostinginfo

O IP público é obtido por um serviço externo somente quando o comando é usado.
A URL pública não pode ser descoberta automaticamente em todas as hospedagens:
ela deve ser informada pela variável PUBLIC_URL, quando existir.
"""

from __future__ import annotations

import asyncio
import os
import socket
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands


IP_SERVICES = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
)


def _discover_public_url() -> tuple[str | None, str]:
    """Tenta descobrir um domínio público sem fabricar um endereço."""
    direct = os.getenv("PUBLIC_URL", "").strip()
    if direct:
        return direct, "PUBLIC_URL"

    direct_candidates = (
        ("RENDER_EXTERNAL_URL", "Render"),
        ("RAILWAY_PUBLIC_URL", "Railway"),
        ("KOYEB_PUBLIC_DOMAIN", "Koyeb"),
        ("APP_URL", "APP_URL"),
        ("SERVICE_URL", "SERVICE_URL"),
        ("EXTERNAL_URL", "EXTERNAL_URL"),
    )
    for variable, source in direct_candidates:
        value = os.getenv(variable, "").strip()
        if not value:
            continue
        if "://" not in value:
            value = f"https://{value}"
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return value.rstrip("/"), source

    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway_domain:
        return f"https://{railway_domain}", "RAILWAY_PUBLIC_DOMAIN"

    fly_name = os.getenv("FLY_APP_NAME", "").strip()
    if fly_name:
        return f"https://{fly_name}.fly.dev", "FLY_APP_NAME"

    heroku_name = os.getenv("HEROKU_APP_NAME", "").strip()
    if heroku_name:
        return f"https://{heroku_name}.herokuapp.com", "HEROKU_APP_NAME"

    return None, "não encontrado nos metadados do provedor"


def _discover_port(listening_ports: list[int]) -> tuple[str, str]:
    """Diferencia porta explicitamente informada de porta apenas local."""
    for variable in ("PUBLIC_PORT", "PORT", "SERVER_PORT", "HTTP_PORT"):
        value = os.getenv(variable, "").strip()
        if value.isdigit() and 1 <= int(value) <= 65535:
            return value, variable

    if not listening_ports:
        return "não encontrada", "nenhuma porta local em escuta"

    preferred = [80, 443, 8080, 8000, 3000, 5000, 3001]
    for port in preferred:
        if port in listening_ports:
            return str(port), "porta local detectada"
    return ", ".join(str(port) for port in listening_ports), "portas locais detectadas"


def _local_listening_ports() -> list[int]:
    """Lê portas TCP em LISTEN sem depender de psutil ou netstat."""
    ports: set[int] = set()

    for proc_file in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc_file, "r", encoding="ascii") as file:
                next(file, None)
                for line in file:
                    fields = line.split()
                    if len(fields) < 4 or fields[3] != "0A":
                        continue
                    try:
                        ports.add(int(fields[1].rsplit(":", 1)[1], 16))
                    except (IndexError, ValueError):
                        continue
        except (FileNotFoundError, PermissionError, OSError):
            continue

    return sorted(ports)


def _public_ip_sync() -> str | None:
    """Obtém IPv4/IPv6 público usando serviços simples de texto."""
    for service in IP_SERVICES:
        try:
            request = Request(
                service,
                headers={"User-Agent": "Poyo-HostingInfo/1.0"},
            )
            with urlopen(request, timeout=5) as response:
                value = response.read(128).decode("utf-8", "replace").strip()

            for family in (socket.AF_INET, socket.AF_INET6):
                try:
                    socket.inet_pton(family, value)
                    return value
                except OSError:
                    continue
        except (OSError, TimeoutError, ValueError):
            continue
    return None


class HostingInfo(commands.Cog):
    """Exibe dados básicos da máquina que executa o processo do bot."""

    hostinghttp = app_commands.Group(
        name="hostinghttp",
        description="Controla o servidor HTTP opcional do bot.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._http_runner: web.AppRunner | None = None
        self._http_site: web.TCPSite | None = None
        self._http_host: str | None = None
        self._http_port: int | None = None

    def cog_unload(self) -> None:
        # discord.py chama cog_unload de forma síncrona.
        if self._http_runner is not None:
            asyncio.create_task(self.stop_http())

    @staticmethod
    async def _http_index(request: web.Request) -> web.Response:
        return web.Response(
            text=(
                "Poyo HTTP online\n"
                "Use /health para verificar a saúde do serviço.\n"
            ),
            content_type="text/plain",
        )

    @staticmethod
    async def _http_health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "service": "poyo"})

    async def start_http(self) -> tuple[bool, str]:
        """Inicia o HTTP somente quando solicitado por comando/configuração."""
        if self._http_runner is not None:
            return False, f"já está ativo em {self._http_host}:{self._http_port}"

        host = os.getenv("HTTP_BIND_HOST", "0.0.0.0").strip() or "0.0.0.0"
        raw_port = os.getenv("PORT", os.getenv("HTTP_PORT", "8080")).strip()
        try:
            port = int(raw_port)
        except ValueError:
            return False, f"porta inválida: `{raw_port}`"
        if not 1 <= port <= 65535:
            return False, "a porta precisa estar entre 1 e 65535"

        application = web.Application()
        application.router.add_get("/", self._http_index)
        application.router.add_get("/health", self._http_health)
        runner = web.AppRunner(application, access_log=None)

        try:
            await runner.setup()
            site = web.TCPSite(runner, host=host, port=port)
            await site.start()
        except (OSError, RuntimeError) as error:
            await runner.cleanup()
            return False, f"não foi possível abrir `{host}:{port}`: {error}"

        self._http_runner = runner
        self._http_site = site
        self._http_host = host
        self._http_port = port
        return True, f"iniciado em `{host}:{port}`"

    async def stop_http(self) -> tuple[bool, str]:
        if self._http_runner is None:
            return False, "não está ativo"
        address = f"{self._http_host}:{self._http_port}"
        try:
            await self._http_runner.cleanup()
        finally:
            self._http_runner = None
            self._http_site = None
            self._http_host = None
            self._http_port = None
        return True, f"parado; a porta `{address}` foi liberada"

    def http_status(self) -> str:
        if self._http_runner is None:
            return "desligado"
        return f"ativo em `{self._http_host}:{self._http_port}`"

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(permissions and permissions.administrator)

    async def _build_info_text(self) -> str:
        public_ip = await asyncio.to_thread(_public_ip_sync)
        hostname = socket.gethostname()
        local_host = os.getenv("HOST", os.getenv("HOSTNAME", "não informado"))
        ports = await asyncio.to_thread(_local_listening_ports)
        public_url, url_source = _discover_public_url()
        public_port, port_source = _discover_port(ports)

        port_text = ", ".join(str(port) for port in ports) if ports else "nenhuma detectada"
        ip_text = public_ip or "não foi possível detectar"
        url_text = public_url or "não determinável automaticamente"

        return (
            "## Informações da hospedagem\n"
            f"**IP público:** `{ip_text}`\n"
            f"**Hostname:** `{hostname}`\n"
            f"**Host configurado:** `{local_host}`\n"
            f"**URL pública:** `{url_text}`\n"
            f"**Origem da URL:** `{url_source}`\n"
            f"**Porta provável:** `{public_port}`\n"
            f"**Origem da porta:** `{port_source}`\n"
            f"**Portas TCP locais em escuta:** `{port_text}`\n\n"
            "A URL e a porta pública dependem do provedor. O comando tenta detectar "
            "metadados conhecidos automaticamente, mas não inventa uma URL quando o "
            "provedor não a informa. Se o bot apenas se conecta ao Discord, normalmente "
            "não existe uma porta pública do bot. As portas listadas são apenas as que "
            "estão abertas localmente; NAT, proxy ou firewall podem impedir acesso externo."
        )

    @commands.command(
        name="hostinginfo",
        help="Envia por DM o IP, URL, hostname e portas da hospedagem.",
        extras={"categoria": "Utilidades", "uso": ",hostinginfo"},
    )
    @commands.guild_only()
    async def hostinginfo_prefix(self, ctx: commands.Context) -> None:
        if not ctx.author.guild_permissions.administrator:
            return
        try:
            await ctx.author.send(await self._build_info_text())
            await ctx.reply("Enviei as informações da hospedagem por mensagem privada.", delete_after=10)
        except discord.Forbidden:
            await ctx.reply("Não consegui enviar mensagem privada para você.", delete_after=10)

    @commands.command(
        name="hostinghttp",
        help="Inicia, para ou mostra o status do servidor HTTP. Uso: ,hostinghttp iniciar|parar|status",
        extras={"categoria": "Utilidades", "uso": ",hostinghttp <iniciar|parar|status>"},
    )
    @commands.guild_only()
    async def hostinghttp_prefix(self, ctx: commands.Context, acao: str = "status") -> None:
        if not ctx.author.guild_permissions.administrator:
            return
        acao = acao.casefold().strip()
        if acao == "iniciar":
            sucesso, mensagem = await self.start_http()
            resposta = ("✅ " if sucesso else "ℹ️ ") + mensagem
        elif acao == "parar":
            sucesso, mensagem = await self.stop_http()
            resposta = ("✅ " if sucesso else "ℹ️ ") + mensagem
        elif acao == "status":
            resposta = f"Servidor HTTP: **{self.http_status()}**. Endpoints: `/` e `/health`."
        else:
            resposta = "Use `,hostinghttp iniciar`, `,hostinghttp parar` ou `,hostinghttp status`."
        await ctx.reply(resposta, delete_after=15)

    @hostinghttp.command(
        name="iniciar",
        description="Inicia o servidor HTTP usando PORT ou HTTP_PORT.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def hostinghttp_start(self, interaction: discord.Interaction) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message("Somente administradores.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        started, message = await self.start_http()
        await interaction.followup.send(
            ("✅ " if started else "ℹ️ ") + message,
            ephemeral=True,
        )

    @hostinghttp.command(
        name="parar",
        description="Para o servidor HTTP opcional.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def hostinghttp_stop(self, interaction: discord.Interaction) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message("Somente administradores.", ephemeral=True)
            return
        stopped, message = await self.stop_http()
        await interaction.response.send_message(
            ("✅ " if stopped else "ℹ️ ") + message,
            ephemeral=True,
        )

    @hostinghttp.command(
        name="status",
        description="Mostra o status do servidor HTTP opcional.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def hostinghttp_status(self, interaction: discord.Interaction) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message("Somente administradores.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Servidor HTTP: **{self.http_status()}**\n"
            "Endpoints: `/` e `/health`.",
            ephemeral=True,
        )

    @app_commands.command(
        name="hostinginfo",
        description="Mostra IP público, hostname, URL e portas da hospedagem.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def hostinginfo(self, interaction: discord.Interaction) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "Somente administradores podem consultar os dados da hospedagem.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        text = await self._build_info_text()

        await interaction.followup.send(
            text,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    cog = HostingInfo(bot)
    await bot.add_cog(cog)
    if os.getenv("AUTO_START_HTTP", "0").strip().lower() in {"1", "true", "yes", "sim"}:
        started, message = await cog.start_http()
        print(f"Servidor HTTP automático: {'OK' if started else 'falhou'} — {message}")
