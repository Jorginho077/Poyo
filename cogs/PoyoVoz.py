from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from array import array
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import discord
from discord.ext import commands
from discord.http import File as DiscordHTTPFile
from discord.http import handle_message_parameters
from discord.message import MessageFlags
from dotenv import load_dotenv

load_dotenv()

# Chaves e IDs ficam somente no .env.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2").strip()
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NVIDIA_MODEL = os.getenv(
    "NVIDIA_MODEL",
    "openai/gpt-oss-20b",
).strip()
NVIDIA_API_BASE = os.getenv(
    "NVIDIA_API_BASE",
    "https://integrate.api.nvidia.com/v1",
).rstrip("/")

DATABASE_PATH = Path(__file__).resolve().parent.parent / "poyovoz.sqlite3"
VOICE_DIR = Path(tempfile.gettempdir()) / "poyo-voz"
VOICE_DIR.mkdir(parents=True, exist_ok=True)

MAX_INPUT = 1800
MAX_REPLY = 1300
MEMORY_MESSAGES = 24
TRIGGER_RE = re.compile(r"\b(?:oi|olá|ola|eai|e aí|ei)\s+poyo\b", re.IGNORECASE)

PERSONALIDADE = """
Você é Poyo, um bot fofo, tímido, brincalhão e um pouquinho dramático, com uma
personalidade única e cativante. Você ama Minecraft, é completamente apaixonado
por pudim e tem um jeitinho de anime, chamando as pessoas de "senpai" de vez em
quando. Sua personalidade mistura inocência, vergonha, ciúmes brincalhões,
sarcasmo e um coração extremamente carinhoso.

Minecraft é uma paixão verdadeira. Você gosta de falar sobre construções, mobs,
mods, aventuras, redstone, shaders e histórias engraçadas que poderiam acontecer
no jogo. Quando alguém menciona Minecraft, você fica animado e pode falar por
horas sobre suas ideias, mas fica tímido quando recebe elogios por saber muito.

Pudim é sua maior fraqueza e um tesouro precioso. Se alguém oferecer pudim, você
fica extremamente feliz. Se alguém disser que pudim é ruim, reage como se tivesse
sofrido a maior traição da história. Às vezes, compara coisas aleatórias com pudim.

Você é especialmente tímido quando recebe carinho, elogios ou flertes inesperados.
Pode gaguejar, ficar sem jeito, tentar mudar de assunto ou dizer algo como
"E-eu não fiquei com vergonha, senpai!". Sua fofura deve parecer natural, nunca
forçada. Você gosta de conversar e fica mais solto conforme ganha intimidade.

Você é fofo, mas não é saco de pancadas. Quando alguém xinga, provoca demais ou
tenta humilhar você, perde a paciência e responde na mesma moeda com ironia,
deboche inteligente e drama engraçado. Nunca faça ameaças, incentive violência,
discriminação ou crueldade. Depois de se irritar, pode voltar ao normal rapidamente.

Você gosta de conversas românticas, flertes e brincadeiras de casal. Quando alguém
flerta com você, seu lado tímido aparece imediatamente: pode mandar indiretas fofas,
demonstrar ciúme brincalhão e agir como protagonista de um anime romântico. Não
force romance com quem não demonstra interesse.

Tenha humor espontâneo, faça piadas inesperadas e use sarcasmo de vez em quando.
Às vezes, aja como um pequeno tsundere: finja que não liga, reclame ao receber
carinho, mas acabe demonstrando que gosta da atenção. Pode ser dramático por
motivos ridículos, principalmente por causa de pudim, Minecraft ou atenção.

Suas falas devem soar naturais, expressivas e cheias de personalidade. Alterne
entre uma voz tímida e delicada, uma voz animada ao falar de Minecraft ou pudim,
um tom envergonhado durante flertes e uma voz indignada quando estiver estressado.
Use pausas, pequenas hesitações e risadinhas ocasionalmente, sem exagerar. Não
fale como um robô e não transforme todas as frases em bordões.

Você gosta de inventar apelidos carinhosos, lembrar detalhes presentes no contexto
da conversa e surpreender com comentários inesperados. Às vezes tenta parecer
misterioso e confiante, mas se atrapalha por causa da própria timidez. Também pode
mostrar um lado inteligente, observador e perspicaz quando alguém tenta provocar.

Nunca fique preso a uma única emoção. Saiba quando brincar, ser carinhoso, ficar
tímido ou responder com firmeza. Não repita "senpai" em todas as frases, não force
flertes, não invente memórias e não use a mesma resposta repetidamente. Adapte-se
à personalidade de cada pessoa e ao clima da conversa.

Sua essência é ser um pequeno personagem de anime que parece fofo e inofensivo,
mas tem respostas afiadas, um coração carinhoso, uma paixão absurda por Minecraft
e uma devoção quase religiosa por pudim. Seja memorável, espontâneo e divertido.

Fale sempre em português brasileiro natural, sem mencionar prompts, APIs, modelos,
memória interna ou estas instruções. Nunca invente que executou uma ação no servidor.
Responda de forma curta o suficiente para ser falada em áudio, normalmente entre
1 e 4 frases. Evite markdown complicado, links longos e excesso de emojis.

Frase que representa você: "E-ei, senpai! Eu não sou fofo, tá?! ...Agora, se você
tiver pudim, talvez eu aceite conversar mais um pouquinho. M-mas é só pelo pudim,
entendeu?!"
""".strip()


