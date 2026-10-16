from __future__ import annotations
import asyncio, io, json, sqlite3
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import discord
from discord.ext import commands

try:
    from ._media import baixar_arquivo
except ModuleNotFoundError:
    async def baixar_arquivo(url: str, nome: str) -> discord.File:
        def baixar() -> bytes:
            pedido = Request(url, headers={"User-Agent": "Poyo-Discord-Bot/1.0"})
            with urlopen(pedido, timeout=20) as resposta:
                return resposta.read()
        return discord.File(io.BytesIO(await asyncio.to_thread(baixar)), filename=nome)

ROBLOX_LOGO_ID = 1509476102890979409
MINECRAFT_LOGO_ID = 1509476073904148582
DATABASE_PATH = Path(__file__).resolve().parent.parent / "avatar.sqlite3"


async def requisicao_json(metodo: str, url: str, dados: Optional[dict] = None) -> dict:
    corpo = json.dumps(dados).encode() if dados is not None else None
    headers = {"User-Agent": "Poyo-Discord-Bot/1.0"}
    if dados is not None:
        headers["Content-Type"] = "application/json"

    def solicitar():
        with urlopen(Request(url, data=corpo, headers=headers, method=metodo), timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    return await asyncio.to_thread(solicitar)


async def buscar_roblox(nome: str) -> Optional[dict]:
    try:
        data = await requisicao_json("POST", "https://users.roblox.com/v1/usernames/users",
                                     {"usernames": [nome], "excludeBannedUsers": False})
        if not data.get("data"):
            return None
        u = data["data"][0]
        uid = int(u["id"])
        thumb = await requisicao_json(
            "GET",
            f"https://thumbnails.roblox.com/v1/users/avatar?userIds={uid}&size=720x720&format=Png&isCircular=false"
        )
        if not thumb.get("data") or not thumb["data"][0].get("imageUrl"):
            return None
        real = str(u.get("name", nome))
        return {
            "plataforma": "Roblox",
            "nome": real,
            "exibicao": str(u.get("displayName", real)),
            "imagem": str(thumb["data"][0]["imageUrl"]),
            "perfil": f"https://www.roblox.com/users/{uid}/profile",
            "logo_id": ROBLOX_LOGO_ID,
        }
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


async def buscar_minecraft(nome: str) -> Optional[dict]:
    try:
        perfil = await requisicao_json("GET", f"https://api.mojang.com/users/profiles/minecraft/{quote(nome)}")
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    uuid, real = perfil.get("id"), perfil.get("name")
    if not uuid or not real:
        return None
    real = str(real)
    return {
        "plataforma": "Minecraft",
        "nome": real,
        "exibicao": real,
        "imagem": f"https://crafatar.com/renders/body/{uuid}?overlay&scale=10",
        "imagem_fallback": f"https://mc-heads.net/body/{uuid}/832",
        "perfil": f"https://namemc.com/profile/{quote(real)}",
        "logo_id": MINECRAFT_LOGO_ID,
    }


async def buscar_skin(entrada: str) -> Optional[dict]:
    valor = entrada.strip().lstrip("@")
    low = valor.lower()
    if low.startswith(("minecraft:", "mine:", "mc:")):
        return await buscar_minecraft(valor.split(":", 1)[1].strip())
    if low.startswith(("roblox:", "rbx:")):
        return await buscar_roblox(valor.split(":", 1)[1].strip())
    result = await buscar_roblox(valor)
    return result if result is not None else await buscar_minecraft(valor)


def texto_curtidas(total: int) -> str:
    return f"{total} {'Curtida' if total == 1 else 'Curtidas'}"


def preparar_banco() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS skinview_posts(
            message_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, guild_id INTEGER NOT NULL,
            skin_json TEXT NOT NULL, imagem_url TEXT NOT NULL, curtidas_json TEXT NOT NULL DEFAULT '[]')""")


def carregar_posts():
    with sqlite3.connect(DATABASE_PATH) as db:
        return db.execute("SELECT message_id, skin_json, imagem_url, curtidas_json FROM skinview_posts").fetchall()


def salvar_post(message_id, channel_id, guild_id, skin, imagem_url, curtidas):
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute("""INSERT INTO skinview_posts VALUES(?,?,?,?,?,?)
        ON CONFLICT(message_id) DO UPDATE SET skin_json=excluded.skin_json,
        imagem_url=excluded.imagem_url, curtidas_json=excluded.curtidas_json""",
                   (message_id, channel_id, guild_id, json.dumps(skin), imagem_url, json.dumps(sorted(curtidas))))


def salvar_curtidas_bg(message_id, curtidas):
    try:
        with sqlite3.connect(DATABASE_PATH) as db:
            db.execute("UPDATE skinview_posts SET curtidas_json=? WHERE message_id=?",
                       (json.dumps(sorted(curtidas)), message_id))
    except Exception:
        pass


class SkinView(discord.ui.LayoutView):
    def __init__(self, skin: dict, imagem_url: str, curtidas=None):
        super().__init__(timeout=None)
        self.skin = skin
        self.imagem_url = imagem_url
        self.curtidas = set(curtidas or [])
        self.lock = asyncio.Lock()
        self.montar()

    def montar(self):
        self.clear_items()
        logo = discord.PartialEmoji(
            name="RobloxLogo" if self.skin["plataforma"] == "Roblox" else "minecraftlogopng2",
            id=int(self.skin["logo_id"]),
        )
        perfil = discord.ui.Button(emoji=logo, style=discord.ButtonStyle.link, url=self.skin["perfil"])
        like = discord.ui.Button(
            emoji="🤍",
            style=discord.ButtonStyle.secondary,
            custom_id=f"skinview:curtir:{self.skin['plataforma']}:{self.skin['nome']}",
        )
        like.callback = self.curtir
        self.add_item(discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    f"## [{self.skin['exibicao']}]({self.skin['perfil']})\n"
                    f"`@{self.skin['nome']}` - {texto_curtidas(len(self.curtidas))}"
                ),
                accessory=perfil,
            ),
            discord.ui.Separator(),
            discord.ui.MediaGallery(discord.MediaGalleryItem(self.imagem_url)),
            discord.ui.Separator(),
            discord.ui.ActionRow(like),
        ))

    async def curtir(self, interaction: discord.Interaction):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except discord.HTTPException:
            pass

        if interaction.message is None:
            try:
                await interaction.followup.send("Não consegui localizar esta publicação.", ephemeral=True)
            except discord.HTTPException:
                pass
            return

        async with self.lock:
            if interaction.user.id in self.curtidas:
                try:
                    await interaction.followup.send("Você já curtiu esta skin.", ephemeral=True)
                except discord.HTTPException:
                    pass
                return

            self.curtidas.add(interaction.user.id)
            self.montar()

            try:
                await interaction.message.edit(view=self)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                self.curtidas.discard(interaction.user.id)
                try:
                    await interaction.followup.send("Não consegui atualizar a curtida. Tente novamente.", ephemeral=True)
                except discord.HTTPException:
                    pass
                return

        if interaction.message is not None:
            interaction.client.loop.create_task(
                asyncio.to_thread(salvar_curtidas_bg, interaction.message.id, self.curtidas)
            )


class Avatar(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        preparar_banco()
        for mid, skin_json, imagem_url, likes_json in carregar_posts():
            try:
                self.bot.add_view(
                    SkinView(json.loads(skin_json), imagem_url, set(json.loads(likes_json))),
                    message_id=int(mid),
                )
            except (ValueError, TypeError, KeyError, discord.HTTPException):
                continue

    async def _enviar_skin(self, ctx: commands.Context, nome: str, buscar):
        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer()

        async def avisar(texto):
            if ctx.interaction is not None:
                await ctx.send(texto, ephemeral=True)
            else:
                await ctx.send(texto, delete_after=10)

        if ctx.message is not None:
            try:
                await ctx.message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        try:
            resultado = await asyncio.wait_for(buscar(nome.strip().lstrip("@")), timeout=25)
        except asyncio.TimeoutError:
            await avisar("A consulta demorou demais. Tente novamente.")
            return

        if resultado is None:
            await avisar("Não encontrei esse usuário nessa plataforma.")
            return

        arquivo = None
        imagem_url = resultado["imagem"]
        for url in (resultado["imagem"], resultado.get("imagem_fallback")):
            if not url:
                continue
            try:
                arquivo = await asyncio.wait_for(baixar_arquivo(url, "poyo-avatar.png"), timeout=30)
                imagem_url = "attachment://poyo-avatar.png"
                break
            except (OSError, TimeoutError, asyncio.TimeoutError, discord.HTTPException):
                continue

        if arquivo is None:
            await avisar("Não consegui baixar a imagem desse avatar agora. Tente novamente.")
            return

        view = SkinView(resultado, imagem_url)
        try:
            msg = await ctx.send(view=view, file=arquivo)
        except (discord.Forbidden, discord.HTTPException):
            await avisar("Não consegui enviar o cartão da skin. Verifique as permissões do bot.")
            return

        if msg.guild is not None:
            salvar_post(msg.id, msg.channel.id, msg.guild.id, resultado, imagem_url, view.curtidas)

    @commands.hybrid_command(
        name="avatar_roblox",
        description="Mostra o avatar de um usuário do Roblox.",
        extras={"categoria": "Utilidades", "uso": ",avatar_roblox nome",
                "descricao": "Mostra o avatar de um usuário do Roblox."},
    )
    @commands.guild_only()
    @commands.bot_has_permissions(send_messages=True, read_message_history=True)
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def avatar_roblox(self, ctx: commands.Context, *, nome: str):
        await self._enviar_skin(ctx, nome, buscar_roblox)

    @commands.hybrid_command(
        name="avatar_minecraft",
        description="Mostra a skin de um usuário do Minecraft.",
        extras={"categoria": "Utilidades", "uso": ",avatar_minecraft nome",
                "descricao": "Mostra a skin de um usuário do Minecraft."},
    )
    @commands.guild_only()
    @commands.bot_has_permissions(send_messages=True, read_message_history=True)
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def avatar_minecraft(self, ctx: commands.Context, *, nome: str):
        await self._enviar_skin(ctx, nome, buscar_minecraft)


async def setup(bot: commands.Bot):
    await bot.add_cog(Avatar(bot))
