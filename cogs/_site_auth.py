"""Login com Discord (OAuth2): monta a URL e troca o código pelo perfil."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

from aiohttp import ClientSession, ClientTimeout

from . import _site_config as cfg

DISCORD_API = "https://discord.com/api/v10"
ADMINISTRATOR = 0x8
MANAGE_GUILD = 0x20


class ErroOAuth(Exception):
    pass


def redirect_uri() -> str:
    return f"{cfg.API_URL}/api/v1/auth/discord/callback"


def url_login(state: str) -> str:
    consulta = urlencode(
        {
            "client_id": cfg.CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect_uri(),
            "scope": "identify guilds",
            "state": state,
        }
    )
    return f"https://discord.com/oauth2/authorize?{consulta}"


def url_avatar(user_id: int, avatar: Optional[str]) -> Optional[str]:
    if not avatar:
        return None
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png?size=128"


def pode_gerenciar(guild_snapshot: dict) -> bool:
    """Dono do servidor, ou com permissão de Administrador / Gerenciar Servidor."""
    if guild_snapshot.get("owner"):
        return True
    try:
        perms = int(guild_snapshot.get("permissions", 0))
    except (TypeError, ValueError):
        return False
    return bool(perms & ADMINISTRATOR or perms & MANAGE_GUILD)


async def trocar_codigo(code: str) -> tuple[dict, list[dict]]:
    """Troca o código do callback pelo perfil e pela lista de servidores.

    O access_token do usuário NÃO é guardado: usamos uma vez e revogamos.
    """
    async with ClientSession(timeout=ClientTimeout(total=15)) as s:
        async with s.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id": cfg.CLIENT_ID,
                "client_secret": cfg.CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(),
            },
        ) as r:
            if r.status != 200:
                raise ErroOAuth(f"token: HTTP {r.status}")
            access = (await r.json())["access_token"]

        cab = {"Authorization": f"Bearer {access}"}
        try:
            async with s.get(f"{DISCORD_API}/users/@me", headers=cab) as r:
                if r.status != 200:
                    raise ErroOAuth(f"perfil: HTTP {r.status}")
                perfil = await r.json()
            async with s.get(f"{DISCORD_API}/users/@me/guilds", headers=cab) as r:
                if r.status != 200:
                    raise ErroOAuth(f"guilds: HTTP {r.status}")
                guilds = await r.json()
        finally:
            try:  # melhor esforço: derruba o token que não vamos mais usar
                await s.post(
                    f"{DISCORD_API}/oauth2/token/revoke",
                    data={
                        "client_id": cfg.CLIENT_ID,
                        "client_secret": cfg.CLIENT_SECRET,
                        "token": access,
                    },
                )
            except Exception:
                pass

    enxuto = [
        {
            "id": g["id"],
            "name": g["name"],
            "owner": bool(g.get("owner")),
            "permissions": g.get("permissions", "0"),
        }
        for g in guilds
    ]
    return perfil, enxuto
