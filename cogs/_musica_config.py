# configuracao do sistema de musica, salva por servidor em json
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("bot.musica.config")

CONFIG_FILE = Path(os.getenv("MUSICA_CONFIG_FILE", "musica_config.json"))

# padrao de cada servidor: soundcloud e bandcamp ligados, modo prefixo
CFG_BLANK = {
    "dj_role": None,        # id do cargo de dj (None = desligado)
    "modo_247": False,      # ficar no canal mesmo com a fila vazia
    "soundcloud": True,     # busca no soundcloud ligada por padrao
    "bandcamp": True,       # busca no bandcamp ligada por padrao
    "modo_slash": False,    # False = comandos de prefixo, True = so slash
    "volume": 80,           # volume padrao (0-100)
}


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        bak = path.with_name(path.name + ".bak")
        log.error("%s ilegivel (%s); copia guardada em %s", path, e, bak)
        try:
            path.replace(bak)
        except OSError:
            pass
        return {}


def _write_json(path: Path, data: dict):
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as e:
        log.warning("nao consegui salvar %s: %s", path, e)


class ConfigMusica:
    """Le e grava a configuracao de musica de cada servidor."""

    def __init__(self) -> None:
        self._dados: dict = _read_json(CONFIG_FILE)

    def _salvar(self) -> None:
        _write_json(CONFIG_FILE, self._dados)

    def obter(self, guild_id: int) -> dict:
        bruto = self._dados.get(str(guild_id), {})
        cfg = dict(CFG_BLANK)
        for chave in CFG_BLANK:
            if chave in bruto:
                cfg[chave] = bruto[chave]
        return cfg

    def definir(self, guild_id: int, chave: str, valor) -> dict:
        if chave not in CFG_BLANK:
            raise KeyError(chave)
        gid = str(guild_id)
        cfg = self.obter(guild_id)
        cfg[chave] = valor
        self._dados[gid] = cfg
        self._salvar()
        return cfg
