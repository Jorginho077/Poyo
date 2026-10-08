"""Camada de dados do sistema Fogo.

Arquivo começando com "_" não é carregado como cog pelo bot.py (de propósito):
é só um módulo de apoio importado por cogs/fogo.py.

Usa o mesmo poyo.sqlite3 das boas-vindas, com tabelas próprias (prefixo fogo).
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

DB_PATH = Path(
    os.getenv(
        "FOGO_DB_FILE",
        Path(__file__).resolve().parent.parent / "poyo.sqlite3",
    )
)

# Quanto tempo um convite fica valendo até ser respondido.
CONVITE_VALIDADE_SEGUNDOS = 24 * 60 * 60


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
            CREATE TABLE IF NOT EXISTS fogo_convites (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                canal_id    INTEGER NOT NULL,
                mensagem_id INTEGER,
                de_id       INTEGER NOT NULL,
                para_id     INTEGER NOT NULL,
                criado_em   REAL    NOT NULL,
                expira_em   REAL    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'pendente'
            );

            CREATE TABLE IF NOT EXISTS fogos (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id           INTEGER NOT NULL,
                usuario_a          INTEGER NOT NULL,
                usuario_b          INTEGER NOT NULL,
                canal_id           INTEGER NOT NULL,
                nome               TEXT,
                sequencia          INTEGER NOT NULL DEFAULT 0,
                recorde            INTEGER NOT NULL DEFAULT 0,
                ativo              INTEGER NOT NULL DEFAULT 1,
                criado_em          REAL    NOT NULL,
                ultimo_acendimento TEXT
            );

            -- Uma dupla só pode ter UM fogo ativo por servidor.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fogo_dupla_ativa
                ON fogos (guild_id, usuario_a, usuario_b)
                WHERE ativo = 1;
            """
        )


def dupla(a: int, b: int) -> tuple[int, int]:
    """Ordena os IDs para que (A, B) e (B, A) sejam a mesma dupla."""
    return (a, b) if a < b else (b, a)


# ---------------------------------------------------------------- convites


