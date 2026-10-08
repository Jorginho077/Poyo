"""Relógio do sistema Fogo: "hoje" sempre no horário de Brasília.

Troque o fuso com a variável FOGO_TZ no .env, se precisar.
Se o sistema não tiver o banco de fusos (tzdata), cai para UTC-3 fixo
(o Brasil não usa horário de verão hoje).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    FUSO = ZoneInfo(os.getenv("FOGO_TZ", "America/Sao_Paulo"))
except Exception:  # tzdata ausente ou fuso inválido
    FUSO = timezone(timedelta(hours=-3), "BRT")


def agora() -> datetime:
    return datetime.now(FUSO)


def hoje() -> str:
    """Data de hoje (fuso do Fogo) no formato AAAA-MM-DD."""
    return agora().date().isoformat()


def ontem(base: str | None = None) -> str:
    d = date.fromisoformat(base) if base else agora().date()
    return (d - timedelta(days=1)).isoformat()
