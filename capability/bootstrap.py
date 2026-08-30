"""Bootstrap the capability registry from the existing actions.py.

`bootstrap_registry` auto-wraps every `action_<name>` method on the
existing ActionHandler instance as a ToolDefinition and binds the
executor. The LLM receives a canonical registry; the implementation
remains the same.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .contract import DangerLevel, Idempotency, ToolCategory, ToolDefinition
from .registry import GLOBAL_REGISTRY, ToolRegistry
from .observer import DiscordObserver, bounded_poll, VerificationOutcome


# Heuristics for assigning V11 metadata from a method name + signature.
# Each entry: (category, danger_level, mutates, idempotency, supports_rollback, supports_dry_run, perms)
_METHOD_PROFILE: Dict[str, Dict[str, Any]] = {
    # READ
    "query_": dict(category=ToolCategory.READ, danger=DangerLevel.LOW, mutates=False, idem=Idempotency.SAFE),
    "get_": dict(category=ToolCategory.READ, danger=DangerLevel.LOW, mutates=False, idem=Idempotency.SAFE),
    "list_": dict(category=ToolCategory.READ, danger=DangerLevel.LOW, mutates=False, idem=Idempotency.SAFE),
    # WRITE (mutating, non-destructive)
    "create_": dict(category=ToolCategory.WRITE, danger=DangerLevel.MEDIUM, mutates=True,
                    idem=Idempotency.RESOURCE),
    "edit_": dict(category=ToolCategory.WRITE, danger=DangerLevel.MEDIUM, mutates=True,
                  idem=Idempotency.RESOURCE, rollback=True),
    "add_": dict(category=ToolCategory.WRITE, danger=DangerLevel.MEDIUM, mutates=True,
                 idem=Idempotency.RESOURCE),
    "set_": dict(category=ToolCategory.WRITE, danger=DangerLevel.MEDIUM, mutates=True,
                 idem=Idempotency.RESOURCE, rollback=True),
    "update_": dict(category=ToolCategory.WRITE, danger=DangerLevel.MEDIUM, mutates=True,
                    idem=Idempotency.RESOURCE, rollback=True),
    "send_": dict(category=ToolCategory.WRITE, danger=DangerLevel.LOW, mutates=True,
                  idem=Idempotency.UNSAFE),
    "post_": dict(category=ToolCategory.WRITE, danger=DangerLevel.LOW, mutates=True,
                  idem=Idempotency.UNSAFE),
    "assign_": dict(category=ToolCategory.SECURITY, danger=DangerLevel.HIGH, mutates=True,
                    idem=Idempotency.RESOURCE, perms=("manage_roles",), rollback=True),
    "remove_role": dict(category=ToolCategory.SECURITY, danger=DangerLevel.HIGH, mutates=True,
                        idem=Idempotency.RESOURCE, perms=("manage_roles",), rollback=True),
    "configure_": dict(category=ToolCategory.SYSTEM, danger=DangerLevel.MEDIUM, mutates=True,
                       idem=Idempotency.RESOURCE, rollback=True),
    "install_": dict(category=ToolCategory.SYSTEM, danger=DangerLevel.MEDIUM, mutates=True,
                     idem=Idempotency.RESOURCE, rollback=True),
    "repair_": dict(category=ToolCategory.SYSTEM, danger=DangerLevel.MEDIUM, mutates=True,
                    idem=Idempotency.RESOURCE, rollback=True),
    "test_": dict(category=ToolCategory.DIAGNOSTIC, danger=DangerLevel.LOW, mutates=False,
                  idem=Idempotency.SAFE),
    "audit_": dict(category=ToolCategory.DIAGNOSTIC, danger=DangerLevel.LOW, mutates=False,
                   idem=Idempotency.SAFE),
    # DESTRUCTIVE
    "delete_": dict(category=ToolCategory.DESTRUCTIVE, danger=DangerLevel.HIGH, mutates=True,
                    idem=Idempotency.SAFE, rollback=False),
    "remove_": dict(category=ToolCategory.DESTRUCTIVE, danger=DangerLevel.HIGH, mutates=True,
                    idem=Idempotency.SAFE, rollback=False),
    "ban_": dict(category=ToolCategory.DESTRUCTIVE, danger=DangerLevel.HIGH, mutates=True,
                 idem=Idempotency.SAFE, rollback=False, perms=("ban_members",)),
    "kick_": dict(category=ToolCategory.DESTRUCTIVE, danger=DangerLevel.MEDIUM, mutates=True,
                  idem=Idempotency.SAFE, rollback=False, perms=("kick_members",)),
    "bulk_": dict(category=ToolCategory.DESTRUCTIVE, danger=DangerLevel.CRITICAL, mutates=True,
                  idem=Idempotency.UNSAFE, rollback=False),
    "lock_": dict(category=ToolCategory.SECURITY, danger=DangerLevel.CRITICAL, mutates=True,
                  idem=Idempotency.RESOURCE, perms=("manage_guild",), rollback=True),
    "nuke_": dict(category=ToolCategory.DESTRUCTIVE, danger=DangerLevel.CRITICAL, mutates=True,
                  idem=Idempotency.UNSAFE, rollback=False),
    "purge_": dict(category=ToolCategory.DESTRUCTIVE, danger=DangerLevel.HIGH, mutates=True,
                   idem=Idempotency.SAFE, rollback=False),
    # AUTOMATION
    "create_automation": dict(category=ToolCategory.AUTOMATION, danger=DangerLevel.MEDIUM,
                              mutates=True, idem=Idempotency.RESOURCE, rollback=True),
    "toggle_automation": dict(category=ToolCategory.AUTOMATION, danger=DangerLevel.MEDIUM,
                              mutates=True, idem=Idempotency.RESOURCE, rollback=True),
    "trigger_": dict(category=ToolCategory.AUTOMATION, danger=DangerLevel.LOW, mutates=False,
                     idem=Idempotency.SAFE),
}


def _profile_for(name: str) -> Dict[str, Any]:
    """Pick the V11 metadata for a method name.

    `name` is the raw attribute on the ActionHandler (e.g. `action_delete_channel`).
    We strip the `action_` prefix and match the resulting tool name against
    the profile table.
    """
    base = name[len("action_"):] if name.startswith("action_") else name
    candidates = sorted(_METHOD_PROFILE.items(), key=lambda kv: -len(kv[0]))
    for prefix, profile in candidates:
        if base.startswith(prefix):
            return dict(profile)
    return dict(category=ToolCategory.AGENT, danger=DangerLevel.LOW, mutates=False,
                idem=Idempotency.SAFE)


def _json_schema_for(sig: inspect.Signature) -> Dict[str, Any]:
    """Best-effort JSON schema for the method's parameters.

    Optional params → not required. Anything else → required.
    """
    props: Dict[str, Any] = {}
    required: List[str] = []
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        annot = param.annotation
        t = "string"
        if annot in (int, "int"):
            t = "integer"
        elif annot in (float, "float"):
            t = "number"
        elif annot in (bool, "bool"):
            t = "boolean"
        elif annot in (list, "list") or (isinstance(annot, str) and annot.startswith("list")):
            t = "array"
        elif annot in (dict, "dict") or (isinstance(annot, str) and annot.startswith("dict")):
            t = "object"
        props[pname] = {"type": t}
        if param.default is inspect._empty:
            required.append(pname)
    schema: Dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def _keywords_for(name: str) -> List[str]:
    # Strip "action_" prefix and split on underscores.
    base = name[len("action_"):] if name.startswith("action_") else name
    parts = re.split(r"[_\W]+", base)
    return [p for p in parts if p]


def wrap_action_method(action_handler: Any, method_name: str) -> ToolDefinition:
    """Build a ToolDefinition that wraps `handler.<method_name>`."""
    method = getattr(action_handler, method_name)
    sig = inspect.signature(method)
    profile = _profile_for(method_name)
    base_name = method_name[len("action_"):] if method_name.startswith("action_") else method_name
    description = (method.__doc__ or "").strip().splitlines()[0] if method.__doc__ else f"Auto-wrapped {base_name}"
    return ToolDefinition(
        name=base_name,
        description=description,
        category=profile["category"],
        parameters=_json_schema_for(sig),
        required_permissions=tuple(profile.get("perms", ())),
        danger_level=profile["danger"],
        mutates_state=profile["mutates"],
        supports_dry_run=False,
        supports_rollback=bool(profile.get("rollback", False)),
        idempotency=profile["idem"],
        executor=f"{action_handler.__class__.__module__}.{action_handler.__class__.__name__}.{method_name}",
        verifier=None,
        timeout_seconds=15.0,
        retry_policy="TRANSIENT_ONLY",
        audit=True,
        keywords=tuple(_keywords_for(base_name)),
        intent_examples=(),
        dependencies=(),
        version="1",
        scope="single_resource",
    )


def _make_executor(action_handler: Any, method_name: str):
    """Return a callable that invokes the action_* method and returns a ToolResult."""
    from .contract import ToolResult, ErrorClass, FailureKind

    method = getattr(action_handler, method_name)

    def executor(parameters: Dict[str, Any]) -> ToolResult:
        try:
            rv = method(**parameters)
        except TypeError as e:
            return ToolResult.fail(f"param_error: {e}", cls_err=ErrorClass.INVALID_PARAMS,
                                   kind=FailureKind.PERMANENT)
        except Exception as e:  # noqa: BLE001
            return ToolResult.fail(str(e), cls_err=ErrorClass.UNKNOWN,
                                   kind=FailureKind.PERMANENT)
        # Normalize return shape.
        if isinstance(rv, ToolResult):
            return rv
        if isinstance(rv, dict):
            success = bool(rv.get("success", rv.get("ok", True)))
            verified = bool(rv.get("verified", success))
            errors = list(rv.get("errors", [])) if success is False else []
            return ToolResult(
                success=success,
                verified=verified,
                data={k: v for k, v in rv.items()
                      if k not in ("success", "ok", "verified", "errors")},
                observations=list(rv.get("observations", [])),
                errors=errors,
            )
        if isinstance(rv, tuple) and len(rv) == 2:
            ok, info = rv
            if isinstance(info, dict):
                return ToolResult(
                    success=bool(ok),
                    verified=bool(ok),
                    data=dict(info),
                )
            return ToolResult(success=bool(ok), data={"result": info})
        if isinstance(rv, bool):
            return ToolResult(success=rv)
        return ToolResult(success=True, data={"result": rv})

    return executor


def bootstrap_registry(action_handler: Any, *,
                       registry: Optional[ToolRegistry] = None,
                       observer: Optional[DiscordObserver] = None) -> ToolRegistry:
    """Auto-wrap every `action_*` method on `action_handler` and register it."""
    reg = registry or ToolRegistry()
    for name in dir(action_handler):
        if not name.startswith("action_"):
            continue
        if not callable(getattr(action_handler, name, None)):
            continue
        try:
            tool = wrap_action_method(action_handler, name)
        except (TypeError, ValueError):
            continue
        executor = _make_executor(action_handler, name)
        verifier = None
        if observer is not None:
            verifier = _auto_verifier_for(tool.name, observer)
        reg.register(tool, executor=executor, verifier=verifier)
    return reg


def _auto_verifier_for(tool_name: str, observer: DiscordObserver):
    """Pick a built-in verifier for the most common tool names."""
    if tool_name in ("delete_channel", "delete_role", "delete_message", "remove_role"):
        target = "channel_id" if "channel" in tool_name else (
            "role_id" if "role" in tool_name else "message_id")
        def v(params, snapshot):
            return observer.verify_channel_deleted({target: params.get(target, "")}, snapshot) \
                if target == "channel_id" else (
                observer.verify_role_on_member({"guild_id": params.get("guild_id", ""),
                                                "member_id": params.get("member_id", ""),
                                                "role_id": params.get("role_id", "")}, snapshot) \
                if tool_name == "remove_role" else
                observer.verify_message_absent(params, snapshot))
        return v
    if tool_name in ("create_channel", "create_role", "create_category"):
        target = "channel_id" if "channel" in tool_name else "role_id"
        def v(params, snapshot, t=target):
            return observer.verify_channel_exists({t: params.get(t, "")}, snapshot) \
                if t == "channel_id" else VerificationOutcome.VERIFIED
        return v
    if tool_name in ("send_message", "post_panel"):
        return observer.verify_message_exists
    if tool_name in ("assign_role",):
        return observer.verify_role_on_member
    return None


def get_default_registry(action_handler: Any = None) -> ToolRegistry:
    """Return a ready-to-use registry. If `action_handler` is given, auto-wrap it."""
    if action_handler is not None:
        return bootstrap_registry(action_handler)
    return GLOBAL_REGISTRY
