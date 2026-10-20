"""Rotas HTTP da API do site (contrato /api/v1 que o front já espera).

Etapa 1: saúde, login com Discord, /me, /me/guilds, /guilds/:id e
/admin/guilds. As demais rotas entram nas próximas etapas.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import discord
from aiohttp import web

from . import _site_auth as auth
from . import _site_config as cfg
from . import _site_db as db

log = logging.getLogger("poyo.site")


class Erro(Exception):
    def __init__(self, status: int, mensagem: str) -> None:
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem


def _json_erro(status: int, mensagem: str) -> web.Response:
    return web.json_response({"erro": mensagem}, status=status)


# ---------- middleware: erros + CORS ----------
@web.middleware
async def middleware(request: web.Request, handler) -> web.StreamResponse:
    origem = request.headers.get("Origin", "").rstrip("/")
    permitido = origem in cfg.origens_permitidas()

    if request.method == "OPTIONS":
        resp: web.StreamResponse = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except Erro as e:
            resp = _json_erro(e.status, e.mensagem)
        except web.HTTPException as e:
            if e.status < 400:
                raise  # redirecionamentos (login do Discord)
            resp = _json_erro(e.status, e.reason or "Erro")
        except Exception:
            log.exception("Erro na rota %s", request.path)
            resp = _json_erro(500, "Erro interno")

    if permitido:
        resp.headers["Access-Control-Allow-Origin"] = origem
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        resp.headers["Access-Control-Max-Age"] = "600"
        resp.headers["Vary"] = "Origin"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# ---------- ajudantes ----------
def _bot(request: web.Request) -> discord.Client:
    bot = request.app["bot"]
    if not bot.is_ready():
        raise Erro(503, "O bot ainda está iniciando")
    return bot


def usuario_atual(request: web.Request) -> dict:
    cab = request.headers.get("Authorization", "")
    if not cab.lower().startswith("bearer "):
        raise Erro(401, "Não logado")
    sessao = db.sessao_por_token(cab[7:].strip())
    if sessao is None:
        raise Erro(401, "Sessão expirada")
    return sessao


async def eh_dono_do_bot(bot: discord.Client, user_id: int) -> bool:
    if user_id in cfg.DONOS_DO_BOT:
        return True
    try:  # dono (ou equipe) da aplicação no Discord
        return await bot.is_owner(discord.Object(id=user_id))  # type: ignore[arg-type]
    except Exception:
        return False


async def papel_na_guild(guild: discord.Guild, user_id: int) -> Optional[str]:
    """'dono', 'admin' ou None (sem acesso). Conferido ao vivo no Discord."""
    if guild.owner_id == user_id:
        return "dono"
    membro = guild.get_member(user_id)
    if membro is None:
        try:
            membro = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.HTTPException):
            return None
    perms = membro.guild_permissions
    return "admin" if (perms.administrator or perms.manage_guild) else None


async def guild_autorizada(
    request: web.Request,
) -> tuple[discord.Guild, str, dict]:
    bot = _bot(request)
    usuario = usuario_atual(request)
    try:
        gid = int(request.match_info["id"])
    except (KeyError, ValueError):
        raise Erro(404, "Servidor não encontrado")
    guild = bot.get_guild(gid)
    if guild is None:
        raise Erro(404, "Servidor não encontrado")
    papel = await papel_na_guild(guild, usuario["user_id"])
    if papel is None and await eh_dono_do_bot(bot, usuario["user_id"]):
        papel = "admin"  # dono do bot vê tudo, mas só o dono do servidor restaura
    if papel is None:
        raise Erro(403, "Você não gerencia esse servidor")
    return guild, papel, usuario


def guild_json(guild: discord.Guild) -> dict[str, Any]:
    dono = guild.owner.name if guild.owner else str(guild.owner_id)
    return {
        "id": str(guild.id),
        "nome": guild.name,
        "dono": dono,
        "membros": guild.member_count or 0,
        "uso": "frio",  # vem das estatísticas (etapa 4)
        "status": db.status_guild(guild.id),
        "ultima": "—",  # vem das estatísticas (etapa 4)
        "modulos": db.modulos_guild(guild.id),
    }


# ---------- rotas ----------
async def saude(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "botPronto": request.app["bot"].is_ready()})


async def auth_login(request: web.Request) -> web.Response:
    raise web.HTTPFound(auth.url_login(db.criar_state()))


async def auth_callback(request: web.Request) -> web.Response:
    def volta(**dados: str) -> web.HTTPFound:
        frag = "&".join(f"{k}={v}" for k, v in dados.items())
        return web.HTTPFound(f"{cfg.SITE_URL}/entrar#{frag}")

    code = request.query.get("code", "")
    if request.query.get("error") or not code:
        raise volta(erro="cancelado")
    if not db.consumir_state(request.query.get("state", "")):
        raise volta(erro="expirado")
    try:
        perfil, guilds = await auth.trocar_codigo(code)
        uid = int(perfil["id"])
    except Exception:
        log.exception("Falha no login com Discord")
        raise volta(erro="falhou")

    nome = perfil.get("global_name") or perfil.get("username") or str(uid)
    token = db.criar_sessao(uid, nome, perfil.get("avatar"), guilds)
    raise volta(token=token)


async def auth_sair(request: web.Request) -> web.Response:
    cab = request.headers.get("Authorization", "")
    if cab.lower().startswith("bearer "):
        db.apagar_sessao(cab[7:].strip())
    return web.Response(status=204)


async def me(request: web.Request) -> web.Response:
    bot = _bot(request)
    u = usuario_atual(request)
    return web.json_response(
        {
            "id": str(u["user_id"]),
            "nome": u["nome"],
            "avatar": auth.url_avatar(u["user_id"], u["avatar"]),
            "donoDoBot": await eh_dono_do_bot(bot, u["user_id"]),
        }
    )


async def me_guilds(request: web.Request) -> web.Response:
    bot = _bot(request)
    u = usuario_atual(request)
    saida = []
    for g in u["guilds"]:
        if not auth.pode_gerenciar(g):
            continue
        guild = bot.get_guild(int(g["id"]))
        saida.append(
            {
                "id": g["id"],
                "nome": g["name"],
                "membros": (guild.member_count or 0) if guild else 0,
                "uso": "frio",
                "botPresente": guild is not None,
                "podeGerenciar": True,
                "papel": "dono" if g.get("owner") else "admin",
            }
        )
    saida.sort(key=lambda x: (not x["botPresente"], x["nome"].lower()))
    return web.json_response(saida)


async def guild_detalhe(request: web.Request) -> web.Response:
    guild, papel, _ = await guild_autorizada(request)
    return web.json_response({**guild_json(guild), "papel": papel})


async def admin_guilds(request: web.Request) -> web.Response:
    bot = _bot(request)
    u = usuario_atual(request)
    if not await eh_dono_do_bot(bot, u["user_id"]):
        raise Erro(403, "Só o dono do bot")
    busca = request.query.get("busca", "").strip().lower()
    lista = [guild_json(g) for g in bot.guilds if busca in g.name.lower()]
    lista.sort(key=lambda g: g["nome"].lower())
    return web.json_response(lista)


def criar_app(bot: discord.Client) -> web.Application:
    app = web.Application(middlewares=[middleware])
    app["bot"] = bot
    p = "/api/v1"
    app.add_routes(
        [
            web.get(f"{p}/saude", saude),
            web.get(f"{p}/auth/discord/login", auth_login),
            web.get(f"{p}/auth/discord/callback", auth_callback),
            web.post(f"{p}/auth/sair", auth_sair),
            web.get(f"{p}/me", me),
            web.get(f"{p}/me/guilds", me_guilds),
            web.get(f"{p}/guilds/{{id}}", guild_detalhe),
            web.get(f"{p}/admin/guilds", admin_guilds),
        ]
    )
    return app
