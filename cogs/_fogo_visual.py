"""Visual do sistema Fogo: emojis, textos e cartões (Components V2).

Para trocar os emojis por emojis customizados do servidor, basta editar as
constantes abaixo (ou definir as variáveis de ambiente correspondentes), por
exemplo: FOGO_EMOJI_FELIZ="<a:fogo_feliz:123456789012345678>"

Etapa 5: a cog cogs/fogo_emojis.py preenche sozinha, ao ligar o bot, o Poyo
feliz / com frio (emojis do aplicativo, enviados a partir de assets/) e os
emojis do servidor (poyo_coracao, poyo_piscadinha, poyo_sono). Qualquer valor
definido no .env ou aqui tem prioridade sobre o automático.
"""

from __future__ import annotations

import os
from typing import Sequence

import discord

# ---------------------------------------------------------------- emojis

EMOJI_FOGO = os.getenv("FOGO_EMOJI", "🔥")
EMOJI_FOGO_FELIZ = os.getenv("FOGO_EMOJI_FELIZ", "🔥")  # fogo feliz e animado
EMOJI_FOGO_TRISTE = os.getenv("FOGO_EMOJI_TRISTE", "🥶")  # fogo com frio
EMOJI_CONVITE = os.getenv("FOGO_EMOJI_CONVITE", "💌")
EMOJI_CORACAO = os.getenv("FOGO_EMOJI_CORACAO", "💖")  # criado / batizado
EMOJI_ESPERA = os.getenv("FOGO_EMOJI_ESPERA", "⏳")  # "ainda não acendeu"

# Imagem do Poyo ao lado do texto dos cartões (URL). Preenchida pela cog
# fogo_emojis; sem ela, os cartões saem só com texto.
IMG_FELIZ: str | None = os.getenv("FOGO_IMG_FELIZ") or None
IMG_TRISTE: str | None = os.getenv("FOGO_IMG_TRISTE") or None

NOME_MIN_DIAS = 10  # mantenha igual ao de _fogo_nome.py

COR_FOGO = discord.Colour.from_rgb(255, 120, 30)  # laranja chama
COR_FRIO = discord.Colour.from_rgb(120, 190, 255)  # azul gelo
COR_NEUTRA = discord.Colour.from_rgb(110, 110, 120)


def dias(n: int) -> str:
    return f"{n} dia" if n == 1 else f"{n} dias"


# ---------------------------------------------------------------- cartão


class CartaoFogo(discord.ui.LayoutView):
    """Container com blocos de texto separados e, opcionalmente, botões."""

    def __init__(
        self,
        *blocos: str,
        cor: discord.Colour = COR_FOGO,
        botoes: Sequence[discord.ui.Item] = (),
        timeout: float | None = None,
        imagem: str | None = None,
    ) -> None:
        super().__init__(timeout=timeout)

        itens: list[discord.ui.Item] = []
        restantes = list(blocos)

        # Com imagem, o título e o texto principal ganham o Poyo ao lado.
        if imagem and len(restantes) >= 2:
            itens.append(
                discord.ui.Section(
                    restantes[0],
                    restantes[1],
                    accessory=discord.ui.Thumbnail(imagem),
                )
            )
            restantes = restantes[2:]
            primeiro = False
        else:
            primeiro = True

        for indice, bloco in enumerate(restantes):
            if indice or not primeiro:
                itens.append(discord.ui.Separator())
            itens.append(discord.ui.TextDisplay(bloco))

        if botoes:
            itens.append(discord.ui.Separator())
            itens.append(discord.ui.ActionRow(*botoes))

        self.add_item(discord.ui.Container(*itens, accent_colour=cor))


# ----------------------------------------------------------------- textos


def texto_convite(de: str, para: str) -> list[str]:
    return [
        f"## {EMOJI_CONVITE} Convite de Fogo",
        (
            f"{de} quer acender um **Fogo** com {para}!\n\n"
            "Todo dia, às **00:00**, os dois precisam reacender a chama "
            "juntos. Quanto mais dias seguidos, mais forte o Fogo queima... "
            "mas se um dos dois esquecer, ele apaga."
        ),
        f"{para}, você topa? {EMOJI_FOGO_FELIZ}\n"
        "-# O convite expira em 24 horas.",
    ]


