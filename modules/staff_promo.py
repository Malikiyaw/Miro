import asyncio
from datetime import datetime
import json
import discord
from discord.ext import commands, tasks
from typing import Optional, List

import time
from data_manager import dm
from logger import logger
from modules.promotion_service import PromotionService


class PromotionReviewView(discord.ui.View):
    def __init__(self, bot=None, guild_id=None, user_id=None, tier_name=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.tier_name = tier_name

        # Static custom_ids for persistence
        self.approve_btn.custom_id = "promo_approve_btn"
        self.deny_btn.custom_id = "promo_deny_btn"

    def _get_review_data(self, message_id: int):
        return dm.load_json(f"promo_review_{message_id}", default={"upvotes": [], "downvotes": [], "user_id": self.user_id, "tier_name": self.tier_name})

    def _save_review_data(self, message_id: int, data: dict):
        dm.save_json(f"promo_review_{message_id}", data)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅", custom_id="promo_approve_btn")
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("Only Senior Staff can vote.", ephemeral=True)

        data = self._get_review_data(interaction.message.id)
        if interaction.user.id not in data.get("upvotes", []):
            if "upvotes" not in data: data["upvotes"] = []
            data["upvotes"].append(interaction.user.id)
            if interaction.user.id in data.get("downvotes", []):
                data["downvotes"].remove(interaction.user.id)
            self._save_review_data(interaction.message.id, data)

        await self._update_message(interaction, data)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌", custom_id="promo_deny_btn")
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("Only Senior Staff can vote.", ephemeral=True)

        data = self._get_review_data(interaction.message.id)
        if interaction.user.id not in data.get("downvotes", []):
            if "downvotes" not in data: data["downvotes"] = []
            data["downvotes"].append(interaction.user.id)
            if interaction.user.id in data.get("upvotes", []):
                data["upvotes"].remove(interaction.user.id)
            self._save_review_data(interaction.message.id, data)

        await self._update_message(interaction, data)

    async def _update_message(self, interaction, data):
        embed = interaction.message.embeds[0]

        user_id = data.get("user_id") or self.user_id
        tier_name = data.get("tier_name") or self.tier_name

        if not user_id:
            try: user_id = int(embed.footer.text.split("ID: ")[1])
            except: pass
        if not tier_name:
            try: tier_name = embed.description.split("**")[1]
            except: pass

        up_count = len(data.get("upvotes", []))
        down_count = len(data.get("downvotes", []))

        found = False
        for i, field in enumerate(embed.fields):
            if field.name == "Votes":
                embed.set_field_at(i, name="Votes", value=f"✅ {up_count} | ❌ {down_count}", inline=True)
                found = True
                break
        if not found:
            embed.add_field(name="Votes", value=f"✅ {up_count} | ❌ {down_count}", inline=True)

        if not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.message.edit(embed=embed, view=self)

        # Execution logic
        config = interaction.client.staff_promo._get_full_config(interaction.guild_id)
        req_votes = config.get("tier_requirements", {}).get(tier_name, {}).get("votes", 3)

        if up_count >= req_votes:
            # Execute promotion
            guild = interaction.guild
            member = guild.get_member(user_id)
            if member:
                if data.get("executed"): return
                data["executed"] = True
                self._save_review_data(interaction.message.id, data)

                success, msg = await interaction.client.staff_promo.manual_promote(guild, member, tier_name, config)
                try:
                    if success:
                        await interaction.followup.send(f"✅ Threshold met! {member.mention} promoted to **{tier_name}**.", ephemeral=False)
                    else:
                        await interaction.followup.send(f"❌ Promotion failed: {msg}", ephemeral=False)
                except: pass
                # Disable buttons
                for child in self.children: child.disabled = True
                await interaction.message.edit(view=self)


class StaffPromotionSystem:
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.promotion_service = PromotionService()
        
        self._default_tiers = []  # Auto-detected from server roles
        
        self._default_trial_settings = {
            "enabled": True,
            "duration_days": 14,
            "evaluation_metrics": {
                "activity_score_min": 0.3,
                "ticket_resolution_min": 5,
                "voice_hours_min": 10
            },
            "auto_revert_on_fail": True
        }
        
        self._default_metrics = {
            "xp": {"weight": 0.20, "max": 5000, "enabled": True},
            "tenure_days": {"weight": 0.15, "max": 90, "enabled": True},
            "messages": {"weight": 0.15, "max": 1000, "enabled": True},
            "tickets_resolved": {"weight": 0.20, "max": 50, "enabled": True},
            "voice_minutes": {"weight": 0.10, "max": 3600, "enabled": True},
            "rep_received": {"weight": 0.08, "max": 100, "enabled": True},
            "rep_given": {"weight": 0.06, "max": 100, "enabled": True},
            "gamification_score": {"weight": 0.10, "max": 100, "enabled": True},
            "level": {"weight": 0.06, "max": 50, "enabled": True}
        }
        
        self._default_settings = {
            "auto_promote": True,
            "auto_demote": False,
            "demotion_threshold_buffer": 0.1,
            "min_tenure_hours": 72,
            "excluded_users": [],
            "promotion_cooldown_hours": 24,
            "demotion_cooldown_hours": 168,
            "notify_on_promotion": True,
            "notify_on_demotion": True,
            "notify_near_promotion": True,
            "near_promotion_threshold": 0.05,
            "announce_channel": None,
            "log_channel": None,
            "progress_notify_channel": None,
            "review_mode": False,
            "review_channel": None,
            "activity_decay_days": 30,
        }
        
        self._default_tier_requirements = {}
        
        
        self._default_rewards = {
            "promotion_reward_coins": 500,
            "promotion_reward_title": True,
            "demotion_penalty_coins": 200,
        }
        
        self._last_promotion_time = {}
        self._last_demotion_time = {}
        self._last_notification_time = {}
    
    def _detect_server_roles(self, guild: discord.Guild) -> List[dict]:
        """Auto-detect existing staff roles in the server"""
        detected = []
        
        server_roles = guild.roles
        
        rank_keywords = {
            "owner": 1.0,
            "admin": 0.95,
            "head": 0.8,
            "senior": 0.6,
            "lead": 0.75,
            "mod": 0.4,
            "trial": 0.2,
            "helper": 0.15,
            "trainee": 0.1,
        }
        
        for role in sorted(server_roles, key=lambda x: x.position, reverse=True):
            role_name_lower = role.name.lower()
            
            if any(kw in role_name_lower for kw in rank_keywords):
                if not role.is_default():
                    detected.append({
                        "name": role.name,
                        "threshold": rank_keywords.get([k for k in rank_keywords if k in role_name_lower][0], 0.3),
                        "role_name": role.name
                    })
        
        if not detected:
            detected = [
                {"name": "Trial Moderator", "threshold": 0.2, "role_name": "Trial Moderator"},
                {"name": "Moderator", "threshold": 0.4, "role_name": "Moderator"},
                {"name": "Senior Moderator", "threshold": 0.6, "role_name": "Senior Moderator"},
                {"name": "Head Moderator", "threshold": 0.8, "role_name": "Head Moderator"},
            ]
        
        detected.sort(key=lambda x: x.get("threshold", 0))
        return detected

    def _get_full_config(self, guild_id: int) -> dict:
        guild = self.bot.get_guild(guild_id)
        
        cfg = dm.get_guild_data(guild_id, "staff_promo_config", {})
        
        if not cfg.get("tiers") or cfg.get("tiers") == []:
            if guild:
                cfg.setdefault("tiers", self._detect_server_roles(guild))
            else:
                cfg.setdefault("tiers", self._get_fallback_tiers())
        
        cfg.setdefault("metrics", self._default_metrics)
        cfg.setdefault("settings", self._default_settings)
        cfg.setdefault("rewards", self._default_rewards)
        cfg.setdefault("roles_by_tier", {})
        cfg.setdefault("tier_requirements", {})
        cfg.setdefault("pending_reviews", [])
        cfg.setdefault("trial_settings", self._default_trial_settings)
        cfg.setdefault("staff_applications", {})
        cfg.setdefault("application_tracking", {})
        
        return cfg
    
    def _get_fallback_tiers(self) -> List[dict]:
        """Fallback tiers if no server roles detected"""
        return [
            {"name": "Trial Staff", "threshold": 0.2, "role_name": "Trial Staff"},
            {"name": "Staff", "threshold": 0.4, "role_name": "Staff"},
            {"name": "Senior Staff", "threshold": 0.6, "role_name": "Senior Staff"},
            {"name": "Head Staff", "threshold": 0.8, "role_name": "Head Staff"},
            {"name": "Admin", "threshold": 0.95, "role_name": "Admin"},
        ]

    async def _promotion_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed:
            try:
                for guild in self.bot.guilds:
                    await self._evaluate_guild(guild)
            except Exception as e:
                logger.error(f"Staff promo loop error: {e}")
            await asyncio.sleep(3600)

    async def _evaluate_guild(self, guild: discord.Guild):
        config = self._get_full_config(guild.id)
        settings = config.get("settings", self._default_settings)
        
        if not settings.get("auto_promote", True):
            return
        
        tiers = config.get("tiers", self._default_tiers)
        metrics = config.get("metrics", self._default_metrics)
        excluded = settings.get("excluded_users", [])
        
        role_ids = dict(config.get("roles_by_tier", {}))
        for tier in tiers:
            tier_name = tier.get("name")
            if tier_name not in role_ids or not role_ids[tier_name]:
                role_name = tier.get("role_name")
                if role_name:
                    r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                    if r:
                        role_ids[tier_name] = r.id
        
        for member in guild.members:
            if member.bot or member.id in excluded:
                continue
            
            if not self.promotion_service._check_tenure(member, settings):
                continue
            
            await self._evaluate_member(guild, member, tiers, role_ids, metrics, settings, config)

    def _check_tenure(self, member: discord.Member, settings: dict) -> bool:
        min_hours = settings.get("min_tenure_hours", 72)
        if not member.joined_at:
            return False
        tenure_hours = (discord.utils.utcnow() - member.joined_at).total_seconds() / 3600
        return tenure_hours >= min_hours

    async def _check_trial_period(self, guild_id: int, member: discord.Member, config: dict) -> Optional[str]:
        """Check trial period status: 'active', 'complete', 'revert', 'extend', or None if not in trial"""
        trial_settings = config.get("trial_settings", self._default_trial_settings)
        if not trial_settings.get("enabled", True):
            return None
            
        # Check if member has trial moderator role
        tiers = config.get("tiers", self._default_tiers)
        role_ids = dict(config.get("roles_by_tier", {}))
        trial_role_id = None
        
        for tier in tiers:
            if tier.get("name") == "Trial Moderator":
                trial_role_id = role_ids.get(tier.get("name"))
                break
                
        if not trial_role_id:
            return None
            
        has_trial_role = any(r.id == trial_role_id for r in member.roles)
        if not has_trial_role:
            return None
            
        # Get trial start time from user data
        udata = dm.get_guild_data(guild_id, f"user_{member.id}", {})
        trial_start = udata.get("trial_start_time")
        
        if not trial_start:
            # Set trial start time if not set
            udata["trial_start_time"] = discord.utils.utcnow().timestamp()
            dm.update_guild_data(guild_id, f"user_{member.id}", udata)
            return "active"
            
        trial_duration_days = trial_settings.get("duration_days", 14)
        trial_seconds = trial_duration_days * 24 * 3600
        elapsed_time = discord.utils.utcnow().timestamp() - trial_start
        
        if elapsed_time >= trial_seconds:
            # Trial period ended, evaluate performance
            evaluation_result = await self.promotion_service._evaluate_trial_performance(guild_id, member, trial_settings, config)
            if evaluation_result == "pass":
                return "complete"
            elif evaluation_result == "fail":
                return "revert"
            else:
                return "extend"  # Need more time
        else:
            return "active"

    async def _evaluate_trial_performance(self, guild_id: int, member: discord.Member, trial_settings: dict, config: dict) -> str:
        """Evaluate trial performance based on metrics"""
        metrics = trial_settings.get("evaluation_metrics", {
            "activity_score_min": 0.3,
            "ticket_resolution_min": 5,
            "voice_hours_min": 10
        })
        
        # Get user data
        udata = dm.get_guild_data(guild_id, f"user_{member.id}", {})
        
        # Check activity score (from promotion system)
        current_score = self.promotion_service._compute_score(guild_id, member.id, member, config.get("metrics", self._default_metrics))
        activity_score_min = metrics.get("activity_score_min", 0.3)
        
        # Check ticket resolutions (would need ticket system integration)
        ticket_resolutions = udata.get("ticket_resolutions", 0)
        ticket_resolution_min = metrics.get("ticket_resolution_min", 5)
        
        # Check voice hours
        voice_hours = udata.get("voice_minutes", 0) / 60  # Convert to hours
        voice_hours_min = metrics.get("voice_hours_min", 10)
        
        # Evaluate criteria
        score_pass = current_score >= activity_score_min
        ticket_pass = ticket_resolutions >= ticket_resolution_min
        voice_pass = voice_hours >= voice_hours_min
        
        # Require at least 2 out of 3 criteria to pass
        passes = sum([score_pass, ticket_pass, voice_pass])
        
        if passes >= 2:
            return "pass"
        else:
            return "fail"

    async def put_on_probation(self, guild: discord.Guild, member: discord.Member, duration_days: int, reason: str):
        """Put a staff member on probation."""
        udata = dm.get_guild_data(guild.id, f"user_{member.id}", {})
        udata["on_probation"] = True
        udata["probation_reason"] = reason
        udata["probation_start_timestamp"] = time.time()
        udata["probation_end_timestamp"] = time.time() + (duration_days * 24 * 3600)
        dm.update_guild_data(guild.id, f"user_{member.id}", udata)

        # Log
        logger.info(f"StaffPromo[{guild.id}] {member} put on probation for {duration_days} days: {reason}")

        try:
            await member.send(f"⚠️ You have been placed on probation for **{duration_days} days**.\nReason: {reason}\nYour promotion eligibility is paused during this period.")
        except: pass
        return True

    async def end_probation(self, guild: discord.Guild, member: discord.Member):
        """End a staff member's probation early."""
        udata = dm.get_guild_data(guild.id, f"user_{member.id}", {})
        udata["on_probation"] = False
        dm.update_guild_data(guild.id, f"user_{member.id}", udata)

        try:
            await member.send(f"✅ Your probation has ended. You are now eligible for promotions again.")
        except: pass
        return True

    async def _handle_trial_revert(self, guild: discord.Guild, member: discord.Member, tiers, role_ids, settings, config):
        """Handle automatic reversion from trial period"""
        # Remove trial moderator role
        trial_role_id = role_ids.get("Trial Moderator")
        if trial_role_id:
            trial_role = guild.get_role(trial_role_id)
            if trial_role and trial_role in member.roles:
                try:
                    await member.remove_roles(trial_role)
                except Exception as e:
                    logger.error(f"Failed to remove trial role: {e}")
        
        # Add back any previous roles if they existed
        # For simplicity, we'll just remove the trial role and let system evaluate normally
        
        # Reset trial data
        udata = dm.get_guild_data(guild.id, f"user_{member.id}", {})
        if "trial_start_time" in udata:
            del udata["trial_start_time"]
            dm.update_guild_data(guild.id, f"user_{member.id}", udata)
        
        # Notify user
        try:
            await member.send("📋 Your trial period has ended and you did not meet the requirements for promotion. "
                            "Your Trial Moderator role has been removed. You can reapply after improving your activity.")
        except:
            pass
            
        logger.info(f"StaffPromo[{guild.id}] {member} reverted from trial period due to insufficient performance")

    async def _evaluate_member(self, guild: discord.Guild, member: discord.Member, tiers, role_ids, metrics, settings, config):
        user_id = member.id
        cooldown_key = f"{guild.id}_{user_id}"
        cooldown_hours = settings.get("promotion_cooldown_hours", 24)
        
        if cooldown_key in self._last_promotion_time:
            last = self._last_promotion_time[cooldown_key]
            if (discord.utils.utcnow() - last).total_seconds() < cooldown_hours * 3600:
                return
        
        score = self.promotion_service._compute_score(guild.id, user_id, member, metrics)
        target_tier = None
        for tier in sorted(tiers, key=lambda t: t.get("threshold", 0)):
            if score >= tier.get("threshold", 0):
                target_tier = tier
        
        # Check for trial period completion/reversion
        trial_status = await self._check_trial_period(guild.id, member, config)
        if trial_status == "revert":
            await self._handle_trial_revert(guild, member, tiers, role_ids, settings, config)
            return
        elif trial_status == "extend":
            # Extend trial period, don't process promotion
            return
        elif trial_status == "complete":
            # Trial completed successfully, allow promotion to next tier
            pass
        
        current_index = self._get_current_tier_index(member, tiers, role_ids)
        target_index = -1 if not target_tier else tiers.index(target_tier)
        
        if target_index > current_index:
            target_tier_name = target_tier.get("name")
            if not self.promotion_service._check_tier_requirements(guild.id, member, target_tier_name, config):
                return
            
            if settings.get("review_mode", False):
                await self.promotion_service._submit_promotion_review(guild, member, target_tier, score, config)
                return
            
            await self._promote_member(guild, member, target_tier, tiers, role_ids, current_index, settings, config)
            self._last_promotion_time[cooldown_key] = discord.utils.utcnow()
        elif settings.get("auto_demote", False) and target_index < current_index and current_index > 0:
            demotion_cooldown_key = f"{guild.id}_{user_id}_demote"
            demotion_cooldown_hours = settings.get("demotion_cooldown_hours", 168)
            if demotion_cooldown_key in self._last_demotion_time:
                last = self._last_demotion_time[demotion_cooldown_key]
                if (discord.utils.utcnow() - last).total_seconds() < demotion_cooldown_hours * 3600:
                    return
            
            buffer = settings.get("demotion_threshold_buffer", 0.1)
            if score < target_tier.get("threshold", 0) - buffer:
                await self._demote_member(guild, member, target_index, tiers, role_ids, current_index, settings, config)
                self._last_demotion_time[demotion_cooldown_key] = discord.utils.utcnow()
        
        if settings.get("notify_near_promotion", True):
            await self._check_progress_notification(guild, member, score, tiers, role_ids, settings)

    def _get_current_tier_index(self, member: discord.Member, tiers, role_ids) -> int:
        for idx, tier in enumerate(tiers):
            rid = role_ids.get(tier.get("name"))
            if rid and any(r.id == rid for r in member.roles):
                return idx
        return -1



    async def _promote_member(self, guild: discord.Guild, member: discord.Member, target_tier, tiers, role_ids, current_index, settings, config):
        new_role_id = role_ids.get(target_tier.get("name"))
        if new_role_id:
            try:
                role = guild.get_role(new_role_id)
                if role and role not in member.roles:
                    await member.add_roles(role)
            except Exception as e:
                logger.error(f"Failed to assign promotion role: {e}")
        
        for idx in range(current_index + 1):
            if idx >= len(tiers):
                continue
            tier = tiers[idx]
            rid = role_ids.get(tier.get("name"))
            if rid:
                rm = guild.get_role(rid)
                if rm and rm in member.roles:
                    try:
                        await member.remove_roles(rm)
                    except:
                        pass
        
        await self._apply_promotion_rewards(guild, member, target_tier.get("name"), config)
        await self._log_promotion(guild, member, target_tier.get("name"), settings)

    async def _demote_member(self, guild: discord.Guild, member: discord.Member, target_index, tiers, role_ids, current_index, settings, config):
        if target_index < 0:
            for idx in range(current_index + 1):
                if idx >= len(tiers):
                    continue
                tier = tiers[idx]
                rid = role_ids.get(tier.get("name"))
                if rid:
                    rm = guild.get_role(rid)
                    if rm and rm in member.roles:
                        try:
                            await member.remove_roles(rm)
                        except:
                            pass
            new_tier_name = "None"
        else:
            target_tier = tiers[target_index]
            new_role_id = role_ids.get(target_tier.get("name"))
            
            for idx in range(current_index + 1):
                if idx >= len(tiers):
                    continue
                tier = tiers[idx]
                rid = role_ids.get(tier.get("name"))
                if rid:
                    rm = guild.get_role(rid)
                    if rm and rm in member.roles:
                        try:
                            await member.remove_roles(rm)
                        except:
                            pass
            
            if new_role_id:
                try:
                    role = guild.get_role(new_role_id)
                    if role:
                        await member.add_roles(role)
                except:
                    pass
            
            new_tier_name = target_tier.get("name")
        
        await self._apply_demotion_penalty(guild, member, new_tier_name, config)
        await self._log_demotion(guild, member, new_tier_name, settings)

    async def _apply_promotion_rewards(self, guild: discord.Guild, member: discord.Member, new_tier: str, config: dict):
        rewards = config.get("rewards", self._default_rewards)
        
        coins = rewards.get("promotion_reward_coins", 0)
        if coins > 0:
            try:
                user_data = dm.get_guild_data(guild.id, f"user_{member.id}", {})
                user_data["coins"] = user_data.get("coins", 0) + coins
                dm.update_guild_data(guild.id, f"user_{member.id}", user_data)
            except Exception as e:
                logger.error(f"Failed to give promotion coins: {e}")
        
        if rewards.get("promotion_reward_title", True):
            try:
                title_data = dm.get_guild_data(guild.id, f"user_{member.id}_titles", {})
                title = f"Promoted {new_tier}"
                if title not in title_data.get("titles", []):
                    title_data.setdefault("titles", []).append(title)
                    dm.update_guild_data(guild.id, f"user_{member.id}_titles", title_data)
            except:
                pass

    async def _apply_demotion_penalty(self, guild: discord.Guild, member: discord.Member, new_tier: str, config: dict):
        rewards = config.get("rewards", self._default_rewards)
        
        coins = rewards.get("demotion_penalty_coins", 0)
        if coins > 0:
            try:
                user_data = dm.get_guild_data(guild.id, f"user_{member.id}", {})
                user_data["coins"] = max(0, user_data.get("coins", 0) - coins)
                dm.update_guild_data(guild.id, f"user_{member.id}", user_data)
            except Exception as e:
                logger.error(f"Failed to apply demotion penalty: {e}")

    async def _check_progress_notification(self, guild: discord.Guild, member: discord.Member, score: float, tiers, role_ids, settings):
        notif_key = f"{guild.id}_{member.id}_progress"
        if notif_key in self._last_notification_time:
            last = self._last_notification_time[notif_key]
            if (discord.utils.utcnow() - last).total_seconds() < 86400:
                return
        
        config = self._get_full_config(guild.id)
        current_index = self._get_current_tier_index(member, tiers, role_ids)
        
        if current_index < len(tiers) - 1:
            next_tier = tiers[current_index + 1]
            threshold = next_tier.get("threshold", 0)
            threshold_val = settings.get("near_promotion_threshold", 0.05)
            
            if threshold - score <= threshold_val and threshold - score > 0:
                percent_away = (threshold - score) * 100
                try:
                    await member.send(f"🎯 You're **{percent_away:.1f}%** away from being promoted to **{next_tier.get('name')}**! Keep it up!")
                    self._last_notification_time[notif_key] = discord.utils.utcnow()
                except:
                    pass

    async def _log_promotion(self, guild: discord.Guild, member: discord.Member, new_tier: str, settings: dict):
        logger.info(f"StaffPromo[{guild.id}] {member} promoted to {new_tier}")
        
        # Save to history
        logs = dm.get_guild_data(guild.id, "promotion_logs", [])
        logs.append({
            "ts": time.time(),
            "user": str(member),
            "user_id": member.id,
            "to": new_tier,
            "reason": "Automatic criteria met"
        })
        dm.update_guild_data(guild.id, "promotion_logs", logs[-50:])

        log_ch_id = settings.get("log_channel")
        if log_ch_id:
            channel = guild.get_channel(int(log_ch_id))
            if channel:
                try:
                    embed = discord.Embed(
                        title="🎖️ Staff Promotion",
                        description=f"{member.mention} has been promoted to **{new_tier}**",
                        color=discord.Color.green()
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await channel.send(embed=embed)
                except:
                    pass
        
        announce_ch_id = settings.get("announce_channel")
        if announce_ch_id and settings.get("notify_on_promotion", True):
            channel = guild.get_channel(int(announce_ch_id))
            if channel:
                try:
                    await channel.send(f"🎉 Congratulations {member.mention}! Promoted to **{new_tier}**!")
                except:
                    pass

    async def _log_demotion(self, guild: discord.Guild, member: discord.Member, new_tier: str, settings: dict):
        logger.info(f"StaffPromo[{guild.id}] {member} demoted to {new_tier}")
        
        # Save to history
        logs = dm.get_guild_data(guild.id, "promotion_logs", [])
        logs.append({
            "ts": time.time(),
            "user": str(member),
            "user_id": member.id,
            "to": new_tier,
            "reason": "Criteria no longer met"
        })
        dm.update_guild_data(guild.id, "promotion_logs", logs[-50:])

        log_ch_id = settings.get("log_channel")
        if log_ch_id:
            channel = guild.get_channel(int(log_ch_id))
            if channel:
                try:
                    embed = discord.Embed(
                        title="📉 Staff Demotion",
                        description=f"{member.mention} has been demoted to **{new_tier}**",
                        color=discord.Color.red()
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await channel.send(embed=embed)
                except:
                    pass
        
        announce_ch_id = settings.get("announce_channel")
        if announce_ch_id and settings.get("notify_on_demotion", True):
            channel = guild.get_channel(int(announce_ch_id))
            if channel:
                try:
                    await channel.send(f"⚠️ {member.mention} has been demoted to **{new_tier}**.")
                except:
                    pass

    async def manual_promote(self, guild: discord.Guild, target_member: discord.Member, tier_name: str, config: dict):
        tiers = config.get("tiers", self._default_tiers)
        role_ids = dict(config.get("roles_by_tier", {}))
        
        tier = next((t for t in tiers if t.get("name", "").lower() == tier_name.lower()), None)
        if not tier:
            return False, "Tier not found"
        
        tier_index = tiers.index(tier)
        
        for t in tiers:
            tier_name_key = t.get("name")
            if tier_name_key not in role_ids or not role_ids[tier_name_key]:
                role_name = t.get("role_name")
                if role_name:
                    r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                    if r:
                        role_ids[tier_name_key] = r.id
        
        current_index = self._get_current_tier_index(target_member, tiers, role_ids)
        
        for idx in range(current_index + 1):
            if idx >= len(tiers):
                continue
            t = tiers[idx]
            rid = role_ids.get(t.get("name"))
            if rid:
                rm = guild.get_role(rid)
                if rm and rm in member.roles:
                    try:
                        await target_member.remove_roles(rm)
                    except:
                        pass
        
        new_role_id = role_ids.get(tier.get("name"))
        if new_role_id:
            try:
                role = guild.get_role(new_role_id)
                if role:
                    await target_member.add_roles(role)
            except Exception as e:
                return False, str(e)
        
        await self._apply_promotion_rewards(guild, target_member, tier.get("name"), config)
        
        cooldown_key = f"{guild.id}_{target_member.id}"
        self._last_promotion_time[cooldown_key] = discord.utils.utcnow()
        
        return True, f"Promoted to {tier.get('name')}"

    async def submit_peer_vote(self, guild_id, voter_id, target_id):
        """Submit a peer vote for a staff member."""
        votes = dm.get_guild_data(guild_id, f"peer_votes_{target_id}", [])
        if voter_id not in votes:
            votes.append(voter_id)
            dm.update_guild_data(guild_id, f"peer_votes_{target_id}", votes)
            return True
        return False

    async def manual_demote(self, guild: discord.Guild, target_member: discord.Member, tier_name: str, config: dict):
        tiers = config.get("tiers", self._default_tiers)
        role_ids = dict(config.get("roles_by_tier", {}))
        
        if tier_name.lower() == "none":
            tier_index = -1
        else:
            tier = next((t for t in tiers if t.get("name", "").lower() == tier_name.lower()), None)
            if not tier:
                return False, "Tier not found"
            tier_index = tiers.index(tier)
        
        for t in tiers:
            tier_name_key = t.get("name")
            if tier_name_key not in role_ids or not role_ids[tier_name_key]:
                role_name = t.get("role_name")
                if role_name:
                    r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                    if r:
                        role_ids[tier_name_key] = r.id
        
        current_index = self._get_current_tier_index(target_member, tiers, role_ids)
        
        for idx in range(current_index + 1):
            if idx >= len(tiers):
                continue
            t = tiers[idx]
            rid = role_ids.get(t.get("name"))
            if rid:
                rm = guild.get_role(rid)
                if rm and rm in target_member.roles:
                    try:
                        await target_member.remove_roles(rm)
                    except:
                        pass
        
        if tier_index >= 0:
            target_tier = tiers[tier_index]
            new_role_id = role_ids.get(target_tier.get("name"))
            if new_role_id:
                try:
                    role = guild.get_role(new_role_id)
                    if role:
                        await target_member.add_roles(role)
                except:
                    pass
            new_tier_name = target_tier.get("name")
        else:
            new_tier_name = "None"
        
        await self._apply_demotion_penalty(guild, target_member, new_tier_name, config)
        
        demotion_cooldown_key = f"{guild.id}_{target_member.id}_demote"
        self._last_demotion_time[demotion_cooldown_key] = discord.utils.utcnow()
        
        return True, f"Demoted to {new_tier_name}"

    def get_config(self, guild_id: int) -> dict:
        return self._get_full_config(guild_id)

    async def setup(self, interaction: discord.Interaction, params: dict = None):
        guild = interaction.guild
        
        doc_name = "staff-promo-guide"
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            doc_channel = await guild.create_text_channel(doc_name, overwrites=overwrites)
        except:
            doc_channel = interaction.channel
        
        config = self._get_full_config(guild.id)
        tiers = config.get("tiers", self._default_tiers)
        metrics = config.get("metrics", self._default_metrics)
        settings = config.get("settings", self._default_settings)
        rewards = config.get("rewards", self._default_rewards)
        
        embed = discord.Embed(
            title="🧭 Staff Auto-Promotion System",
            description="Automatically promotes/demotes staff based on performance metrics",
            color=discord.Color.green()
        )
        
        tiers_text = "\n".join([f"• **{t['name']}**: {int(t['threshold']*100)}%" for t in tiers])
        embed.add_field(name="📊 Promotion Tiers", value=tiers_text or "No tiers configured", inline=False)
        
        embed.add_field(
            name="⚙️ Configuration",
            value=(
                f"• Auto-promote: `{settings.get('auto_promote', True)}`\n"
                f"• Auto-demote: `{settings.get('auto_demote', False)}`\n"
                f"• Min tenure: `{settings.get('min_tenure_hours', 72)}h`\n"
                f"• Promotion cooldown: `{settings.get('promotion_cooldown_hours', 24)}h`"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🎁 Promotion Rewards",
            value=(
                f"• Coins: `{rewards.get('promotion_reward_coins', 500)}`\n"
                f"• Title: `{rewards.get('promotion_reward_title', True)}`\n"
                f"• Demotion penalty: `{rewards.get('demotion_penalty_coins', 200)}` coins"
            ),
            inline=False
        )
        
        embed.add_field(
            name="💬 Commands",
            value=(
                "• `!staffpromo status` - Check your score\n"
                "• `!staffpromo leaderboard` - Top staff\n"
                "• `!staffpromo progress` - Progress to next tier\n"
                "• `!staffpromo config` - View config (admin)\n"
                "• `!staffpromo promote @user <tier>` - Promote (admin)\n"
                "• `!staffpromo demote @user <tier>` - Demote (admin)\n"
                "• `!staffpromo exclude add/remove @user` - Exclude user\n"
                "• `!staffpromo roles add/remove <tier> @role` - Map roles"
            ),
            inline=False
        )
        
        await doc_channel.send(embed=embed)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["vote"] = json.dumps({"command_type": "peer_vote"})
        custom_cmds["staffpromo status"] = json.dumps({"command_type": "staffpromo_status"})
        custom_cmds["staffpromo leaderboard"] = json.dumps({"command_type": "staffpromo_leaderboard"})
        custom_cmds["staffpromo config"] = json.dumps({"command_type": "staff_promo_config"})
        custom_cmds["staffpromo progress"] = json.dumps({"command_type": "staffpromo_progress"})
        custom_cmds["staffpromo promote"] = json.dumps({"command_type": "staffpromo_promote"})
        custom_cmds["staffpromo demote"] = json.dumps({"command_type": "staffpromo_demote"})
        custom_cmds["staffpromo exclude"] = json.dumps({"command_type": "staffpromo_exclude"})
        custom_cmds["staffpromo roles"] = json.dumps({"command_type": "staffpromo_roles"})
        custom_cmds["staffpromo review"] = json.dumps({"command_type": "staffpromo_review"})
        custom_cmds["staffpromo requirements"] = json.dumps({"command_type": "staffpromo_requirements"})
        custom_cmds["staffpromo bonuses"] = json.dumps({"command_type": "staffpromo_bonuses"})
        custom_cmds["staffpromo approve"] = json.dumps({"command_type": "staffpromo_review"})
        custom_cmds["staffpromo reject"] = json.dumps({"command_type": "staffpromo_review"})
        custom_cmds["staffpromo tiers"] = json.dumps({"command_type": "staffpromo_tiers"})
        custom_cmds["tiers"] = json.dumps({"command_type": "staffpromo_tiers"})
        
        custom_cmds["help staffpromo"] = json.dumps({
            "command_type": "help_embed",
            "title": "Staff Promotion System Help",
            "description": "Auto-promotes/demotes staff based on performance metrics.",
            "fields": [
                {"name": "!staffpromo status", "value": "Check your current promotion score.", "inline": False},
                {"name": "!staffpromo leaderboard", "value": "View top staff members by score.", "inline": False},
                {"name": "!staffpromo progress", "value": "See progress to next tier.", "inline": False},
                {"name": "!staffpromo requirements", "value": "View tier requirements.", "inline": False},
                {"name": "!staffpromo config", "value": "View configuration (admin).", "inline": False},
                {"name": "!staffpromo promote @user <tier>", "value": "Manually promote user (admin).", "inline": False},
                {"name": "!staffpromo demote @user <tier>", "value": "Manually demote user (admin).", "inline": False},
                {"name": "!staffpromo exclude add/remove @user", "value": "Exclude from auto-promotion (admin).", "inline": False},
                {"name": "!staffpromo roles add/remove <tier> @role", "value": "Map roles to tiers (admin).", "inline": False},
                {"name": "!staffpromo tiers", "value": "Manage promotion tiers interactively (admin).", "inline": False},
                {"name": "!tiers", "value": "Manage promotion tiers interactively (admin).", "inline": False},
                {"name": "!staffpromo review", "value": "View pending reviews (admin).", "inline": False},
                {"name": "!staffpromo approve @user", "value": "Approve promotion (admin).", "inline": False},
                {"name": "!staffpromo reject @user", "value": "Reject promotion (admin).", "inline": False},
                {"name": "!help staffpromo", "value": "Show this help embed.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        await interaction.followup.send("✅ Staff Promotion System set up!", ephemeral=True)


class StaffPromoTiersView(discord.ui.View):
    """Interactive hierarchy management for staff tiers"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    def create_embed(self):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])

        embed = discord.Embed(
            title="🏗️ Staff Promotion Tiers Management",
            description="Manage the staff hierarchy tiers below.",
            color=discord.Color.blue()
        )

        if tiers:
            for i, tier in enumerate(tiers):
                embed.add_field(
                    name=f"{i+1}. {tier['name']}",
                    value=f"Role: <@&{tier['role_id']}>\nRequirements: {tier.get('requirements', 'None')}",
                    inline=False
                )
        else:
            embed.add_field(name="No Tiers", value="Add tiers using the buttons below.", inline=False)

        return embed

    @discord.ui.button(label="Add Tier", style=discord.ButtonStyle.success, emoji="➕")
    async def add_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddTierModal(self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Edit Tier", style=discord.ButtonStyle.primary, emoji="✏️")
    async def edit_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])
        if not tiers:
            return await interaction.response.send_message("No tiers to edit.", ephemeral=True)

        select = TierSelect(tiers)
        await interaction.response.send_message("Select a tier to edit:", view=TierSelectView(select), ephemeral=True)

    @discord.ui.button(label="Remove Tier", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def remove_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])
        if not tiers:
            return await interaction.response.send_message("No tiers to remove.", ephemeral=True)

        select = TierSelect(tiers, action="remove")
        await interaction.response.send_message("Select a tier to remove:", view=TierSelectView(select), ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)


class AddTierModal(discord.ui.Modal):
    def __init__(self, guild_id: int):
        super().__init__(title="Add New Tier")
        self.guild_id = guild_id

        self.name_input = discord.ui.TextInput(label="Tier Name", placeholder="e.g. Junior Moderator")
        self.role_id_input = discord.ui.TextInput(label="Role ID", placeholder="123456789")
        self.requirements_input = discord.ui.TextInput(label="Requirements", style=discord.TextStyle.long, placeholder="Activity requirements, etc.", required=False)

        self.add_item(self.name_input)
        self.add_item(self.role_id_input)
        self.add_item(self.requirements_input)

    async def on_submit(self, interaction: discord.Interaction):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])

        try:
            role_id_int = int(self.role_id_input.value)
        except ValueError:
            return await interaction.response.send_message("Invalid role ID.", ephemeral=True)

        new_tier = {
            "name": self.name_input.value,
            "role_id": role_id_int,
            "requirements": self.requirements_input.value or "None"
        }
        tiers.append(new_tier)
        config["tiers"] = tiers
        dm.update_guild_data(self.guild_id, "staff_promo_config", config)

        await interaction.response.send_message(f"✅ Added tier '{self.name_input.value}'!", ephemeral=True)


class TierSelect(discord.ui.Select):
    def __init__(self, tiers: list, action: str = "edit"):
        options = []
        for i, tier in enumerate(tiers):
            options.append(discord.SelectOption(
                label=tier['name'],
                value=str(i),
                description=f"Role: <@&{tier['role_id']}>"
            ))
        super().__init__(placeholder=f"Select tier to {action}", options=options[:25])
        self.tiers = tiers
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0])
        tier = self.tiers[index]

        if self.action == "edit":
            modal = EditTierModal(self.view.guild_id, index, tier)
            await interaction.response.send_modal(modal)
        elif self.action == "remove":
            config = dm.get_guild_data(self.view.guild_id, "staff_promo_config", {})
            tiers = config.get("tiers", [])
            removed = tiers.pop(index)
            config["tiers"] = tiers
            dm.update_guild_data(self.view.guild_id, "staff_promo_config", config)
            await interaction.response.send_message(f"✅ Removed tier '{removed['name']}'!", ephemeral=True)


class TierSelectView(discord.ui.View):
    def __init__(self, select: TierSelect):
        super().__init__()
        self.add_item(select)


class EditTierModal(discord.ui.Modal):
    def __init__(self, guild_id: int, index: int, tier: dict):
        super().__init__(title=f"Edit Tier: {tier['name']}")
        self.guild_id = guild_id
        self.index = index
        self.tier = tier

        self.name_input = discord.ui.TextInput(label="Tier Name", default=tier['name'])
        self.role_id_input = discord.ui.TextInput(label="Role ID", default=str(tier['role_id']))
        self.requirements_input = discord.ui.TextInput(label="Requirements", style=discord.TextStyle.long, default=tier.get('requirements', 'None'))

        self.add_item(self.name_input)
        self.add_item(self.role_id_input)
        self.add_item(self.requirements_input)

    async def on_submit(self, interaction: discord.Interaction):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])

        try:
            role_id_int = int(self.role_id_input.value)
        except ValueError:
            return await interaction.response.send_message("Invalid role ID.", ephemeral=True)

        tiers[self.index] = {
            "name": self.name_input.value,
            "role_id": role_id_int,
            "requirements": self.requirements_input.value
        }
        config["tiers"] = tiers
        dm.update_guild_data(self.guild_id, "staff_promo_config", config)

        await interaction.response.send_message(f"✅ Updated tier '{self.name_input.value}'!", ephemeral=True)


class StaffPromoRequirementsView(discord.ui.View):
    """Per-tier criteria editor"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    def create_embed(self):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])

        embed = discord.Embed(
            title="📋 Staff Promotion Requirements",
            description="Set criteria for each tier.",
            color=discord.Color.green()
        )

        if tiers:
            for tier in tiers:
                embed.add_field(
                    name=tier['name'],
                    value=f"Requirements: {tier.get('requirements', 'None')}\nRole: <@&{tier['role_id']}>",
                    inline=False
                )
        else:
            embed.add_field(name="No Tiers", value="Add tiers first using `!staffpromo tiers`.", inline=False)

        return embed

    @discord.ui.button(label="Set Requirements", style=discord.ButtonStyle.primary, emoji="✏️")
    async def set_requirements(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        tiers = config.get("tiers", [])
        if not tiers:
            return await interaction.response.send_message("No tiers to edit.", ephemeral=True)

        select = TierSelect(tiers, action="requirements")
        await interaction.response.send_message("Select a tier to set requirements:", view=TierSelectView(select), ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)


class StaffPromoStatusView(discord.ui.View):
    """Check staff promotion status"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    def create_embed(self):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        enabled = config.get("enabled", False)
        tiers = config.get("tiers", [])

        embed = discord.Embed(
            title="📊 Staff Promotion Status",
            color=discord.Color.blue() if enabled else discord.Color.red()
        )

        embed.add_field(name="System Status", value="✅ Enabled" if enabled else "❌ Disabled", inline=True)
        embed.add_field(name="Total Tiers", value=str(len(tiers)), inline=True)
        embed.add_field(name="Active Promotions", value="Check individual user progress", inline=False)

        return embed


class StaffPromoLeaderboardView(discord.ui.View):
    """Staff leaderboard"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    def create_embed(self):
        # Placeholder for leaderboard logic
        embed = discord.Embed(
            title="🏆 Staff Leaderboard",
            description="Top performing staff members.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Coming Soon", value="Leaderboard feature under development.", inline=False)
        return embed


class StaffPromoProgressView(discord.ui.View):
    """Personal progress"""
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id

    def create_embed(self):
        embed = discord.Embed(
            title="📈 Your Promotion Progress",
            description="Track your journey through the staff ranks.",
            color=discord.Color.purple()
        )
        embed.add_field(name="Current Tier", value="Check with staff for details.", inline=False)
        return embed


class StaffPromoBonusesView(discord.ui.View):
    """Bonuses management"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    def create_embed(self):
        embed = discord.Embed(
            title="🎁 Promotion Bonuses",
            description="Manage bonus rewards for promotions.",
            color=discord.Color.yellow()
        )
        embed.add_field(name="Bonuses", value="Configure bonuses in the config panel.", inline=False)
        return embed


class StaffPromo(StaffPromotionSystem):
    """Message-based compatibility facade used by actions.py custom-command handlers."""

    async def progress(self, message):
        """!staffpromo progress — show your promotion progress."""
        guild_id = message.guild.id
        config = self.get_config(guild_id)
        if not config.get("enabled", False):
            return await message.channel.send("❌ The staff promotion system is disabled on this server.")

        tiers = config.get("tiers", [])
        embed = discord.Embed(
            title="📈 Staff Promotion Progress",
            description=f"Your journey through the staff ranks in **{message.guild.name}**.",
            color=discord.Color.purple()
        )
        for i, tier in enumerate(tiers[:10]):
            name = tier.get("name", f"Tier {i + 1}")
            req = tier.get("requirements", {})
            embed.add_field(
                name=f"**{name}**",
                value=f"Activity: {req.get('activity', 0)}% • Tenure: {req.get('tenure_days', 0)}d",
                inline=False
            )
        embed.set_footer(text="Use !configpanel staffpromo to configure")
        await message.channel.send(embed=embed)

    async def tiers(self, message):
        """!staffpromo tiers — list all promotion tiers."""
        guild_id = message.guild.id
        config = self.get_config(guild_id)
        tiers = config.get("tiers", [])
        if not tiers:
            return await message.channel.send("📋 No tiers configured yet.")

        embed = discord.Embed(title="🏷️ Staff Promotion Tiers", color=discord.Color.blue())
        for i, tier in enumerate(tiers, 1):
            name = tier.get("name", f"Tier {i}")
            role_id = tier.get("role_id")
            role = message.guild.get_role(int(role_id)) if role_id else None
            embed.add_field(
                name=f"{i}. {name}",
                value=f"Role: {role.mention if role else 'None'} • Min Score: {tier.get('min_score', 0)}",
                inline=False
            )
        await message.channel.send(embed=embed)

    async def config(self, message):
        """!staffpromo config — show the current configuration."""
        guild_id = message.guild.id
        config = self.get_config(guild_id)
        embed = discord.Embed(title="⚙️ Staff Promotion Config", color=discord.Color.green())
        embed.add_field(name="Enabled", value="✅ Yes" if config.get("enabled", False) else "❌ No", inline=True)
        embed.add_field(name="Tiers", value=len(config.get("tiers", [])), inline=True)
        embed.add_field(name="Review Cycle (days)", value=config.get("review_cycle_days", 30), inline=True)
        await message.channel.send(embed=embed)

    async def promote(self, message, args=None):
        """!staffpromo promote @user <tier> — manually promote a member."""
        if not message.author.guild_permissions.administrator:
            return await message.channel.send("❌ Only administrators can promote members.")

        if not message.mentions:
            return await message.channel.send("❌ Usage: `!staffpromo promote @user <tier name>`")

        target = message.mentions[0]
        tier_name = " ".join((args or message.content.split())[3:]).strip('"')
        if not tier_name:
            return await message.channel.send("❌ Usage: `!staffpromo promote @user <tier name>`")

        config = self.get_config(message.guild.id)
        tiers = config.get("tiers", [])
        target_tier = next((t for t in tiers if t.get("name", "").lower() == tier_name.lower()), None)
        if not target_tier:
            return await message.channel.send(f"❌ Tier '{tier_name}' not found.")

        try:
            await self.manual_promote(message.guild, target, target_tier.get("name"), config)
            await message.channel.send(f"✅ **{target.display_name}** promoted to **{target_tier.get('name')}**!")
        except Exception as e:
            logger.error(f"Error in manual promotion: {e}")
            await message.channel.send("❌ Failed to promote member. Please try again.")