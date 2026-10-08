"""Regras do nome do Fogo (liberado aos 10 dias de sequência)."""

from __future__ import annotations

import re

NOME_MIN_DIAS = 10  # dias de sequência para liberar o nome
NOME_MIN = 2  # tamanho mínimo do nome
NOME_MAX = 24  # tamanho máximo do nome
PROPOSTA_VALIDADE_SEGUNDOS = 24 * 60 * 60  # tempo para o par responder

# Caracteres que quebrariam a formatação ou criariam menções/links.
_PROIBIDOS = re.compile(r"[@<>`*_~|\\\[\]\n\r\t]")
_LINK = re.compile(r"(https?:|www\.|discord\.gg|\.gg/)", re.IGNORECASE)


def validar_nome(texto: str) -> tuple[bool, str]:
    """Retorna (True, nome_limpo) ou (False, mensagem_de_erro)."""
    nome = re.sub(r"\s+", " ", (texto or "").strip())

    if len(nome) < NOME_MIN:
        return False, f"O nome precisa ter pelo menos {NOME_MIN} letras."
    if len(nome) > NOME_MAX:
        return False, f"O nome pode ter no máximo {NOME_MAX} caracteres."
    if _PROIBIDOS.search(nome):
        return False, (
            "Esse nome tem símbolos que não posso usar "
            "(como @, <, >, *, _, ~, | ou `)."
        )
    if _LINK.search(nome):
        return False, "O nome do Fogo não pode ter links."
    return True, nome
