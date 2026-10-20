"""Configuração da API do site (lida do .env).

Arquivo começando com "_" não é carregado como cog (de propósito):
é só um módulo de apoio importado por cogs/site_api.py.
"""

from __future__ import annotations

import os


def _lista_ids(nome: str) -> set[int]:
    bruto = os.getenv(nome, "")
    return {int(x) for x in bruto.replace(" ", "").split(",") if x.isdigit()}


def _lista_urls(nome: str) -> set[str]:
    bruto = os.getenv(nome, "")
    return {x.strip().rstrip("/") for x in bruto.split(",") if x.strip()}


# Onde a API escuta. Mantenha 127.0.0.1: quem expõe para a internet é o
# túnel/proxy, não o Python.
HOST = os.getenv("SITE_API_HOST", "127.0.0.1")
PORT = int(os.getenv("SITE_API_PORT", "8787"))

# URL pública do site (front) e da API. Sem barra no final.
SITE_URL = os.getenv("SITE_URL", "").strip().rstrip("/")
API_URL = os.getenv("SITE_API_URL", "").strip().rstrip("/")

# Origens extras aceitas no CORS (ex.: http://localhost:5173 para testar).
ORIGENS_EXTRAS = _lista_urls("SITE_ORIGENS_EXTRAS")

# OAuth2 do Discord (Developer Portal > OAuth2).
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()

# Donos do bot além do dono da aplicação no Discord (opcional).
DONOS_DO_BOT = _lista_ids("SITE_DONOS_DO_BOT")

SESSAO_DIAS = int(os.getenv("SITE_SESSAO_DIAS", "7"))

# Estado de um módulo que ainda ninguém mexeu, em servidor novo.
ESTADO_PADRAO = os.getenv("SITE_MODULO_PADRAO", "bloqueado")

MODULOS = (
    "tickets",
    "parcerias",
    "fogo",
    "musica",
    "boas-vindas",
    "moderacao",
    "vip",
    "ia",
    "backup",
)
ESTADOS = ("liberado", "leitura", "bloqueado")

if ESTADO_PADRAO not in ESTADOS:
    ESTADO_PADRAO = "bloqueado"


def origens_permitidas() -> set[str]:
    return ({SITE_URL} if SITE_URL else set()) | ORIGENS_EXTRAS


def faltando() -> list[str]:
    """Nomes das variáveis obrigatórias que ainda não foram preenchidas."""
    obrigatorias = {
        "SITE_URL": SITE_URL,
        "SITE_API_URL": API_URL,
        "DISCORD_CLIENT_ID": CLIENT_ID,
        "DISCORD_CLIENT_SECRET": CLIENT_SECRET,
    }
    return [nome for nome, valor in obrigatorias.items() if not valor]
