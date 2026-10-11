from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Chaves e IDs ficam somente no .env.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

DATABASE_PATH = Path(__file__).resolve().parent.parent / "poyovoz.sqlite3"
VOICE_DIR = Path(tempfile.gettempdir()) / "poyo-voz"
VOICE_DIR.mkdir(parents=True, exist_ok=True)

MAX_INPUT = 1800
MAX_REPLY = 1300
MEMORY_MESSAGES = 24
TRIGGER_RE = re.compile(r"\b(?:oi|olá|ola|eai|e aí|ei)\s+poyo\b", re.IGNORECASE)

PERSONALIDADE = """
Você é Poyo, a mascote extrovertida e brincalhona de um servidor do Discord.
Você ama Minecraft, fala bastante, faz piadas leves e zoeiras carinhosas,
mas nunca humilha, ameaça, discrimina ou incentiva brigas. Trate bem quem
trata você bem. Fale em português brasileiro natural, como uma pessoa real,
sem dizer que é uma IA, sem mencionar prompts, APIs ou memória interna.
Se perguntarem sobre Minecraft, demonstre entusiasmo e dê respostas úteis.
Responda de forma curta o suficiente para ser falada em áudio, normalmente
entre 1 e 4 frases. Não use markdown complicado, links longos ou emojis em
excesso. Nunca invente que executou uma ação no servidor.
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
            f"Gemini rejeitou a solicitação (HTTP {erro.code}). Detalhes: {detalhe}"
        ) from erro


def gerar_texto_sync(mensagens: list[dict[str, str]]) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY não configurada")

    sistema = ""
    conteudos: list[dict] = []
    for mensagem in mensagens:
        papel = mensagem["role"]
        texto = mensagem["content"]
        if papel == "system":
            sistema = texto
            continue
        conteudos.append(
            {
                "role": "model" if papel == "assistant" else "user",
                "parts": [{"text": texto}],
            }
        )

    payload = {
        "systemInstruction": {"parts": [{"text": sistema}]},
        "contents": conteudos,
        "generationConfig": {
            "temperature": 0.9,
            "topP": 0.95,
            "maxOutputTokens": 260,
        },
    }
    resposta = post_json(
        f"{GEMINI_API_BASE}/models/{GEMINI_MODEL}:generateContent",
        payload,
        {
            "x-goog-api-key": GEMINI_API_KEY,
            "Content-Type": "application/json",
        },
    )
    texto = resposta["candidates"][0]["content"]["parts"][0]["text"].strip()
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


class PoyoVoz(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.locks: dict[int, asyncio.Lock] = {}
        print(
            "[PoyoVoz] Configuração: "
            f"Gemini={'ok' if GEMINI_API_KEY else 'ausente'} | "
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
        """Gera um MP3 e envia o áudio como anexo no canal de texto."""
        guild_id = message.guild.id
        async with self.lock_for(guild_id):
            caminho = VOICE_DIR / f"{guild_id}-{message.id}.mp3"
            try:
                await asyncio.to_thread(gerar_audio_sync, texto, caminho)
                await message.reply(
                    file=discord.File(str(caminho), filename="poyo-resposta.mp3"),
                    mention_author=False,
                )
            finally:
                try:
                    caminho.unlink(missing_ok=True)
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