def criar_convite(
    guild_id: int, canal_id: int, de_id: int, para_id: int
) -> int:
    agora = time.time()
    with _conexao() as con:
        cur = con.execute(
            """
            INSERT INTO fogo_convites
                (guild_id, canal_id, de_id, para_id, criado_em, expira_em)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                canal_id,
                de_id,
                para_id,
                agora,
                agora + CONVITE_VALIDADE_SEGUNDOS,
            ),
        )
        return int(cur.lastrowid)


def definir_mensagem_convite(convite_id: int, mensagem_id: int) -> None:
    with _conexao() as con:
        con.execute(
            "UPDATE fogo_convites SET mensagem_id = ? WHERE id = ?",
            (mensagem_id, convite_id),
        )


def cancelar_convite(convite_id: int) -> None:
    """Usado quando o envio da mensagem do convite falha."""
    with _conexao() as con:
        con.execute(
            "UPDATE fogo_convites SET status = 'cancelado' "
            "WHERE id = ? AND status = 'pendente'",
            (convite_id,),
        )


def obter_convite(convite_id: int) -> Optional[sqlite3.Row]:
    with _conexao() as con:
        return con.execute(
            "SELECT * FROM fogo_convites WHERE id = ?", (convite_id,)
        ).fetchone()


def convite_pendente_entre(
    guild_id: int, a: int, b: int
) -> Optional[sqlite3.Row]:
    """Convite ainda válido entre os dois, em qualquer direção."""
    with _conexao() as con:
        return con.execute(
            """
            SELECT * FROM fogo_convites
            WHERE guild_id = ? AND status = 'pendente' AND expira_em > ?
              AND ((de_id = ? AND para_id = ?) OR (de_id = ? AND para_id = ?))
            LIMIT 1
            """,
            (guild_id, time.time(), a, b, b, a),
        ).fetchone()


def convites_pendentes() -> list[sqlite3.Row]:
    with _conexao() as con:
        return con.execute(
            "SELECT * FROM fogo_convites WHERE status = 'pendente'"
        ).fetchall()


def marcar_expirado(convite_id: int) -> None:
    with _conexao() as con:
        con.execute(
            "UPDATE fogo_convites SET status = 'expirado' "
            "WHERE id = ? AND status = 'pendente'",
            (convite_id,),
        )


def recusar_convite(convite_id: int) -> str:
    """Retorna 'ok', 'expirado' ou 'ja_resolvido'."""
    with _conexao() as con:
        con.execute("BEGIN IMMEDIATE")
        c = con.execute(
            "SELECT * FROM fogo_convites WHERE id = ?", (convite_id,)
        ).fetchone()
        if c is None or c["status"] != "pendente":
            return "ja_resolvido"
        if c["expira_em"] <= time.time():
            con.execute(
                "UPDATE fogo_convites SET status = 'expirado' WHERE id = ?",
                (convite_id,),
            )
            return "expirado"
        con.execute(
            "UPDATE fogo_convites SET status = 'recusado' WHERE id = ?",
            (convite_id,),
        )
        return "ok"


def aceitar_convite(
    convite_id: int, canal_id: int
) -> tuple[str, Optional[sqlite3.Row]]:
    """Aceita o convite e cria o Fogo, tudo de forma atômica.

    Retorna (resultado, fogo). Resultados possíveis:
    'ok', 'expirado', 'ja_resolvido', 'ja_existe'.
    """
    with _conexao() as con:
        con.execute("BEGIN IMMEDIATE")
        c = con.execute(
            "SELECT * FROM fogo_convites WHERE id = ?", (convite_id,)
        ).fetchone()
        if c is None or c["status"] != "pendente":
            return "ja_resolvido", None
        if c["expira_em"] <= time.time():
            con.execute(
                "UPDATE fogo_convites SET status = 'expirado' WHERE id = ?",
                (convite_id,),
            )
            return "expirado", None

        a, b = dupla(c["de_id"], c["para_id"])
        existente = con.execute(
            "SELECT 1 FROM fogos "
            "WHERE guild_id = ? AND usuario_a = ? AND usuario_b = ? "
            "AND ativo = 1",
            (c["guild_id"], a, b),
        ).fetchone()
        if existente is not None:
            con.execute(
                "UPDATE fogo_convites SET status = 'cancelado' WHERE id = ?",
                (convite_id,),
            )
            return "ja_existe", None

        con.execute(
            "UPDATE fogo_convites SET status = 'aceito' WHERE id = ?",
            (convite_id,),
        )
        cur = con.execute(
            """
            INSERT INTO fogos
                (guild_id, usuario_a, usuario_b, canal_id, criado_em)
            VALUES (?, ?, ?, ?, ?)
            """,
            (c["guild_id"], a, b, canal_id, time.time()),
        )
        fogo = con.execute(
            "SELECT * FROM fogos WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return "ok", fogo


# -------------------------------------------------------------------- fogos


def fogo_ativo_entre(
    guild_id: int, a: int, b: int
) -> Optional[sqlite3.Row]:
    x, y = dupla(a, b)
    with _conexao() as con:
        return con.execute(
            "SELECT * FROM fogos WHERE guild_id = ? AND usuario_a = ? "
            "AND usuario_b = ? AND ativo = 1",
            (guild_id, x, y),
        ).fetchone()


def fogos_do_membro(guild_id: int, user_id: int) -> list[sqlite3.Row]:
    """Fogos ativos do membro, do maior para o menor."""
    with _conexao() as con:
        return con.execute(
            """
            SELECT * FROM fogos
            WHERE guild_id = ? AND ativo = 1
              AND (usuario_a = ? OR usuario_b = ?)
            ORDER BY sequencia DESC, criado_em ASC
            """,
            (guild_id, user_id, user_id),
        ).fetchall()
