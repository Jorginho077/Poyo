"""
Diagnóstico do sistema de tickets (discord.py 2.x).

Instalar no bot:
    await bot.load_extension("diagnostico")

Usar:
    - slash command /diagnostico (só ADM), ou
    - no painel de comandos-adm: painel.add_item(DiagButton())

Ao enviar, abre um modal (canal do ticket + membro com problema, ambos opcionais)
e devolve um diagnostico.txt só pra você (ephemeral).
O arquivo NÃO inclui token nem senhas.

Opcional: para incluir a config salva do seu sistema de tickets no relatório:
    bot.diag_extra = lambda guild: json.dumps(config, indent=2, ensure_ascii=False, default=str)
"""
import collections
import datetime
import io
import logging
import platform

import discord
from discord import app_commands
from discord.ext import commands

LOG_BUFFER = collections.deque(maxlen=400)

KEY_PERMS = [
    "administrator",
    "view_channel",
    "send_messages",
    "send_messages_in_threads",
    "read_message_history",
    "embed_links",
    "attach_files",
    "manage_channels",
    "manage_roles",
    "manage_messages",
    "manage_threads",
    "use_application_commands",
]


class _BufHandler(logging.Handler):
    def emit(self, record):
        try:
            LOG_BUFFER.append(self.format(record))
        except Exception:
            pass


def _names(perms):
    return [n for n in KEY_PERMS if getattr(perms, n, False)]


def _fmt_overwrites(channel):
    out = []
    for target, ow in channel.overwrites.items():
        allow, deny = ow.pair()
        kind = "cargo" if isinstance(target, discord.Role) else "membro"
        nome = getattr(target, "name", target)
        out.append(
            f"      {kind} {nome} ({target.id}) | permite: {_names(allow) or '-'} | nega: {_names(deny) or '-'}"
        )
    return out or ["      (sem overwrites)"]


def _id(valor):
    v = (valor or "").strip().strip("<#@!>")
    return int(v) if v.isdigit() else None


def build_report(bot, guild, canal=None, membro=None):
    L = []
    add = L.append
    me = guild.me

    add(f"== DIAGNOSTICO {datetime.datetime.now(datetime.timezone.utc).isoformat()} ==")
    add(f"discord.py {discord.__version__} | python {platform.python_version()}")
    add(f"servidor: {guild.name} ({guild.id}) | membros: {guild.member_count}")
    add(
        f"bot: {me} | maior cargo: {me.top_role.name} (pos {me.top_role.position}) "
        f"| admin: {me.guild_permissions.administrator}"
    )
    add(f"perms do bot: {_names(me.guild_permissions)}")
    add(
        f"intents: guilds={bot.intents.guilds} members={bot.intents.members} "
        f"message_content={bot.intents.message_content}"
    )
    add("")

    # ---- checagem do canal / membro (o bug do "somente leitura") ----
    if canal is not None:
        base = canal.parent if isinstance(canal, discord.Thread) else canal
        add("== CHECAGEM DO CANAL ==")
        add(f"canal: {canal.name} ({canal.id}) tipo={canal.type.name}")
        if isinstance(canal, discord.Thread):
            add(f"  thread: archived={canal.archived} locked={canal.locked} dono={canal.owner_id}")
        cat = getattr(base, "category", None) if base is not None else None
        add(f"  categoria do Discord: {cat.name if cat else None} ({cat.id if cat else '-'})")
        if base is not None:
            add(f"  sincronizado com a categoria: {base.permissions_synced}")
            add("  overwrites do canal:")
            L.extend(_fmt_overwrites(base))
            if cat:
                add("  overwrites da categoria:")
                L.extend(_fmt_overwrites(cat))
        add(f"  perms do bot neste canal: {_names(canal.permissions_for(me))}")
        if membro is not None:
            p = canal.permissions_for(membro)
            add(f"  membro: {membro} ({membro.id}) cargos={[r.name for r in membro.roles]}")
            add(
                f"  EFETIVO -> admin={p.administrator} ver={p.view_channel} enviar={p.send_messages} "
                f"enviar_em_threads={p.send_messages_in_threads} historico={p.read_message_history}"
            )
            add(f"  timeout ativo: {membro.is_timed_out()}")
        add("")

    # ---- cargos ----
    add("== CARGOS (do mais alto ao mais baixo) ==")
    for r in sorted(guild.roles, key=lambda r: -r.position):
        flag = ""
        if r != guild.default_role and r.position >= me.top_role.position:
            flag = " [ACIMA/IGUAL AO BOT: bot nao consegue gerenciar]"
        add(f"{r.position:>3} {r.name} ({r.id}) managed={r.managed} perms={_names(r.permissions)}{flag}")
    add("")

    # ---- categorias e canais ----
    add("== CATEGORIAS E CANAIS ==")
    for cat in guild.categories:
        add(f"[{cat.name}] ({cat.id})")
        L.extend(_fmt_overwrites(cat))
        for ch in cat.channels:
            add(f"  - {ch.name} ({ch.id}) {ch.type.name} sync={ch.permissions_synced}")
            if not ch.permissions_synced:
                L.extend(_fmt_overwrites(ch))
    add("")

    # ---- config do sistema de tickets (opcional) ----
    extra = getattr(bot, "diag_extra", None)
    if callable(extra):
        add("== CONFIG DO SISTEMA ==")
        try:
            add(str(extra(guild)))
        except Exception as e:  # noqa: BLE001
            add(f"erro ao ler config: {e!r}")
        add("")

    # ---- logs recentes ----
    add("== ULTIMOS LOGS / ERROS ==")
    L.extend(LOG_BUFFER or ["(vazio)"])
    return "\n".join(L)


class DiagModal(discord.ui.Modal, title="Diagnóstico"):
    canal = discord.ui.TextInput(label="ID do canal (vazio = este canal)", required=False)
    membro = discord.ui.TextInput(label="ID de quem não consegue falar (opcional)", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            g = interaction.guild
            cid, mid = _id(self.canal.value), _id(self.membro.value)
            canal = (g.get_channel_or_thread(cid) if cid else None) or interaction.channel
            membro = None
            if mid:
                membro = g.get_member(mid)
                if membro is None:
                    try:
                        membro = await g.fetch_member(mid)
                    except discord.HTTPException:
                        membro = None
            texto = build_report(interaction.client, g, canal, membro)
            arq = discord.File(io.BytesIO(texto.encode("utf-8")), filename="diagnostico.txt")
            await interaction.followup.send("Pronto. Manda esse arquivo.", file=arq, ephemeral=True)
        except Exception as e:  # noqa: BLE001
            await interaction.followup.send(f"Falhou ao gerar: `{e!r}`", ephemeral=True)


class DiagButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Diagnóstico",
            emoji="🩺",
            style=discord.ButtonStyle.secondary,
            custom_id="diag:run",
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Só ADM.", ephemeral=True)
        await interaction.response.send_modal(DiagModal())


class DiagView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(DiagButton())


class Diag(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="diagnostico", description="Gera um .txt com o estado do sistema de tickets (só ADM)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def diagnostico(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Só ADM.", ephemeral=True)
        await interaction.response.send_modal(DiagModal())


async def setup(bot):
    root = logging.getLogger()
    if not any(isinstance(h, _BufHandler) for h in root.handlers):
        h = _BufHandler()
        h.setLevel(logging.INFO)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
    bot.add_view(DiagView())
    await bot.add_cog(Diag(bot))
