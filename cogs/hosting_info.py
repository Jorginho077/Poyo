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

    @app_commands.command(
        name="hostinginfo",
        description="Mostra IP público, hostname, URL e portas da hospedagem.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def hostinginfo(self, interaction: discord.Interaction) -> None:
        permissions = getattr(interaction.user, "guild_permissions", None)
        if not permissions or not permissions.administrator:
            await interaction.response.send_message(
                "Somente administradores podem consultar os dados da hospedagem.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        public_ip = await asyncio.to_thread(_public_ip_sync)
        hostname = socket.gethostname()
        local_host = os.getenv("HOST", os.getenv("HOSTNAME", "não informado"))
        ports = await asyncio.to_thread(_local_listening_ports)
        public_url, url_source = _discover_public_url()
        public_port, port_source = _discover_port(ports)

        port_text = ", ".join(str(port) for port in ports) if ports else "nenhuma detectada"
        ip_text = public_ip or "não foi possível detectar"
        url_text = public_url or "não determinável automaticamente"

        text = (
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
            "não existe uma porta pública do bot. "
            "As portas listadas são apenas as que estão abertas localmente; NAT, "
            "proxy ou firewall podem impedir acesso externo."
        )

        await interaction.followup.send(
            text,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HostingInfo(bot))
