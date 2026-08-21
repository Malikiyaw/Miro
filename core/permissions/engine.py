from logger import logger
from .context import RequestContext
from .policies import PUBLIC_ACTIONS, SENSITIVE_ACTIONS, NEVER_ESCALATED_SOURCES, PolicyResult
from .roles import RoleHierarchy


class Decision(PolicyResult):
    pass


class PermissionEngine:
    """
    Central validation layer wrapping existing checks (Discord permissions,
    actions.py admin gate, role hierarchy) without removing them.
    Every mutating request — human, AI, scheduled, or automod — flows through
    evaluate() before execution.
    """

    def __init__(self, hierarchy: RoleHierarchy = None):
        self.hierarchy = hierarchy or RoleHierarchy()
        self._deny_counts: dict = {}

    def evaluate(self, ctx: RequestContext) -> Decision:
        """Return an allow/deny decision for the request described by ctx."""
        action = (ctx.action or "").lower()

        if action in PUBLIC_ACTIONS:
            return Decision(True)

        # Bot identity (scheduled tasks) may act but sensitive actions get flagged
        if ctx.is_bot_identity:
            if action in SENSITIVE_ACTIONS:
                logger.info(f"PermissionEngine: scheduled/system execution of sensitive action '{action}' in guild {ctx.guild_id}")
            return Decision(True, confirm=action in SENSITIVE_ACTIONS)

        # Non-admin sources are limited to public actions
        if not ctx.is_admin and not ctx.is_owner:
            if ctx.source in NEVER_ESCALATED_SOURCES:
                return Decision(False, f"source '{ctx.source}' cannot perform '{action}'")
            return Decision(False, f"Administrator permission required for '{action}'")

        # Admins/owners still cannot bypass role hierarchy for role-targeted actions
        if ctx.target_role_position is not None:
            ok = self.hierarchy.can_act_on(
                ctx.user_top_role_position, ctx.bot_top_role_position,
                ctx.target_role_position, ctx.is_owner,
            )
            if not ok:
                self._record_deny(action)
                return Decision(False, "target role is above you or the bot in the hierarchy")
            if self.hierarchy.is_protected(ctx.guild_id, ctx.metadata.get("target_role_id", -1)):
                return Decision(False, "target role is protected by Miro hierarchy config")

        return Decision(True, confirm=action in SENSITIVE_ACTIONS)

    def _record_deny(self, action: str):
        self._deny_counts[action] = self._deny_counts.get(action, 0) + 1

    def stats(self) -> dict:
        return {"denies": dict(self._deny_counts)}
