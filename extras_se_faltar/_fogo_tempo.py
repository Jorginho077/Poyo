"""Data do dia do Fogo (fuso de Brasília, ou FOGO_TZ no .env).

ATENÇÃO: só use este arquivo se o seu projeto NÃO tiver um `_fogo_tempo.py`.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


def _fuso():
    nome = os.getenv("FOGO_TZ", "America/Sao_Paulo")
    if ZoneInfo is not None:
        try:
            return ZoneInfo(nome)
        except Exception:
            pass
    return timezone(timedelta(hours=-3))


def hoje() -> str:
    """Data (AAAA-MM-DD) de agora no fuso do Fogo."""
    return datetime.now(_fuso()).date().isoformat()
