"""Configurações do Fogo guardadas no banco (painel `,fogoconfig`):
horário da virada do dia e onde os avisos são enviados (DM e/ou servidor).

Horário da virada do dia do Fogo.

Padrão: 00:00. Com outro horário (ex.: 06:00), o "dia" do Fogo vai das 06:00
às 05:59 do dia seguinte. Fica gravado na tabela `fogo_config` (chave
`hora_virada`) e vale para todos os servidores.

Este módulo substitui `_fogo_tempo` nas chamadas de `hoje()`. Com 00:00 ele
devolve exatamente o que o `_fogo_tempo` devolve.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

from . import _fogo_db as db
from . import _fogo_tempo as tempo

CHAVE = "hora_virada"
CHAVE_DM = "enviar_dm"
CHAVE_SERVIDOR = "enviar_servidor"
_minutos = 0  # minutos depois da meia-noite em que o dia vira
_dm = True  # avisos na DM de cada pessoa
_servidor = False  # painel no canal do Fogo


def _fuso():
    nome = os.getenv("FOGO_TZ", "America/Sao_Paulo")
    if ZoneInfo is not None:
        try:
            return ZoneInfo(nome)
        except Exception:
            pass
    return timezone(timedelta(hours=-3))


def interpretar(texto: str) -> tuple[bool, int]:
    """Lê "6", "6h", "06:30" ou "6h30". Devolve (valido, minutos)."""
    m = re.fullmatch(r"\s*(\d{1,2})\s*(?:[:hH]\s*(\d{2})?)?\s*", texto or "")
    if not m:
        return False, 0
    horas, minutos = int(m[1]), int(m[2] or 0)
    if horas > 23 or minutos > 59:
        return False, 0
    return True, horas * 60 + minutos


def rotulo(minutos: int | None = None) -> str:
    """Horário como "HH:MM"."""
    m = _minutos if minutos is None else minutos
    return f"{m // 60:02d}:{m % 60:02d}"


def rotulo_fim() -> str:
    """Último minuto do dia ("23:59" quando a virada é 00:00)."""
    return rotulo((_minutos - 1) % 1440)


def carregar() -> None:
    """Lê o horário gravado no banco (chamado quando o bot liga)."""
    global _minutos, _dm, _servidor
    valido, minutos = interpretar(db.config_obter(CHAVE) or "00:00")
    _minutos = minutos if valido else 0
    _dm = (db.config_obter(CHAVE_DM) or "1") == "1"
    _servidor = (db.config_obter(CHAVE_SERVIDOR) or "0") == "1"


def dm_ativo() -> bool:
    return _dm


def servidor_ativo() -> bool:
    return _servidor


def definir_envio(dm: bool, servidor: bool) -> None:
    """Liga/desliga os avisos na DM e no servidor."""
    global _dm, _servidor
    db.config_gravar(CHAVE_DM, "1" if dm else "0")
    db.config_gravar(CHAVE_SERVIDOR, "1" if servidor else "0")
    _dm, _servidor = dm, servidor


def definir(minutos: int) -> None:
    """Grava o novo horário e passa a usá-lo na hora."""
    global _minutos
    db.config_gravar(CHAVE, rotulo(minutos))
    _minutos = minutos


def hoje() -> str:
    """Data (AAAA-MM-DD) do dia do Fogo que está valendo agora."""
    if _minutos == 0:
        return tempo.hoje()
    agora = datetime.now(_fuso()) - timedelta(minutes=_minutos)
    return agora.date().isoformat()
