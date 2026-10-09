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
from pathlib import Path
from typing import Sequence

import discord

from . import _fogo_hora as hora

# ---------------------------------------------------------------- emojis

EMOJI_FOGO = os.getenv("FOGO_EMOJI", "🔥")
EMOJI_FOGO_FELIZ = os.getenv("FOGO_EMOJI_FELIZ", "🔥")  # fogo feliz e animado
EMOJI_FOGO_TRISTE = os.getenv("FOGO_EMOJI_TRISTE", "🥶")  # fogo com frio
EMOJI_CONVITE = os.getenv("FOGO_EMOJI_CONVITE", "💌")
EMOJI_CORACAO = os.getenv("FOGO_EMOJI_CORACAO", "💖")  # criado / batizado
EMOJI_ESPERA = os.getenv("FOGO_EMOJI_ESPERA", "⏳")  # "ainda não acendeu"

NOME_MIN_DIAS = 10  # mantenha igual ao de _fogo_nome.py

COR_FOGO = discord.Colour.from_rgb(255, 120, 30)  # laranja chama
COR_FRIO = discord.Colour.from_rgb(120, 190, 255)  # azul gelo
COR_NEUTRA = discord.Colour.from_rgb(110, 110, 120)


# GIF de brilhos que fica em cima do botão "Acender o Fogo".
ENFEITE_NOME = "fogo_brilho.gif"
ENFEITE_CAMINHO = Path(__file__).resolve().parent.parent / "assets" / ENFEITE_NOME


def enfeite_existe() -> bool:
    return ENFEITE_CAMINHO.is_file()


def arquivo_enfeite() -> discord.File:
    """Um discord.File novo (cada envio gasta o seu)."""
    return discord.File(ENFEITE_CAMINHO, filename=ENFEITE_NOME)


def dias(n: int) -> str:
    return f"{n} dia" if n == 1 else f"{n} dias"


# ---------------------------------------------------------------- cartão


class CartaoFogo(discord.ui.LayoutView):
    """Container com blocos de texto separados e, opcionalmente, botões.

    Sem cor lateral (o parâmetro `cor` é aceito só por compatibilidade).
    `midia` é o nome de um anexo ("attachment://...") mostrado acima dos botões.
    """

    def __init__(
        self,
        *blocos: str,
        cor: discord.Colour = COR_FOGO,
        botoes: Sequence[discord.ui.Item] = (),
        midia: str | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)

        itens: list[discord.ui.Item] = []
        for indice, bloco in enumerate(blocos):
            if indice:
                itens.append(discord.ui.Separator(visible=False))
            itens.append(discord.ui.TextDisplay(bloco))

        if midia:
            itens.append(discord.ui.Separator(visible=False))
            itens.append(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(f"attachment://{midia}")
                )
            )

        if botoes:
            itens.append(discord.ui.Separator(visible=False))
            itens.append(discord.ui.ActionRow(*botoes))

        self.add_item(discord.ui.Container(*itens))


# ----------------------------------------------------------------- textos


def texto_convite(de: str, para: str) -> list[str]:
    return [
        f"## {EMOJI_CONVITE} Convite de Fogo",
        (
            f"{de} quer acender um **Fogo** com {para}!\n\n"
            "Todo dia, os dois acendem juntos. "
            "Se um esquecer, o Fogo apaga."
        ),
        f"{para}, você topa? {EMOJI_FOGO_FELIZ}\n"
        "-# Expira em 24 horas.",
    ]


def cartao_fogo_criado(a: str, b: str) -> CartaoFogo:
    return CartaoFogo(
        f"# {EMOJI_CORACAO} Fogo criado!",
        (
            f"## {a} + {b}\n"
            f"{EMOJI_FOGO} **Sequência:** {dias(0)}\n\n"
            "Olhem a **DM**: o botão **Acender o Fogo** já chegou. "
            "Quando os dois clicarem, a sequência começa."
        ),
        cor=COR_FOGO,
    )


def cartao_convite_recusado(de: str, para: str) -> CartaoFogo:
    return CartaoFogo(
        "## 🧊 Convite recusado",
        f"{para} recusou o convite de {de}.",
        cor=COR_FRIO,
    )


