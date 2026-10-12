# fila e reproducao de audio por servidor usando ffmpeg
import asyncio
import logging
from collections import deque

import discord

from ._musica_fontes import Faixa

log = logging.getLogger("bot.musica.player")

FFMPEG_OPCOES = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


class PlayerGuild:
    """Um player por servidor: fila, volume e loop de reproducao."""

    def __init__(self, cog, guild_id: int) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.fila: deque[Faixa] = deque()
        self.atual: Faixa | None = None
        self.volume: float = 0.8
        self._evento_proxima = asyncio.Event()
        self._tarefa: asyncio.Task | None = None
        self._trava = asyncio.Lock()

    # ------------------------------------------------------------ ciclo

    def garantir_loop(self) -> None:
        if self._tarefa is None or self._tarefa.done():
            self._evento_proxima.clear()
            self._tarefa = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        bot = self.cog.bot
        while True:
            self._evento_proxima.clear()
            if not self.fila:
                self.atual = None
                cfg = self.cog.config.obter(self.guild_id)
                if not cfg.get("modo_247"):
                    await self._desconectar()
                    return
                # 24/7: espera ate algo entrar na fila
                try:
                    await asyncio.wait_for(self._aguardar_fila(), timeout=None)
                except asyncio.CancelledError:
                    return
                continue

            self.atual = self.fila.popleft()
            guild = bot.get_guild(self.guild_id)
            voz = guild.voice_client if guild else None
            if voz is None or not voz.is_connected():
                self.atual = None
                return

            try:
                fonte = discord.FFmpegPCMAudio(self.atual.url_stream, **FFMPEG_OPCOES)
                fonte = discord.PCMVolumeTransformer(fonte, volume=self.volume)
            except Exception as e:  # ffmpeg falhou ao abrir o stream
                log.warning("ffmpeg falhou em %r: %s", self.atual.titulo, e)
                await self.cog.aviso_falha(self.guild_id, self.atual)
                continue

            voz.play(
                fonte,
                after=lambda e: bot.loop.call_soon_threadsafe(self._evento_proxima.set),
            )
            await self.cog.aviso_tocando(self.guild_id, self.atual)
            await self._evento_proxima.wait()

    async def _aguardar_fila(self) -> None:
        while not self.fila:
            await asyncio.sleep(1)

    async def _desconectar(self) -> None:
        guild = self.cog.bot.get_guild(self.guild_id)
        if guild and guild.voice_client:
            try:
                await guild.voice_client.disconnect()
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------ acoes

    async def adicionar(self, faixas: list[Faixa]) -> None:
        async with self._trava:
            self.fila.extend(faixas)
        self.garantir_loop()

    def pular(self) -> None:
        guild = self.cog.bot.get_guild(self.guild_id)
        if guild and guild.voice_client and guild.voice_client.is_playing():
            guild.voice_client.stop()  # dispara o after -> proxima

    async def parar(self) -> None:
        self.fila.clear()
        self.atual = None
        guild = self.cog.bot.get_guild(self.guild_id)
        if guild and guild.voice_client:
            if guild.voice_client.is_playing():
                guild.voice_client.stop()
            await self._desconectar()
        if self._tarefa and not self._tarefa.done():
            self._tarefa.cancel()

    def pausar(self) -> bool:
        guild = self.cog.bot.get_guild(self.guild_id)
        if guild and guild.voice_client and guild.voice_client.is_playing():
            guild.voice_client.pause()
            return True
        return False

    def retomar(self) -> bool:
        guild = self.cog.bot.get_guild(self.guild_id)
        if guild and guild.voice_client and guild.voice_client.is_paused():
            guild.voice_client.resume()
            return True
        return False

    def definir_volume(self, valor: int) -> None:
        self.volume = max(0, min(100, valor)) / 100
        guild = self.cog.bot.get_guild(self.guild_id)
        if guild and guild.voice_client and isinstance(
            guild.voice_client.source, discord.PCMVolumeTransformer
        ):
            guild.voice_client.source.volume = self.volume
