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
        _migrar(con)


# Colunas adicionadas depois da Etapa 1 (ciclo diário).
_COLUNAS_NOVAS = {
    "dia_aberto": "TEXT",  # data (AAAA-MM-DD) do dia que está valendo
    "a_acendeu": "INTEGER NOT NULL DEFAULT 0",  # usuario_a acendeu hoje?
    "b_acendeu": "INTEGER NOT NULL DEFAULT 0",  # usuario_b acendeu hoje?
    "painel_id": "INTEGER",  # mensagem do painel do dia
    "encerrado_em": "REAL",  # quando o fogo apagou
}


def _migrar(con: sqlite3.Connection) -> None:
    existentes = {r["name"] for r in con.execute("PRAGMA table_info(fogos)")}
    for nome, tipo in _COLUNAS_NOVAS.items():
        if nome not in existentes:
            con.execute(f"ALTER TABLE fogos ADD COLUMN {nome} {tipo}")


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
    convite_id: int, canal_id: int, hoje: str
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
                (guild_id, usuario_a, usuario_b, canal_id, criado_em,
                 dia_aberto)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (c["guild_id"], a, b, canal_id, time.time(), hoje),
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


# ------------------------------------------------------------ ciclo diário


def obter_fogo(fogo_id: int) -> Optional[sqlite3.Row]:
    with _conexao() as con:
        return con.execute(
            "SELECT * FROM fogos WHERE id = ?", (fogo_id,)
        ).fetchone()


def fogos_ativos() -> list[sqlite3.Row]:
    with _conexao() as con:
        return con.execute(
            "SELECT * FROM fogos WHERE ativo = 1 ORDER BY id"
        ).fetchall()


def definir_painel(fogo_id: int, mensagem_id: int) -> None:
    with _conexao() as con:
        con.execute(
            "UPDATE fogos SET painel_id = ? WHERE id = ?",
            (mensagem_id, fogo_id),
        )


def dia_completo(fogo: sqlite3.Row) -> bool:
    return bool(fogo["a_acendeu"]) and bool(fogo["b_acendeu"])


def acender(
    fogo_id: int, user_id: int, hoje: str
) -> tuple[str, Optional[sqlite3.Row]]:
    """Registra o clique em "Acender o Fogo".

    Resultados: 'ok' (falta o outro), 'completo' (os dois acenderam, a
    sequência subiu), 'ja_acendeu', 'nao_participa', 'dia_encerrado',
    'inativo'.
    """
    with _conexao() as con:
        con.execute("BEGIN IMMEDIATE")
        f = con.execute(
            "SELECT * FROM fogos WHERE id = ?", (fogo_id,)
        ).fetchone()
        if f is None or not f["ativo"]:
            return "inativo", f
        if user_id not in (f["usuario_a"], f["usuario_b"]):
            return "nao_participa", f
        if f["dia_aberto"] != hoje:
            return "dia_encerrado", f

        coluna = "a_acendeu" if user_id == f["usuario_a"] else "b_acendeu"
        if f[coluna]:
            return "ja_acendeu", f

        con.execute(f"UPDATE fogos SET {coluna} = 1 WHERE id = ?", (fogo_id,))
        f = con.execute(
            "SELECT * FROM fogos WHERE id = ?", (fogo_id,)
        ).fetchone()

        if f["a_acendeu"] and f["b_acendeu"]:
            nova = f["sequencia"] + 1
            con.execute(
                """
                UPDATE fogos
                SET sequencia = ?, recorde = MAX(recorde, ?),
                    ultimo_acendimento = ?
                WHERE id = ?
                """,
                (nova, nova, hoje, fogo_id),
            )
            f = con.execute(
                "SELECT * FROM fogos WHERE id = ?", (fogo_id,)
            ).fetchone()
            return "completo", f
        return "ok", f


def virar_dia(
    fogo_id: int, hoje: str
) -> tuple[str, Optional[sqlite3.Row]]:
    """Faz a virada do dia para um Fogo.

    Resultados:
    - 'nada'     o dia ainda é o mesmo;
    - 'novo_dia' os dois tinham acendido: abre o dia de hoje (painel novo);
    - 'apagou'   faltou alguém: o Fogo se apaga (a sequência fica gravada).

    Se o bot ficou fora do ar e passaram dias sem painel, quem já tinha
    completado o último dia aberto não é punido: o Fogo só é reaberto.
    """
    with _conexao() as con:
        con.execute("BEGIN IMMEDIATE")
        f = con.execute(
            "SELECT * FROM fogos WHERE id = ?", (fogo_id,)
        ).fetchone()
        if f is None or not f["ativo"]:
            return "nada", f

        dia = f["dia_aberto"]
        if dia is not None and dia >= hoje:
            return "nada", f

        # Fogo da Etapa 1 (sem dia aberto) ou dia completo: abre hoje.
        if dia is None or (f["a_acendeu"] and f["b_acendeu"]):
            con.execute(
                """
                UPDATE fogos
                SET dia_aberto = ?, a_acendeu = 0, b_acendeu = 0,
                    painel_id = NULL
                WHERE id = ?
                """,
                (hoje, fogo_id),
            )
            return "novo_dia", con.execute(
                "SELECT * FROM fogos WHERE id = ?", (fogo_id,)
            ).fetchone()

        con.execute(
            "UPDATE fogos SET ativo = 0, encerrado_em = ? WHERE id = ?",
            (time.time(), fogo_id),
        )
        return "apagou", con.execute(
            "SELECT * FROM fogos WHERE id = ?", (fogo_id,)
        ).fetchone()
