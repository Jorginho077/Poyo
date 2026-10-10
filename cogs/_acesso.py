"""Cargo com acesso a todos os comandos do Poyo.

Quem tem esse cargo passa em qualquer checagem de permissão dos comandos
(`has_permissions`, `,ptconfig`, painéis...), mesmo sem ser Administrador.

O que NÃO muda:
- o Poyo continua precisando das permissões dele (`bot_has_permissions`);
- a hierarquia de cargos nos comandos de moderação (não dá pra punir quem tem
  cargo igual ou acima);
- membro calado continua sem poder usar comandos.

Pra trocar o cargo sem mexer no código, use no .env:
CARGO_ACESSO_TOTAL=123456789012345678
"""

from __future__ import annotations

import os

import discord
from discord.ext import commands
from discord.utils import cached_property

CARGO_ACESSO_TOTAL = int(os.getenv("CARGO_ACESSO_TOTAL", "1558536440588148736"))


def tem_acesso_total(membro) -> bool:
    """True se o membro tem o cargo de acesso total."""
    cargos = getattr(membro, "roles", None)
    if not cargos:
        return False
    return any(cargo.id == CARGO_ACESSO_TOTAL for cargo in cargos)


class ContextoPoyo(commands.Context):
    """Contexto que enxerga o cargo de acesso total como todas as permissões."""

    @cached_property
    def permissions(self) -> discord.Permissions:
        if tem_acesso_total(self.author):
            return discord.Permissions.all()
        return commands.Context.permissions.function(self)