def cartao_fogo_criado(a: str, b: str) -> CartaoFogo:
    return CartaoFogo(
        f"## {EMOJI_CORACAO} Fogo criado!",
        (
            f"{a} + {b}\n"
            f"{EMOJI_FOGO} **Sequência:** {dias(0)}\n\n"
            "O Poyo acendeu a primeira faísca de vocês! "
            "Olhem a **DM** de cada um: o painel com o botão "
            "**Acender o Fogo** já chegou lá. Quando os dois clicarem, "
            "a sequência começa."
        ),
        cor=COR_FOGO,
        imagem=IMG_FELIZ,
    )


def cartao_convite_recusado(de: str, para: str) -> CartaoFogo:
    return CartaoFogo(
        "## 🧊 Convite recusado",
        f"{para} recusou o convite de {de}. "
        "O Fogo não foi aceso desta vez.",
        cor=COR_FRIO,
    )


def cartao_convite_expirado(de: str, para: str) -> CartaoFogo:
    return CartaoFogo(
        "## ⏳ Convite expirado",
        f"O convite de {de} para {para} não foi respondido a tempo.",
        cor=COR_NEUTRA,
    )


def cartao_ja_existe(a: str, b: str) -> CartaoFogo:
    return CartaoFogo(
        f"## {EMOJI_FOGO} Vocês já têm um Fogo",
        f"{a} + {b} já estão com um Fogo ativo por aqui. "
        "Cuidem bem dessa chama!",
        cor=COR_FOGO,
    )


def cartao_aviso(titulo: str, texto: str) -> CartaoFogo:
    return CartaoFogo(f"## {titulo}", texto, cor=COR_NEUTRA)


# ------------------------------------------------- ciclo diário (Etapa 2)


def _dupla_txt(f) -> str:
    return f"<@{f['usuario_a']}> + <@{f['usuario_b']}>"


def _linha_nome(f) -> str:
    return f"\n{EMOJI_FOGO} **Fogo:** {f['nome']}" if f["nome"] else ""


def frase_chama(sequencia: int) -> str:
    """Frase do Poyo conforme a força da sequência."""
    if sequencia <= 1:
        return "A primeira chama pegou! Agora é só manter acesa."
    if sequencia < 7:
        return "A chama está crescendo!"
    if sequencia < 30:
        return "O fogo está firme e animado!"
    return "O fogo está queimando forte!"


def blocos_painel(f) -> list[str]:
    """Textos do painel do dia (o botão é montado pelo cog)."""
    a_ok, b_ok = bool(f["a_acendeu"]), bool(f["b_acendeu"])
    pendente = f"{EMOJI_ESPERA} ainda não acendeu"
    status_a = "✅ acendeu" if a_ok else pendente
    status_b = "✅ acendeu" if b_ok else pendente
    seq = (
        f"**Sequência:** {dias(f['sequencia'])}"
        if f["sequencia"]
        else "**Sequência:** ainda não começou"
    )
    return [
        f"## {EMOJI_FOGO_FELIZ} Hora de acender o Fogo!",
        (
            f"{_dupla_txt(f)}{_linha_nome(f)}\n{seq}\n\n"
            f"<@{f['usuario_a']}>: {status_a}\n"
            f"<@{f['usuario_b']}>: {status_b}"
        ),
        "-# Os dois precisam acender até 23:59 de hoje. "
        "Se um dos dois faltar, o Fogo apaga.",
    ]


def cartao_fogo_aceso(
    f,
    botoes: Sequence[discord.ui.Item] = (),
    rodape: str | None = None,
) -> CartaoFogo:
    blocos = [
        f"## {EMOJI_FOGO_FELIZ} Fogo aceso!",
        (
            f"{_dupla_txt(f)}{_linha_nome(f)}\n"
            f"**Sequência:** {dias(f['sequencia'])}\n\n"
            f"{frase_chama(f['sequencia'])}"
        ),
    ]

    if botoes:  # o nome está liberado e o Fogo ainda não tem um
        if f["sequencia"] == NOME_MIN_DIAS:
            blocos.append(
                f"### 🎉 {dias(NOME_MIN_DIAS)} de Fogo!\n"
                "Vocês desbloquearam o **nome do Fogo**. "
                "Escolham um nome para essa sequência!"
            )
        else:
            blocos.append(
                "O Fogo de vocês ainda não tem nome. "
                "Que tal batizar essa chama?"
            )
    fim = "-# Volte às 00:00 para reacender a chama."
    blocos.append(f"{fim}\n{rodape}" if rodape else fim)

    return CartaoFogo(
        *blocos, cor=COR_FOGO, botoes=botoes, imagem=IMG_FELIZ
    )