def cartao_convite_expirado(de: str, para: str) -> CartaoFogo:
    return CartaoFogo(
        "## ⏳ Convite expirado",
        f"O convite de {de} para {para} expirou.",
        cor=COR_NEUTRA,
    )


def cartao_ja_existe(a: str, b: str) -> CartaoFogo:
    return CartaoFogo(
        f"## {EMOJI_FOGO} Vocês já têm um Fogo",
        f"{a} + {b} já têm um Fogo ativo.",
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
        return "A primeira chama pegou!"
    if sequencia < 7:
        return "A chama está crescendo!"
    if sequencia < 30:
        return "O fogo está firme!"
    return "O fogo está queimando forte!"


def blocos_painel(f) -> list[str]:
    """Texto do painel do dia (o GIF e o botão são montados pelo cog)."""
    marca_a = "✅" if f["a_acendeu"] else "⬜"
    marca_b = "✅" if f["b_acendeu"] else "⬜"
    seq = f"Sequência de {dias(f['sequencia'])} · " if f["sequencia"] else ""
    return [
        "### Acender o Fogo\n"
        f"{marca_a} <@{f['usuario_a']}>\n"
        f"{marca_b} <@{f['usuario_b']}>\n"
        f"-# {seq}Até {hora.rotulo_fim()}"
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
                "Vocês desbloquearam o **nome do Fogo**!"
            )
        else:
            blocos.append(
                "O Fogo ainda não tem nome."
            )
    fim = f"-# Volte às {hora.rotulo()}."
    blocos.append(f"{fim}\n{rodape}" if rodape else fim)

    return CartaoFogo(*blocos, cor=COR_FOGO, botoes=botoes)


def cartao_fogo_apagado(f, rodape: str | None = None) -> CartaoFogo:
    if f["sequencia"] > 0:
        fim = f"A sequência de {dias(f['sequencia'])} acabou."
    else:
        fim = "Apagou antes de começar."
    return CartaoFogo(
        f"## {EMOJI_FOGO_TRISTE} O fogo apagou...",
        (
            f"{_dupla_txt(f)}{_linha_nome(f)}\n"
            f"{fim}\n\n"
            "O Poyo está com frio..."
        ),
        (
            "-# Recomecem com `,fogo @membro`."
            + (f"\n{rodape}" if rodape else "")
        ),
        cor=COR_FRIO,
    )


def cartao_fogo_removido(f) -> CartaoFogo:
    """Painel antigo de um Fogo removido por um administrador."""
    return CartaoFogo(
        "## 🧊 Fogo removido",
        f"{_dupla_txt(f)}{_linha_nome(f)}\n"
        "Esse Fogo foi encerrado por um administrador.",
        cor=COR_NEUTRA,
    )


def cartao_painel_encerrado(f) -> CartaoFogo:
    """O painel antigo, sem botão, depois que o tempo acabou."""
    return CartaoFogo(
        f"## {EMOJI_FOGO_TRISTE} Tempo esgotado",
        f"{_dupla_txt(f)}{_linha_nome(f)}\n"
        "Painel encerrado.",
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
            "Sugira um nome. Seu par precisa aceitar."
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
            f"<@{proponente_id}> sugeriu o nome **{nome}**.\n\n"
            f"<@{parceiro}>, aceita?"
        ),
        "-# Vale por 24 horas.",
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
        "-# O nome agora aparece nas mensagens do Fogo.",
        cor=COR_FOGO,
    )


def cartao_nome_recusado(f, nome: str, quem_recusou: int) -> CartaoFogo:
    return CartaoFogo(
        "## 🧊 Nome recusado",
        f"<@{quem_recusou}> recusou o nome **{nome}**. "
        "Sugiram outro com `,nomefogo`.",
        cor=COR_FRIO,
    )


def cartao_proposta_encerrada(motivo: str) -> CartaoFogo:
    return CartaoFogo("## ⏳ Sugestão encerrada", motivo, cor=COR_NEUTRA)
