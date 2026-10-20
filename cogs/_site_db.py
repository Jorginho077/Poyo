"""Banco da API do site: sessões, estados do OAuth e dados por servidor.

Arquivo próprio (site.sqlite3) para não misturar com o poyo.sqlite3 do bot.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from . import _site_config as cfg

DB_PATH = Path(
    os.getenv(
        "SITE_DB_FILE",
        Path(__file__).resolve().parent.parent / "site.sqlite3",
    )
)

STATE_VALIDADE = 10 * 60  # segundos para concluir o login no Discord


@contextmanager
def _conexao() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def iniciar() -> None:
    """Cria as tabelas, se ainda não existirem. Pode rodar várias vezes."""
    with _conexao() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessoes (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                nome       TEXT    NOT NULL,
                avatar     TEXT,
                guilds     TEXT    NOT NULL,
                criada_em  REAL    NOT NULL,
                expira_em  REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_states (
                state     TEXT PRIMARY KEY,
                expira_em REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS site_guilds (
                guild_id    INTEGER PRIMARY KEY,
                bloqueado   INTEGER NOT NULL DEFAULT 0,
                primeira_vez REAL   NOT NULL
            );
            CREATE TABLE IF NOT EXISTS site_modulos (
                guild_id INTEGER NOT NULL,
                chave    TEXT    NOT NULL,
                estado   TEXT    NOT NULL,
                PRIMARY KEY (guild_id, chave)
            );
            """
        )


def _hash(token: str) -> str:
    # Só o hash fica no banco: se vazar o arquivo, os tokens não vazam.
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- OAuth ----------
def criar_state() -> str:
    state = secrets.token_urlsafe(24)
    with _conexao() as con:
        con.execute(
            "INSERT INTO oauth_states (state, expira_em) VALUES (?, ?)",
            (state, time.time() + STATE_VALIDADE),
        )
    return state


def consumir_state(state: str) -> bool:
    """Vale uma vez só. Devolve False se não existe ou expirou."""
    if not state:
        return False
    with _conexao() as con:
        linha = con.execute(
            "SELECT expira_em FROM oauth_states WHERE state = ?", (state,)
        ).fetchone()
        con.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    return bool(linha) and linha["expira_em"] >= time.time()


# ---------- sessões ----------
def criar_sessao(
    user_id: int, nome: str, avatar: Optional[str], guilds: list[dict]
) -> str:
    token = secrets.token_urlsafe(32)
    agora = time.time()
    with _conexao() as con:
        con.execute(
            "INSERT INTO sessoes VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _hash(token),
                user_id,
                nome,
                avatar,
                json.dumps(guilds),
                agora,
                agora + cfg.SESSAO_DIAS * 86400,
            ),
        )
    return token


def sessao_por_token(token: str) -> Optional[dict]:
    if not token:
        return None
    with _conexao() as con:
        linha = con.execute(
            "SELECT * FROM sessoes WHERE token_hash = ?", (_hash(token),)
        ).fetchone()
    if linha is None or linha["expira_em"] < time.time():
        return None
    return {
        "user_id": linha["user_id"],
        "nome": linha["nome"],
        "avatar": linha["avatar"],
        "guilds": json.loads(linha["guilds"]),
    }


def apagar_sessao(token: str) -> None:
    with _conexao() as con:
        con.execute("DELETE FROM sessoes WHERE token_hash = ?", (_hash(token),))


def limpar_expirados() -> None:
    agora = time.time()
    with _conexao() as con:
        con.execute("DELETE FROM sessoes WHERE expira_em < ?", (agora,))
        con.execute("DELETE FROM oauth_states WHERE expira_em < ?", (agora,))


# ---------- dados por servidor ----------
def registrar_guild(guild_id: int) -> None:
    with _conexao() as con:
        con.execute(
            "INSERT OR IGNORE INTO site_guilds (guild_id, primeira_vez) VALUES (?, ?)",
            (guild_id, time.time()),
        )


def modulos_guild(guild_id: int) -> dict[str, str]:
    """Estado de cada módulo; os que ninguém mexeu usam o padrão do .env."""
    with _conexao() as con:
        linhas = con.execute(
            "SELECT chave, estado FROM site_modulos WHERE guild_id = ?",
            (guild_id,),
        ).fetchall()
    salvos = {l["chave"]: l["estado"] for l in linhas}
    return {m: salvos.get(m, cfg.ESTADO_PADRAO) for m in cfg.MODULOS}


def status_guild(guild_id: int) -> str:
    """'bloqueado' se o dono do bot bloqueou; 'novo' se ninguém configurou; senão 'ativo'."""
    with _conexao() as con:
        g = con.execute(
            "SELECT bloqueado FROM site_guilds WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        mexeu = con.execute(
            "SELECT 1 FROM site_modulos WHERE guild_id = ? LIMIT 1", (guild_id,)
        ).fetchone()
    if g is not None and g["bloqueado"]:
        return "bloqueado"
    return "ativo" if mexeu else "novo"
