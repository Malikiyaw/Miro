import discord
from typing import Optional


def truncate(text: str, limit: int = 1000) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_bool(value: bool) -> str:
    return "🟢 Enabled" if value else "🔴 Disabled"


def build_status_embed(title: str, guild: Optional[discord.Guild],
                       status: bool = None, color: int = None,
                       fields: list = None, footer: str = None) -> discord.Embed:
    """Consistent panel/status card used by every Miro system panel."""
    if color is None:
        color = 0x57F287 if status else (0xED4245 if status is False else 0x5865F2)
    embed = discord.Embed(
        title=title,
        description=f"Server: **{guild.name}**" if guild else None,
        color=color,
    )
    for f in fields or []:
        embed.add_field(name=f[0], value=truncate(str(f[1]), 1024), inline=f[2] if len(f) > 2 else True)
    if footer:
        embed.set_footer(text=footer)
    return embed


class ConfirmView(discord.ui.View):
    """Danger-zone confirmation. on_confirm receives the interaction."""

    def __init__(self, author_id: int, title: str, danger: bool = True, timeout: float = 30):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.confirmed = False
        self.title = title
        confirm_style = discord.ButtonStyle.danger if danger else discord.ButtonStyle.success
        self.confirm_button.style = confirm_style

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the person who opened this prompt can respond.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        for child in self.children:
            child.disabled = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❎ Cancelled.", view=self)
        self.stop()


class ToggleButton(discord.ui.Button):
    """Green/red enable-disable button that renders its own state."""

    def __init__(self, label: str, enabled: bool, custom_id: str, row: int = None):
        super().__init__(
            label=label,
            emoji="✅" if enabled else "⛔",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.danger,
            custom_id=custom_id,
            row=row,
        )


class SaveButton(discord.ui.Button):
    def __init__(self, custom_id: str, row: int = None):
        super().__init__(label="Save", emoji="💾", style=discord.ButtonStyle.primary, custom_id=custom_id, row=row)


class ResetButton(discord.ui.Button):
    def __init__(self, custom_id: str, row: int = 4):
        super().__init__(label="Reset config", emoji="♻️", style=discord.ButtonStyle.danger, custom_id=custom_id, row=row)


class TestButton(discord.ui.Button):
    def __init__(self, custom_id: str, row: int = 3):
        super().__init__(label="Test", emoji="🧪", style=discord.ButtonStyle.secondary, custom_id=custom_id, row=row)


class BackButton(discord.ui.Button):
    def __init__(self, custom_id: str, row: int = 4):
        super().__init__(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, custom_id=custom_id, row=row)


def channel_options(guild: discord.Guild, limit: int = 20) -> list:
    return [discord.SelectOption(label=c.name[:90], value=str(c.id))
            for c in list(guild.text_channels)[:limit]]


def role_options(guild: discord.Guild, limit: int = 20) -> list:
    roles = [r for r in guild.roles if not r.managed and r != guild.default_role]
    return [discord.SelectOption(label=r.name[:90], value=str(r.id)) for r in roles[:limit]]
