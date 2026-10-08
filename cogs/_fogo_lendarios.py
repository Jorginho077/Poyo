"""Lendários do Fogo (Etapa 4): regras, texto da bio e cartões.

As duas primeiras duplas a chegarem a 500 dias de sequência viram os
Lendários do Fogo, e ficam registradas na bio do Poyo para sempre.

Este módulo não toca no banco nem na API do Discord: só tem constantes,
funções de texto (fáceis de testar) e os cartões visuais.
"""

from __future__ import annotations

import os
from typing import Optional, Sequence

from . import _fogo_visual as visual

# Dias de sequência para entrar nos Lendários. A variável de ambiente existe
# só para teste (ex.: FOGO_LENDARIO_DIAS=3). Em produção, deixe em 500.
LENDARIO_DIAS = int(os.getenv("FOGO_LENDARIO_DIAS", "500"))
LENDARIO_VAGAS = 2  # duas duplas, e só

EMOJI_LENDARIO = os.getenv("FOGO_EMOJI_LENDARIO", "🏆")

# O Discord limita a bio (descrição) de um aplicativo a 400 caracteres.
BIO_LIMITE = 400
BIO_TITULO = "🔥 Lendários do Fogo"  # também serve de marcador na bio

# Quantas vezes tentamos mandar o aviso no canal antes de desistir dele
# (a bio é independente: continua sendo tentada).
ANUNCIO_MAX_TENTATIVAS = 5


# ------------------------------------------------------------------- bio


def _tam(texto: str) -> int:
    """Tamanho como o Discord conta (emojis como 🔥 valem 2)."""
    return len(texto.encode("utf-16-le")) // 2


def _cortar(texto: str, limite: int) -> str:
    """Corta o texto para caber em `limite`, terminando em "…"."""
    if _tam(texto) <= limite:
        return texto
    texto = texto.rstrip()
    while texto and _tam(texto) + 1 > limite:
        texto = texto[:-1]
    return texto.rstrip() + "…" if texto else ""


def abertura_da_bio(atual: str, abertura_env: Optional[str] = None) -> str:
    """Texto de abertura que deve ser preservado na bio.

    - Se FOGO_BIO_ABERTURA estiver definido, ele manda.
    - Senão, é tudo o que está na bio antes do bloco dos Lendários (ou a bio
      inteira, se o bloco ainda não existe). Assim o que você já escreveu lá
      nunca é apagado, e editar a abertura à mão continua funcionando.
    """
    if abertura_env is not None and abertura_env.strip():
        return abertura_env.strip()
    atual = atual or ""
    posicao = atual.find(BIO_TITULO)
    texto = atual[:posicao] if posicao >= 0 else atual
    return texto.strip()


def montar_bio(
    abertura: str, lendarios: Sequence[tuple[str, str, int]]
) -> str:
    """Monta a bio: abertura + bloco dos Lendários (que tem prioridade).

    `lendarios` é uma lista de (nome_a, nome_b, dias), já na ordem das vagas.
    Se não couber em 400 caracteres, a abertura é encurtada com "…"; o bloco
    dos Lendários nunca é cortado.
    """
    linhas = [BIO_TITULO] + [
        f"@{a} + @{b} — {visual.dias(d)}" for a, b, d in lendarios
    ]
    bloco = "\n".join(linhas)
    if _tam(bloco) > BIO_LIMITE:  # só aconteceria com nomes absurdos
        bloco = _cortar(bloco, BIO_LIMITE)

    abertura = (abertura or "").strip()
    espaco = BIO_LIMITE - _tam(bloco) - 2  # 2 = linha em branco de separação
    if not abertura or espaco <= 0:
        return bloco
    abertura = _cortar(abertura, espaco)
    if not abertura or abertura == "…":
        return bloco
    return f"{abertura}\n\n{bloco}"


# --------------------------------------------------------------- cartões


def _dupla(m) -> str:
    return f"<@{m['usuario_a']}> + <@{m['usuario_b']}>"