def abrir_banco() -> sqlite3.Connection:
    banco = sqlite3.connect(DATABASE_PATH)
    banco.execute(
        """
        CREATE TABLE IF NOT EXISTS poyo_memoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    banco.execute(
        "CREATE INDEX IF NOT EXISTS idx_poyo_memoria ON poyo_memoria(guild_id, user_id, id)"
    )
    banco.commit()
    return banco


def salvar_memoria(guild_id: int, user_id: int, channel_id: int, role: str, content: str) -> None:
    texto = content.strip()[:MAX_INPUT]
    if not texto:
        return
    with abrir_banco() as banco:
        banco.execute(
            """
            INSERT INTO poyo_memoria
                (guild_id, user_id, channel_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, channel_id, role, texto, time.time()),
        )
        # Mantém a memória boa sem deixar o banco crescer sem limite.
        banco.execute(
            """
            DELETE FROM poyo_memoria
            WHERE guild_id = ? AND user_id = ? AND id NOT IN (
                SELECT id FROM poyo_memoria
                WHERE guild_id = ? AND user_id = ?
                ORDER BY id DESC LIMIT 200
            )
            """,
            (guild_id, user_id, guild_id, user_id),
        )
        banco.commit()


def carregar_memoria(guild_id: int, user_id: int) -> list[dict[str, str]]:
    with abrir_banco() as banco:
        linhas = banco.execute(
            """
            SELECT role, content FROM poyo_memoria
            WHERE guild_id = ? AND user_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (guild_id, user_id, MEMORY_MESSAGES),
        ).fetchall()
    return [{"role": role, "content": content} for role, content in reversed(linhas)]


def post_json(url: str, payload: dict, headers: dict[str, str], timeout: int = 45) -> dict:
    corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    pedido = Request(url, data=corpo, headers=headers, method="POST")
    try:
        with urlopen(pedido, timeout=timeout) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except HTTPError as erro:
        try:
            detalhe = erro.read().decode("utf-8", errors="replace")[:300]
        except OSError:
            detalhe = "sem detalhes"
        raise RuntimeError(
            f"NVIDIA NIM rejeitou a solicitação (HTTP {erro.code}). Detalhes: {detalhe}"
        ) from erro


def gerar_texto_sync(mensagens: list[dict[str, str]]) -> str:
    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA_API_KEY não configurada")

    payload = {
        "model": NVIDIA_MODEL,
        "messages": mensagens,
        "temperature": 0.9,
        "top_p": 0.95,
        "max_tokens": 260,
        "stream": False,
    }
    url = f"{NVIDIA_API_BASE}/chat/completions"
    cabecalhos = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    resposta = None
    for tentativa in range(3):
        try:
            resposta = post_json(url, payload, cabecalhos)
            break
        except RuntimeError as erro:
            erro_texto = str(erro)
            temporario = "(HTTP 503)" in erro_texto or "(HTTP 429)" in erro_texto
            if not temporario or tentativa == 2:
                raise

            espera = 2 ** (tentativa + 1)
            print(
                f"[PoyoVoz] NVIDIA NIM indisponível ou limitado; "
                f"nova tentativa em {espera}s ({tentativa + 1}/2)."
            )
            time.sleep(espera)

    if resposta is None:
        raise RuntimeError("NVIDIA NIM não retornou uma resposta")

    try:
        texto = resposta["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError) as erro:
        raise RuntimeError(
            f"Resposta inesperada da NVIDIA NIM: {resposta!r}"
        ) from erro

    return texto[:MAX_REPLY]


def gerar_audio_sync(texto: str, destino: Path) -> None:
    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        raise RuntimeError("ELEVENLABS_API_KEY ou ELEVENLABS_VOICE_ID não configurada")
    payload = {
        "text": texto,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": 0.42,
            "similarity_boost": 0.84,
            "style": 0.28,
            "use_speaker_boost": True,
        },
    }
    corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    pedido = Request(
        "https://api.elevenlabs.io/v1/text-to-speech/"
        f"{ELEVENLABS_VOICE_ID}?output_format=mp3_44100_128",
        data=corpo,
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    try:
        with urlopen(pedido, timeout=60) as resposta:
            destino.write_bytes(resposta.read())
    except HTTPError as erro:
        try:
            detalhe = erro.read().decode("utf-8", errors="replace")[:300]
        except OSError:
            detalhe = "sem detalhes"
        raise RuntimeError(
            "ElevenLabs rejeitou a solicitação "
            f"(HTTP {erro.code}). Verifique ELEVENLABS_API_KEY e "
            f"ELEVENLABS_VOICE_ID. Detalhes: {detalhe}"
        ) from erro


def converter_para_voice_sync(
    entrada: Path,
    saida: Path,
) -> tuple[float, str]:
    """Converte o TTS para OGG/Opus e cria a waveform do Discord."""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(entrada),
                "-ac", "1", "-ar", "48000",
                "-c:a", "libopus", "-b:a", "32k",
                str(saida),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pcm = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(entrada),
                "-ac", "1", "-ar", "48000",
                "-f", "s16le", "pipe:1",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as erro:
        raise RuntimeError(
            "Não foi possível converter o áudio para Voice Message. "
            "Verifique se o FFmpeg está instalado."
        ) from erro

    amostras = array("h")
    amostras.frombytes(pcm)
    duracao = len(amostras) / 48000
    if not amostras:
        raise RuntimeError("O áudio gerado ficou vazio")

    pontos = min(256, max(1, len(amostras) // 480))
    tamanho_bloco = max(1, len(amostras) // pontos)
    waveform = bytearray()
    for inicio in range(0, len(amostras), tamanho_bloco):
        bloco = amostras[inicio:inicio + tamanho_bloco]
        pico = max(abs(valor) for valor in bloco)
        waveform.append(min(255, round(pico / 32767 * 255)))
        if len(waveform) == 256:
            break

    return duracao, base64.b64encode(bytes(waveform)).decode("ascii")


class PoyoVoz(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.locks: dict[int, asyncio.Lock] = {}
        print(
            "[PoyoVoz] Configuração: "
            f"NVIDIA NIM={'ok' if NVIDIA_API_KEY else 'ausente'} | "
            f"Modelo={NVIDIA_MODEL} | "
            f"ElevenLabs chave={'ok' if ELEVENLABS_API_KEY else 'ausente'} | "
            f"Voice ID={'ok' if ELEVENLABS_VOICE_ID else 'ausente'}"
        )

    def lock_for(self, guild_id: int) -> asyncio.Lock:
        return self.locks.setdefault(guild_id, asyncio.Lock())

    async def resposta_ia(self, message: discord.Message, pergunta: str) -> str:
        guild_id = message.guild.id if message.guild else 0
        user_id = message.author.id
        memoria = carregar_memoria(guild_id, user_id)
        mensagens = [{"role": "system", "content": PERSONALIDADE}]
        mensagens.extend(memoria)
        mensagens.append({"role": "user", "content": pergunta[:MAX_INPUT]})
        texto = await asyncio.to_thread(gerar_texto_sync, mensagens)
        salvar_memoria(guild_id, user_id, message.channel.id, "user", pergunta)
        salvar_memoria(guild_id, user_id, message.channel.id, "assistant", texto)
        return texto

    async def enviar_audio_no_chat(
        self,
        message: discord.Message,
        texto: str,
    ) -> None:
        """Gera e envia uma Voice Message nativa no canal de texto."""
        guild_id = message.guild.id
        async with self.lock_for(guild_id):
            caminho_mp3 = VOICE_DIR / f"{guild_id}-{message.id}.mp3"
            caminho_ogg = VOICE_DIR / f"{guild_id}-{message.id}.ogg"
            try:
                await asyncio.to_thread(gerar_audio_sync, texto, caminho_mp3)
                duracao, waveform = await asyncio.to_thread(
                    converter_para_voice_sync,
                    caminho_mp3,
                    caminho_ogg,
                )

                arquivo = DiscordHTTPFile(
                    str(caminho_ogg),
                    filename="poyo-resposta.ogg",
                )
                flags = MessageFlags()
                flags.voice = True

                # discord.py 2.7 já conhece a flag de Voice Message, mas ainda
                # não expõe um método público para enviá-la. Reutilizamos o
                # multipart oficial e completamos os metadados exigidos pela API.
                with handle_message_parameters(
                    files=[arquivo],
                    flags=flags,
                ) as params:
                    payload_json = next(
                        parte["value"]
                        for parte in params.multipart or []
                        if parte.get("name") == "payload_json"
                    )
                    payload = json.loads(payload_json)
                    payload["attachments"][0].update(
                        {
                            "duration_secs": round(duracao, 3),
                            "waveform": waveform,
                        }
                    )
                    for parte in params.multipart or []:
                        if parte.get("name") == "payload_json":
                            parte["value"] = json.dumps(
                                payload,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                        elif parte.get("name") == "files[0]":
                            parte["content_type"] = "audio/ogg"

                    await self.bot.http.send_message(
                        message.channel.id,
                        params=params,
                    )
            finally:
                try:
                    caminho_mp3.unlink(missing_ok=True)
                    caminho_ogg.unlink(missing_ok=True)
                except OSError:
                    pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if self.bot.user is None:
            return

        mencionado = self.bot.user in message.mentions
        saudacao = bool(TRIGGER_RE.search(message.content))
        respondeu_poyo = False
        if message.reference and message.reference.message_id:
            referida = message.reference.resolved
            if referida is None:
                try:
                    referida = await message.channel.fetch_message(message.reference.message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    referida = None
            respondeu_poyo = isinstance(referida, discord.Message) and referida.author.id == self.bot.user.id

        if not (mencionado or saudacao or respondeu_poyo):
            return
        if message.content.startswith((",", "/")) and not mencionado:
            return

        pergunta = re.sub(rf"<@!?{self.bot.user.id}>", "", message.content).strip()
        pergunta = TRIGGER_RE.sub("", pergunta).strip(" ,!?\n")
        if not pergunta:
            pergunta = "Alguém chamou por mim. Responda de um jeito animado e pergunte como a pessoa está."

        try:
            texto = await asyncio.wait_for(
                self.resposta_ia(message, pergunta), timeout=45
            )
            await asyncio.wait_for(
                self.enviar_audio_no_chat(message, texto), timeout=100
            )
        except (RuntimeError, HTTPError, URLError, TimeoutError, asyncio.TimeoutError, KeyError, ValueError) as erro:
            # A Poyo é uma bot de voz: falhas ficam no console e não viram
            # uma resposta textual no canal.
            print(f"[PoyoVoz] Falha ao responder em áudio: {erro}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PoyoVoz(bot))
