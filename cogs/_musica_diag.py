# diagnostico da musica: pacotes instalados e permissoes que o poyo precisa
import asyncio
import importlib.metadata as md
import importlib.util
import shutil
from dataclasses import dataclass

import discord


@dataclass
class Item:
    nome: str
    ok: bool
    detalhe: str = ""     # versao encontrada ou o que falta
    ajuda: str = ""       # como resolver quando falta


# permissoes (atributo do discord.py, nome em portugues)
PERMS_TEXTO = (
    ("view_channel", "Ver canal"),
    ("send_messages", "Enviar mensagens"),
    ("attach_files", "Anexar arquivos"),
    ("external_emojis", "Usar emojis externos"),
)
PERMS_TEXTO_OPCIONAL = (
    ("manage_messages", "Gerenciar mensagens (apagar o comando digitado)"),
)
PERMS_VOZ = (
    ("view_channel", "Ver canal"),
    ("connect", "Conectar"),
    ("speak", "Falar"),
)


def permissoes_do_convite() -> discord.Permissions:
    """Tudo que a musica usa, pra montar o link de convite."""
    return discord.Permissions(
        view_channel=True,
        send_messages=True,
        send_messages_in_threads=True,
        attach_files=True,
        external_emojis=True,
        manage_messages=True,
        connect=True,
        speak=True,
    )


# ---------------------------------------------------------------- pacotes

def _versao(pacote: str) -> str | None:
    try:
        return md.version(pacote)
    except md.PackageNotFoundError:
        return None


def _versao_ffmpeg(caminho: str) -> str:
    import subprocess

    try:
        saida = subprocess.run(
            [caminho, "-version"], capture_output=True, text=True, timeout=4
        ).stdout
        primeira = (saida.splitlines() or [""])[0]
        partes = primeira.split()
        return partes[2] if len(partes) > 2 and partes[0] == "ffmpeg" else caminho
    except (OSError, subprocess.SubprocessError):
        return caminho


def pacotes(rapido: bool = False) -> list[Item]:
    """Confere tudo que a voz precisa. `rapido` nao executa o ffmpeg."""
    itens: list[Item] = []

    # discord.py 2.7+ (a voz do discord exige o protocolo DAVE)
    ver = discord.__version__
    try:
        maior_menor = tuple(int(x) for x in ver.split(".")[:2])
    except ValueError:
        maior_menor = (0, 0)
    itens.append(Item(
        "discord.py", maior_menor >= (2, 7), f"versão {ver}",
        'precisa ser 2.7 ou mais novo: `pip install -U "discord.py[voice]"`',
    ))

    nacl = _versao("PyNaCl")
    itens.append(Item(
        "PyNaCl", nacl is not None and importlib.util.find_spec("nacl") is not None,
        f"versão {nacl}" if nacl else "não instalado", "`pip install PyNaCl`",
    ))

    davey = _versao("davey")
    itens.append(Item(
        "davey", davey is not None and importlib.util.find_spec("davey") is not None,
        f"versão {davey}" if davey else "não instalado",
        "`pip install davey` (ou `pip install \"discord.py[voice]\"`)",
    ))

    ytdlp = _versao("yt-dlp")
    itens.append(Item(
        "yt-dlp", ytdlp is not None and importlib.util.find_spec("yt_dlp") is not None,
        f"versão {ytdlp}" if ytdlp else "não instalado", "`pip install -U yt-dlp`",
    ))

    caminho = shutil.which("ffmpeg")
    itens.append(Item(
        "ffmpeg", caminho is not None,
        (caminho if rapido else f"versão {_versao_ffmpeg(caminho)}") if caminho else "não encontrado",
        "instale no servidor (Linux: `apt install ffmpeg`)",
    ))

    # libopus: no linux vem do sistema; windows/macos ja vem com o discord.py
    try:
        opus = discord.opus.is_loaded() or discord.opus._load_default()
    except Exception:
        opus = False
    itens.append(Item(
        "libopus", bool(opus), "carregada" if opus else "não encontrada",
        "Linux: `apt install libopus0`",
    ))
    return itens


async def pacotes_async() -> list[Item]:
    return await asyncio.to_thread(pacotes)