def _linha_nome(m) -> str:
    if m["nome"]:
        return f"\n{visual.EMOJI_FOGO} **Fogo:** {m['nome']}"
    return ""


def _ordinal(posicao: int) -> str:
    return f"{posicao}ª"


def cartao_lendario(m, ocupadas: int) -> visual.CartaoFogo:
    """Anúncio de quem conquistou uma das vagas de Lendário."""
    restantes = LENDARIO_VAGAS - ocupadas
    if restantes <= 0:
        situacao = "As duas vagas dos Lendários agora estão preenchidas."
    else:
        situacao = (
            "Ainda resta 1 vaga de Lendário do Fogo."
            if restantes == 1
            else f"Ainda restam {restantes} vagas de Lendário do Fogo."
        )
    return visual.CartaoFogo(
        f"## {EMOJI_LENDARIO} Lendários do Fogo!",
        (
            f"{_dupla(m)}{_linha_nome(m)}\n"
            f"**Sequência:** {visual.dias(m['dias'])}\n\n"
            f"{visual.EMOJI_FOGO_FELIZ} Vocês são a **{_ordinal(m['posicao'])} "
            "dupla da história do Poyo** a chegar aos "
            f"{visual.dias(m['dias'])} de Fogo! O nome de vocês fica "
            "registrado **para sempre** na bio do Poyo."
        ),
        f"-# {situacao}",
        cor=visual.COR_FOGO,
    )


def cartao_marco_sem_vaga(m, ja_lendaria: bool) -> visual.CartaoFogo:
    """500 dias sem vaga: comemora, mas explica que não entra na bio."""
    if ja_lendaria:
        motivo = (
            "Vocês já são Lendários do Fogo, e cada dupla só ocupa "
            "uma vaga. Mas que chama longa!"
        )
    else:
        motivo = (
            "As duas vagas dos Lendários do Fogo já tinham sido "
            "preenchidas por outras duplas, mas vocês fizeram história "
            "do mesmo jeito!"
        )
    return visual.CartaoFogo(
        f"## {visual.EMOJI_FOGO_FELIZ} {visual.dias(m['dias'])} de Fogo!",
        (
            f"{_dupla(m)}{_linha_nome(m)}\n"
            f"**Sequência:** {visual.dias(m['dias'])}\n\n{motivo}"
        ),
        cor=visual.COR_FOGO,
    )


def cartao_hall(
    lendarios: Sequence, melhor_ativo=None
) -> visual.CartaoFogo:
    """Lista dos Lendários (comando ,lendarios)."""
    por_posicao = {m["posicao"]: m for m in lendarios}
    linhas: list[str] = []
    for posicao in range(1, LENDARIO_VAGAS + 1):
        m = por_posicao.get(posicao)
        if m is None:
            linhas.append(f"**{_ordinal(posicao)} vaga:** aberta, esperando uma dupla!")
        else:
            linhas.append(
                f"**{_ordinal(posicao)} vaga:** {_dupla(m)}"
                f"{' · ' + m['nome'] if m['nome'] else ''} "
                f"— {visual.dias(m['dias'])}"
            )

    blocos = [
        f"## {EMOJI_LENDARIO} Lendários do Fogo",
        "\n".join(linhas),
    ]

    abertas = LENDARIO_VAGAS - len(lendarios)
    if abertas > 0:
        rodape = (
            f"-# Para entrar: chegar a {visual.dias(LENDARIO_DIAS)} de "
            f"sequência. Vagas abertas: {abertas} de {LENDARIO_VAGAS}."
        )
        if melhor_ativo is not None:
            rodape += (
                f"\n-# Fogo mais perto agora: <@{melhor_ativo['usuario_a']}> + "
                f"<@{melhor_ativo['usuario_b']}> com "
                f"{visual.dias(melhor_ativo['sequencia'])}."
            )
        blocos.append(rodape)
    else:
        blocos.append(
            "-# As duas vagas foram preenchidas e estão registradas "
            "para sempre na bio do Poyo."
        )
    return visual.CartaoFogo(*blocos, cor=visual.COR_FOGO)
