"""Regras do nome do Fogo (liberado com 10 dias).

ATENÇÃO: só use este arquivo se o seu projeto NÃO tiver um `_fogo_nome.py`.
Se já existir, mantenha o seu (os textos de erro de lá ficam valendo).
"""

from __future__ import annotations

import re

NOME_MIN = 2
NOME_MAX = 24
NOME_MIN_DIAS = 10  # mantenha igual ao de _fogo_visual.py
PROPOSTA_VALIDADE_SEGUNDOS = 24 * 60 * 60

_PROIBIDOS = set("@<>*_~|`[]\\")
_LINK = re.compile(r"(https?://|www\.|discord\.gg|\.[a-z]{2,}/)", re.IGNORECASE)


def validar_nome(texto: str) -> tuple[bool, str]:
    """Devolve (True, nome_limpo) ou (False, mensagem_de_erro)."""
    nome = " ".join((texto or "").split())
    if len(nome) < NOME_MIN or len(nome) > NOME_MAX:
        return False, f"O nome precisa ter de {NOME_MIN} a {NOME_MAX} caracteres."
    if any(c in _PROIBIDOS for c in nome):
        return False, "Use só letras, números, espaços e emojis (nada de @ < > * _ ~ | ` [ ] \\)."
    if _LINK.search(nome):
        return False, "O nome não pode ter links."
    return True, nome
