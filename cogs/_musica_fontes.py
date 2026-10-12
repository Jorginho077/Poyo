# busca e resolucao de audio SEM youtube: soundcloud, bandcamp,
# links diretos (mp3/ogg/wav/flac/m4a) e radios (streams http)
import asyncio
import logging
import re
from dataclasses import dataclass, field

import yt_dlp

log = logging.getLogger("bot.musica.fontes")

# extensoes de audio aceitas como link direto
EXTENSOES_DIRETAS = (".mp3", ".ogg", ".oga", ".wav", ".flac", ".m4a", ".opus", ".aac")

_YDL_BASE = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": False,
    "default_search": None,
    # nunca youtube: se um link do youtube cair aqui, a extração falha
    # e o cog responde que a fonte nao e suportada
    "extract_flat": False,
    "socket_timeout": 15,
    "nocheckcertificate": True,
}


@dataclass
class Faixa:
    titulo: str
    url_stream: str          # url que o ffmpeg vai abrir
    url_pagina: str | None = None
    duracao: float | None = None
    autor: str | None = None
    pedido_por: str | None = None
    fonte: str = "desconhecida"

    @property
    def duracao_txt(self) -> str:
        if not self.duracao:
            return "ao vivo"
        m, s = divmod(int(self.duracao), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class FonteDesligada(Exception):
    """A fonte pedida esta desligada no ,pmconfig do servidor."""


class FonteNaoSuportada(Exception):
    """Link de uma fonte que o Poyo nao usa (ex.: YouTube)."""


class NadaEncontrado(Exception):
    pass


def _bloqueado_youtube(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be|music\.youtube\.com)", url, re.I))


def _extrair(query: str, cfg: dict) -> list[Faixa]:
    """Roda em thread separada: yt-dlp e bloqueante."""
    if _bloqueado_youtube(query):
        raise FonteNaoSuportada("YouTube não é suportado pelo Poyo.")

    opcoes = dict(_YDL_BASE)
    alvo = query
    fonte = "link"

    if re.match(r"https?://", query):
        host = re.sub(r"^https?://(www\.)?", "", query).split("/")[0].lower()
        if "soundcloud" in host:
            if not cfg.get("soundcloud", True):
                raise FonteDesligada("SoundCloud está desligado neste servidor.")
            fonte = "soundcloud"
        elif "bandcamp" in host:
            if not cfg.get("bandcamp", True):
                raise FonteDesligada("Bandcamp está desligado neste servidor.")
            fonte = "bandcamp"
        elif query.lower().split("?")[0].endswith(EXTENSOES_DIRETAS):
            fonte = "direto"
        else:
            # qualquer outro http: tratamos como stream/radio ou pagina generica
            fonte = "radio"
    else:
        # texto livre: o Bandcamp não tem busca por nome no yt-dlp, só
        # link direto de faixa/álbum — então a busca por nome só passa
        # pelo SoundCloud mesmo.
        if cfg.get("soundcloud", True):
            alvo = f"scsearch1:{query}"
            fonte = "soundcloud"
        elif cfg.get("bandcamp", True):
            raise FonteDesligada(
                "O Bandcamp não tem busca por nome — manda o link direto "
                "da música ou do álbum. Ou ligue o SoundCloud pra buscar "
                "por nome."
            )
        else:
            raise FonteDesligada(
                "SoundCloud e Bandcamp estão desligados. "
                "Envie um link direto de áudio ou de rádio."
            )

    try:
        with yt_dlp.YoutubeDL(opcoes) as ydl:
            info = ydl.extract_info(alvo, download=False)
    except yt_dlp.utils.DownloadError as e:
        log.info("extracao falhou para %r: %s", alvo, e)
        raise NadaEncontrado(query) from e

    if not info:
        raise NadaEncontrado(query)

    entradas = info.get("entries") or [info]
    faixas: list[Faixa] = []
    for entrada in entradas:
        if not entrada:
            continue
        url_stream = entrada.get("url")
        if not url_stream:
            formatos = entrada.get("formats") or []
            if formatos:
                url_stream = formatos[-1].get("url")
        if not url_stream:
            continue
        faixas.append(
            Faixa(
                titulo=entrada.get("title") or "Sem título",
                url_stream=url_stream,
                url_pagina=entrada.get("webpage_url") or (query if re.match(r"https?://", query) else None),
                duracao=entrada.get("duration"),
                autor=entrada.get("uploader") or entrada.get("artist"),
                fonte=fonte,
            )
        )

    if not faixas:
        raise NadaEncontrado(query)
    return faixas


async def buscar(query: str, cfg: dict, pedido_por: str) -> list[Faixa]:
    faixas = await asyncio.to_thread(_extrair, query, cfg)
    for faixa in faixas:
        faixa.pedido_por = pedido_por
    return faixas
