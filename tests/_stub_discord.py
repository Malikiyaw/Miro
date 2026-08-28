"""Minimal discord stub enabling offline import of Miro modules in tests."""
import sys
import types as _t


class _Auto:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _inst
    def __getattr__(self, n): return _fn
    def __iter__(self): return iter([])
    def __bool__(self): return False


_inst = _Auto()
def _fn(*a, **k): return _inst


class _DynModule(_t.ModuleType):
    def __getattr__(self, name):
        obj = _Auto(); setattr(self, name, obj); return obj


class Embed:
    def __init__(self, *a, **k): pass
    def add_field(self, *a, **k): pass
    def set_footer(self, t=None, i=None): pass
    def set_author(self, *a, **k): pass


class HTTPException(Exception):
    pass


ds = _DynModule("discord")
ds.ButtonStyle = _Auto()
ds.Color = _Auto()
ds.Interaction = _Auto()
ds.Guild = _Auto()
ds.Member = _Auto()
ds.Role = _Auto()
ds.Permissions = _Auto()
ds.Embed = Embed
ds.HTTPException = HTTPException
ds.SelectOption = _Auto()
ds.utils = _Auto()
ds.Object = _Auto()

ui = _DynModule("discord.ui")


class View:
    def __init__(self, *a, **k): self.timeout = k.get("timeout", 180)
    def add_item(self, i): pass
    async def wait(self): return True
    def stop(self): pass


def _d(**k):
    def d(f): return f
    return d


ui.button = _d
ui.View = View
for _n in ("Button", "Select", "RoleSelect", "ChannelSelect", "SelectOption", "TextInput"):
    setattr(ui, _n, type(_n, (object,), {
        "__init__": lambda s, *a, **k: None,
        "add_item": lambda s, i: None,
    }))


class Modal:
    def __init_subclass__(cls, **k): pass
    def __init__(self, *a, **k): pass
    def add_item(self, i): pass


ui.Modal = Modal
ds.ui = ui

appcmds = _DynModule("discord.app_commands")
ds.app_commands = appcmds

# discord.ext support (merged module files may import commands at top level)
ext = _DynModule("discord.ext")
extcmds = _DynModule("discord.ext.commands")


class Cog:
    @staticmethod
    def listener(*a, **k):
        def d(f):
            return f
        return d


extcmds.Bot = _Auto
extcmds.Cog = Cog
extcmds.command = _d
extcmds.group = _d
extcmds.has_permissions = _d
ext.commands = extcmds

sys.modules["discord"] = ds
sys.modules["discord.ui"] = ui
sys.modules["discord.app_commands"] = appcmds
sys.modules["discord.ext"] = ext
sys.modules["discord.ext.commands"] = extcmds