def problemas_pacotes() -> list[Item]:
    """So o que impede de tocar (rapido, sem rodar o ffmpeg)."""
    return [i for i in pacotes(rapido=True) if not i.ok]


# ---------------------------------------------------------------- permissoes

def _faltando(perms: discord.Permissions, lista) -> list[str]:
    return [nome for attr, nome in lista if not getattr(perms, attr, False)]


def faltam_no_texto(canal, me) -> tuple[list[str], list[str]]:
    """(obrigatorias que faltam, opcionais que faltam) no canal de texto."""
    perms = canal.permissions_for(me)
    return _faltando(perms, PERMS_TEXTO), _faltando(perms, PERMS_TEXTO_OPCIONAL)


def faltam_na_voz(canal, me) -> list[str]:
    """Permissoes que faltam pra entrar e falar nesse canal de voz."""
    perms = canal.permissions_for(me)
    falta = _faltando(perms, PERMS_VOZ)
    lotado = bool(canal.user_limit) and len(canal.members) >= canal.user_limit
    if lotado and not (perms.move_members or perms.manage_channels or perms.administrator):
        falta.append("Mover membros (o canal está lotado)")
    return falta


# ---------------------------------------------------------------- texto do relatorio

def _linha(item: Item) -> str:
    if item.ok:
        return f"✅ **{item.nome}** · {item.detalhe}"
    return f"❌ **{item.nome}** · {item.detalhe} → {item.ajuda}"


def _linha_perm(lista, faltam: list[str]) -> str:
    return " · ".join(
        f"{'❌' if nome in faltam else '✅'} {nome}" for _, nome in lista
    )


def relatorio(guild: discord.Guild, canal_texto, canal_voz, itens: list[Item], client_id: int | None) -> list[str]:
    """Blocos de texto (cada um cabe em um TextDisplay)."""
    me = guild.me
    problemas = 0
    blocos: list[str] = []

    # --- pacotes
    problemas += sum(1 for i in itens if not i.ok)
    blocos.append("### 📦 Pacotes\n" + "\n".join(_linha(i) for i in itens))

    # --- texto
    if canal_texto is not None:
        obrig, opc = faltam_no_texto(canal_texto, me)
        problemas += len(obrig)
        txt = f"### 💬 Permissões em {canal_texto.mention}\n" + _linha_perm(PERMS_TEXTO, obrig)
        txt += "\n-# opcional: " + _linha_perm(PERMS_TEXTO_OPCIONAL, opc)
        blocos.append(txt)

    # --- voz
    if canal_voz is not None:
        falta = faltam_na_voz(canal_voz, me)
        problemas += len(falta)
        linha = _linha_perm(PERMS_VOZ, falta)
        if falta and any(f.startswith("Mover") for f in falta):
            linha += "\n❌ " + next(f for f in falta if f.startswith("Mover"))
        blocos.append(f"### 🔊 Permissões em {canal_voz.mention}\n{linha}")
    else:
        # ninguem em voz: varre os canais e mostra so os que tem problema
        ruins = []
        for canal in list(guild.voice_channels) + list(guild.stage_channels):
            falta = faltam_na_voz(canal, me)
            if falta:
                ruins.append(f"{canal.mention}: falta {', '.join(falta)}")
        if ruins:
            problemas += len(ruins)
            extra = f"\n-# …e mais {len(ruins) - 8}" if len(ruins) > 8 else ""
            blocos.append("### 🔊 Canais de voz com problema\n" + "\n".join(ruins[:8]) + extra)
        else:
            blocos.append("### 🔊 Canais de voz\n✅ Consigo entrar e falar em todos.")

    # --- resumo e link
    if problemas:
        resumo = f"⚠️ **{problemas} problema(s)** — resolva os itens com ❌ e rode de novo."
    else:
        resumo = "✅ **Tudo certo!** O Poyo tem tudo que precisa pra tocar música."
    if client_id:
        valor = permissoes_do_convite().value
        resumo += (
            "\n-# Permissões exatas da música (inteiro): "
            f"`{valor}` · [convite com elas](https://discord.com/oauth2/authorize"
            f"?client_id={client_id}&scope=bot+applications.commands&permissions={valor})"
        )
    blocos.insert(0, "## 🩺 Diagnóstico da música")
    blocos.append(resumo)
    return blocos
