"""
SystemInstaller — single backend for /autosetup, /configpanel, and AI Agent.

Wraps AutoSetupSystem bespoke installers + INSTALL_TABLE + ResourceManager.
"""
import discord
from data_manager import dm
from logger import logger
from core.resource_manager import ResourceManager
from core.capabilities import get_capability


class SystemInstaller:
    def __init__(self, bot):
        self.bot = bot
        self.rm = ResourceManager(bot)

    async def install(self, guild: discord.Guild, key: str, user=None) -> dict:
        cap = get_capability(key)
        if not cap:
            return {"ok": False, "error": f"Unknown system {key}"}
        # delegate to AutoSetupSystem bespoke or generic table
        autosetup = self.bot.auto_setup
        method = f"setup_{key}_system"
        if hasattr(autosetup, method):
            ok = await getattr(autosetup, method)(guild, user)
            return {"ok": bool(ok)}
        # fallback: generic installer + mark enabled
        try:
            ok = await autosetup.setup_generic_system(guild, key, user)
            return {"ok": bool(ok)}
        except Exception as e:
            logger.error(f"Installer install {key} failed: {e}")
            return {"ok": False, "error": str(e)}

    async def repair(self, guild: discord.Guild, key: str) -> dict:
        """Idempotent repair: recreate missing channel/role/panel without duplicating healthy resources."""
        cap = get_capability(key)
        if not cap:
            return {"ok": False, "error": "unknown"}
        # currently only verification is fully V10; others fallback to install if nothing exists
        cfg = dm.get_guild_data(guild.id, cap.config_key, {}) or {}
        report = {"created": [], "reused": [], "failed": []}
        # channel/role sinks for verification
        if key == "verification":
            # verify_channel
            ch_id = cfg.get("verify_channel")
            ch = self.rm.resolve_channel(guild, ch_id)
            if not ch:
                new_ch = await self.rm.create_channel(guild, "verify")
                if new_ch:
                    cfg["verify_channel"] = str(new_ch.id)
                    report["created"].append("verify_channel")
                else:
                    report["failed"].append("verify_channel")
            else:
                report["reused"].append("verify_channel")
            for rk, name in [("verified_role","Verified"), ("unverified_role","Unverified")]:
                rid = cfg.get(rk)
                r = self.rm.resolve_role(guild, rid)
                if not r:
                    new_r = await self.rm.create_role(guild, name, color=discord.Color.green() if name=="Verified" else discord.Color.red())
                    if new_r:
                        cfg[rk] = str(new_r.id)
                        report["created"].append(rk)
                    else:
                        report["failed"].append(rk)
                else:
                    report["reused"].append(rk)
            # post/repost panel
            try:
                ch2 = self.rm.resolve_channel(guild, cfg.get("verify_channel"))
                if ch2:
                    from modules.member_management import VerificationView
                    embed = discord.Embed(title="🔐 Verification Required",
                                          description="Click **Verify Me** to verify.", color=discord.Color.blue())
                    view = VerificationView(self.bot.verification, guild.id)
                    # use _post_panel helper if available else direct send
                    try:
                        await self.bot.auto_setup._post_panel(guild, "verification", ch2,
                            "🔐 Verification Required",
                            "Click the **Verify Me** button below to verify yourself and gain full access to the server.",
                            discord.Color.blue(), view)
                        report["created"].append("panel")
                    except Exception:
                        await ch2.send(embed=embed, view=view)
                        report["created"].append("panel")
                dm.update_guild_data(guild.id, cap.config_key, cfg)
            except Exception as e:
                logger.warning(f"verification repair panel failed: {e}")
            # ensure enabled
            if not cfg.get("enabled"):
                cfg["enabled"] = True
                dm.update_guild_data(guild.id, cap.config_key, cfg)
            ok = len(report["failed"]) == 0
            return {"ok": ok, "report": report}
        # Generic fallback: ensure enabled + try to heal missing channel/role from SYSTEM_GROUPS settings
        try:
            from modules.system_panels import SYSTEM_GROUPS
            for g in SYSTEM_GROUPS.values():
                for s in g["subsystems"]:
                    if s["key"] == key:
                        for setting in s.get("settings", []):
                            if setting.get("type") == "channel":
                                ck = setting.get("key")
                                ch = self.rm.resolve_channel(guild, cfg.get(ck))
                                if not ch and setting.get("key"):
                                    # try to create named channel heuristically
                                    new_ch = await self.rm.create_channel(guild, ck.replace("_","-"))
                                    if new_ch:
                                        cfg[ck] = str(new_ch.id)
                                        report["created"].append(ck)
                            if setting.get("type") == "role":
                                rk = setting.get("key")
                                r = self.rm.resolve_role(guild, cfg.get(rk))
                                if not r and rk:
                                    new_r = await self.rm.create_role(guild, rk.replace("_"," ").title())
                                    if new_r:
                                        cfg[rk] = str(new_r.id)
                                        report["created"].append(rk)
                        break
        except Exception:
            pass
        if not cfg.get("enabled"):
            cfg["enabled"] = True
            report["created"].append("enabled")
        dm.update_guild_data(guild.id, cap.config_key, cfg)
        ok = len(report["failed"]) == 0
        return {"ok": ok, "report": report}

    def preflight(self, guild: discord.Guild, key: str) -> dict:
        """Dry-run: what would install/repair do, without mutating."""
        cap = get_capability(key)
        if not cap:
            return {"ok": False, "error": "unknown"}
        cfg = dm.get_guild_data(guild.id, cap.config_key, {}) or {}
        missing = []
        for res in cap.resources:
            if res.kind == "channel":
                # heuristic: any channel-type setting key matching resource
                ch = self.rm.resolve_channel(guild, cfg.get(res.key))
                if not ch and res.required:
                    missing.append(res.key)
            elif res.kind == "role":
                r = self.rm.resolve_role(guild, cfg.get(res.key))
                if not r and res.required:
                    missing.append(res.key)
        # also check SYSTEM_GROUPS channel settings
        try:
            from modules.system_panels import SYSTEM_GROUPS
            for g in SYSTEM_GROUPS.values():
                for s in g["subsystems"]:
                    if s["key"] == key:
                        for st in s.get("settings", []):
                            if st.get("type") == "channel" and st.get("key") not in [m for m in missing]:
                                ch = self.rm.resolve_channel(guild, cfg.get(st["key"]))
                                if not ch and cfg.get(st["key"]) is None and st.get("key") not in missing:
                                    # channel not set -> would create
                                    missing.append(st["key"])
                        break
        except Exception:
            pass
        perms_ok = guild.me.guild_permissions.manage_channels and guild.me.guild_permissions.manage_roles if guild.me else False
        return {"ok": True, "missing": missing, "perms_ok": perms_ok, "enabled": bool(cfg.get("enabled"))}

    async def dry_run(self, guild: discord.Guild, key: str) -> dict:
        """Alias for preflight (V10 naming)."""
        return self.preflight(guild, key)

    async def test(self, guild: discord.Guild, key: str) -> dict:
        cap = get_capability(key)
        if not cap:
            return {"ok": False, "error": "unknown"}
        if key == "verification":
            cfg = dm.get_guild_data(guild.id, cap.config_key, {}) or {}
            ch = self.rm.resolve_channel(guild, cfg.get("verify_channel"))
            vr = self.rm.resolve_role(guild, cfg.get("verified_role"))
            ur = self.rm.resolve_role(guild, cfg.get("unverified_role"))
            # diagnostics mirroring run_diagnostics + view registration
            checks = [
                ("verification loaded", self.bot.verification is not None),
                ("enabled", bool(cfg.get("enabled"))),
                ("verify channel exists", ch is not None),
                ("bot can view channel", bool(ch and guild.me and ch.permissions_for(guild.me).view_channel)),
                ("bot can send", bool(ch and guild.me and ch.permissions_for(guild.me).send_messages)),
                ("verified role exists", vr is not None),
                ("unverified role exists", ur is not None),
                ("bot can manage verified", bool(vr and guild.me and vr.position < guild.me.top_role.position)),
                ("bot can manage unverified", bool(ur and guild.me and ur.position < guild.me.top_role.position)),
            ]
            ok = all(v for _, v in checks)
            # also show panel existence: history scan
            panel_ok = False
            if ch:
                msg = await self.rm.find_panel_message(ch, "VerificationView", limit=20)
                panel_ok = msg is not None
                checks.append(("verify panel exists", panel_ok))
                ok = ok and panel_ok
            return {"ok": ok, "checks": checks}
        # generic: run diagnostics via system_panels run_diagnostics
        try:
            from modules.system_panels import run_diagnostics
            spec = {"subsystems": [{"key": key, "label": cap.label, "module_attr": cap.runtime_owner.lower(), "config_key": cap.config_key, "settings": [{"type":"channel","key":"verify_channel"}] if key=="verification" else []}]}  # minimal
            # fallback to real SYSTEM_GROUPS if available
            from modules.system_panels import SYSTEM_GROUPS
            for g in SYSTEM_GROUPS.values():
                for s in g["subsystems"]:
                    if s["key"] == key:
                        spec = g
                        break
            diags = await run_diagnostics(self.bot, guild, spec)
            return {"ok": True, "diagnostics": diags}
        except Exception as e:
            return {"ok": False, "error": str(e)}