def cartao_fogo_apagado(f, rodape: str | None = None) -> CartaoFogo:
    if f["sequencia"] > 0:
        fim = f"A sequência de {dias(f['sequencia'])} chegou ao fim."
    else:
        fim = "O fogo apagou antes mesmo de a sequência começar."
    return CartaoFogo(
        f"## {EMOJI_FOGO_TRISTE} O fogo apagou...",
        (
            f"{_dupla_txt(f)}{_linha_nome(f)}\n"
            f"{fim}\n\n"
            "O Poyo está com frio..."
        ),
        (
            "-# Que tal chamar a pessoa de novo com `,fogo @membro`?"
            + (f"\n{rodape}" if rodape else "")
        ),
        cor=COR_FRIO,
        imagem=IMG_TRISTE,
    )


def cartao_painel_encerrado(f) -> CartaoFogo:
    """O painel antigo, sem botão, depois que o tempo acabou."""
    return CartaoFogo(
        f"## {EMOJI_FOGO_TRISTE} Tempo esgotado",
        f"{_dupla_txt(f)}{_linha_nome(f)}\n"
        "Esse painel não aceita mais cliques.",
        cor=COR_NEUTRA,
    )


# ------------------------------------------------ nome do Fogo (Etapa 3)


def cartao_convite_nome(f, botoes: Sequence[discord.ui.Item]) -> CartaoFogo:
    atual = f"**{f['nome']}**" if f["nome"] else "ainda sem nome"
    return CartaoFogo(
        "## 🏷️ Nome do Fogo",
        (
            f"{_dupla_txt(f)}\n"
            f"**Sequência:** {dias(f['sequencia'])}\n"
            f"**Nome atual:** {atual}\n\n"
            "Clique no botão para sugerir um nome. "
            "Seu par precisa aceitar para ele valer."
        ),
        cor=COR_FOGO,
        botoes=botoes,
    )


def cartao_proposta_nome(
    f, proponente_id: int, nome: str, botoes: Sequence[discord.ui.Item]
) -> CartaoFogo:
    parceiro = (
        f["usuario_b"] if proponente_id == f["usuario_a"] else f["usuario_a"]
    )
    return CartaoFogo(
        "## 🏷️ Sugestão de nome",
        (
            f"<@{proponente_id}> sugeriu chamar o Fogo de vocês de "
            f"**{nome}**.\n\n<@{parceiro}>, o que você acha?"
        ),
        "-# A sugestão vale por 24 horas.",
        cor=COR_FOGO,
        botoes=botoes,
    )


def cartao_fogo_batizado(f) -> CartaoFogo:
    return CartaoFogo(
        f"## {EMOJI_CORACAO} Fogo batizado!",
        (
            f"{_dupla_txt(f)}\n"
            f"{EMOJI_FOGO} **Fogo:** {f['nome']}\n"
            f"**Sequência:** {dias(f['sequencia'])}"
        ),
        "-# A partir de agora o nome aparece em todas as mensagens do Fogo.",
        cor=COR_FOGO,
        imagem=IMG_FELIZ,
    )


def cartao_nome_recusado(f, nome: str, quem_recusou: int) -> CartaoFogo:
    return CartaoFogo(
        "## 🧊 Nome recusado",
        f"<@{quem_recusou}> não curtiu o nome **{nome}**. "
        "Vocês podem sugerir outro com `,nomefogo`.",
        cor=COR_FRIO,
    )


def cartao_proposta_encerrada(motivo: str) -> CartaoFogo:
    return CartaoFogo("## ⏳ Sugestão encerrada", motivo, cor=COR_NEUTRA)
