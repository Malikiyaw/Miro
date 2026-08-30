"""MIRO V11 — capability package tests.

Covers sections 1-63 of the V11 spec. All tests are hermetic (no Discord,
no network) and use the in-memory implementations.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="capability_"))

import json
import time
import asyncio
from typing import Any, Dict, List

import pytest

from capability import (
    ToolDefinition, ToolResult, ToolCategory, DangerLevel, Idempotency,
    ErrorClass, FailureKind,
    ToolRegistry, GLOBAL_REGISTRY,
    Resolver, ResolutionResult, ResolutionSource, IdProvenance,
    PermissionPreflight, PermissionCheck, RoleHierarchyEngine, ChannelPermissionEngine,
    PERMISSION_FLAGS,
    RiskEngine, ConfirmationEngine, HumanConfirmationPolicy, DryRun, ScopeLimit,
    RiskAssessment,
    BulkExecutor, BulkResult, BulkMode, BulkOutcome, ActionLock, LockManager,
    JobControl, JobStatus, TransactionPlanner,
    DiscordObserver, VerificationOutcome, bounded_poll, classify_error, RecoveryEngine,
    RecoveryAction,
    RateLimitManager, RateLimitBucket, ActionQueue, QueueItem, PerGuildConcurrency,
    CircuitBreaker, CircuitState,
    ToolHealth, ToolHealthMonitor, JSONSchemaValidator,
    SecretRedactor, ToolSecurityPolicy,
    ServerSnapshotter, RollbackRegistry, RollbackPlan,
    LoopGuard, ToolCallBudget, BudgetExceeded, Simulator, SimulationResult,
    CompletionGate,
    ExecutionReceipt, ReceiptManager,
    CompositeTools, audit_server, repair_system, run_system_test,
    IntentClassifier, CapabilityDiscovery, DiscoverySlice,
    bootstrap_registry, get_default_registry, wrap_action_method,
)
from capability.contract import parameters_hash


# ===================================================================
# Section 1: ToolDefinition contract
# ===================================================================

class TestToolDefinition:
    def test_minimal(self):
        t = ToolDefinition(name="x", description="x")
        assert t.name == "x"
        assert t.danger_level == DangerLevel.LOW
        assert t.mutates_state is False
        assert t.idempotency == Idempotency.UNSAFE

    def test_to_dict_includes_category_value(self):
        t = ToolDefinition(name="x", description="x", category=ToolCategory.DESTRUCTIVE)
        d = t.to_dict()
        assert d["category"] == "destructive"
        assert d["danger_level"] == "low"
        assert d["idempotency"] == "unsafe"

    def test_fingerprint_stable(self):
        t1 = ToolDefinition(name="x", description="x", category=ToolCategory.WRITE, version="1")
        t2 = ToolDefinition(name="x", description="x", category=ToolCategory.WRITE, version="1")
        assert t1.fingerprint() == t2.fingerprint()
        t3 = ToolDefinition(name="x", description="x", category=ToolCategory.DESTRUCTIVE, version="1")
        assert t1.fingerprint() != t3.fingerprint()


class TestToolResult:
    def test_ok(self):
        r = ToolResult.ok(channel_id="123")
        assert r.success and r.verified
        assert r.data == {"channel_id": "123"}

    def test_fail(self):
        r = ToolResult.fail("bad", cls_err=ErrorClass.INVALID_PARAMS,
                            kind=FailureKind.PERMANENT)
        assert not r.success
        assert r.errors == ["bad"]
        assert r.error_class == ErrorClass.INVALID_PARAMS
        assert r.failure_kind == FailureKind.PERMANENT

    def test_to_dict_includes_error_values(self):
        r = ToolResult.fail("x", cls_err=ErrorClass.NOT_FOUND, kind=FailureKind.REPLAN_REQUIRED)
        d = r.to_dict()
        assert d["error_class"] == "not_found"
        assert d["failure_kind"] == "replan_required"

    def test_add_observation_idempotent(self):
        r = ToolResult.ok()
        r.add_observation("a").add_observation("").add_observation("b")
        assert r.observations == ["a", "b"]


class TestParametersHash:
    def test_stable_across_key_order(self):
        a = parameters_hash({"a": 1, "b": 2})
        b = parameters_hash({"b": 2, "a": 1})
        assert a == b

    def test_different_for_different_values(self):
        assert parameters_hash({"a": 1}) != parameters_hash({"a": 2})


# ===================================================================
# Section 3: ToolRegistry 2.0
# ===================================================================

class TestToolRegistry:
    def _reg(self) -> ToolRegistry:
        r = ToolRegistry()
        r.register(ToolDefinition(name="delete_channel", description="Delete a channel",
                                  category=ToolCategory.DESTRUCTIVE,
                                  keywords=("delete", "channel"),
                                  intent_examples=("delete #general",)),
                   executor=lambda p: ToolResult.ok())
        r.register(ToolDefinition(name="send_message", description="Send a message",
                                  category=ToolCategory.WRITE,
                                  keywords=("send", "message"),
                                  intent_examples=("post a message",)))
        r.register(ToolDefinition(name="get_channel", description="Get a channel",
                                  category=ToolCategory.READ,
                                  keywords=("get", "channel")))
        return r

    def test_register_and_get(self):
        r = self._reg()
        assert r.exists("delete_channel")
        assert not r.exists("nope")
        with pytest.raises(KeyError):
            r.get("nope")

    def test_search(self):
        r = self._reg()
        out = r.search("delete channel")
        assert out and out[0].name == "delete_channel"

    def test_discover_intent(self):
        r = self._reg()
        out = r.discover("delete the duplicate warning channels")
        assert any(t.name == "delete_channel" for t in out)

    def test_alias(self):
        r = self._reg()
        r.alias("del_chan", "delete_channel")
        assert r.resolve("del_chan") == "delete_channel"
        assert r.exists("del_chan")
        r.unregister("delete_channel")
        assert not r.exists("del_chan")

    def test_capabilities_count(self):
        r = self._reg()
        caps = r.capabilities()
        assert caps.get("destructive") == 1
        assert caps.get("write") == 1
        assert caps.get("read") == 1

    def test_health_reports_missing_executor(self):
        r = ToolRegistry()
        r.register(ToolDefinition(name="x", description="x"))
        h = r.health()
        assert h["missing_executors"] == ["x"]

    def test_dependency_graph(self):
        r = ToolRegistry()
        r.register(ToolDefinition(name="a", description="a", dependencies=("b", "c")))
        r.register(ToolDefinition(name="b", description="b"))
        g = r.dependency_graph()
        assert g["a"] == ["b", "c"]


# ===================================================================
# Sections 6, 7, 35, 36: Resolver + provenance
# ===================================================================

class TestResolver:
    def _r(self) -> Resolver:
        r = Resolver()
        r.add_channel("g1", "100", "general")
        r.add_channel("g1", "101", "warnings")
        r.add_member("g1", "200", "Alice")
        r.add_role("g1", "300", "Moderator")
        return r

    def test_exact_name(self):
        r = self._r()
        res = r.resolve_channel("general", "g1")
        assert res.resolved and res.id == "100" and res.confidence == 1.0
        assert res.candidates[0].source == ResolutionSource.DISCORD_LOOKUP

    def test_digits_passthrough(self):
        r = self._r()
        res = r.resolve_channel("999", "g1")
        assert res.resolved and res.id == "999"
        assert res.candidates[0].source == ResolutionSource.USER_SUPPLIED

    def test_ambiguous_returns_unresolved(self):
        r = Resolver()
        r.add_channel("g1", "100", "foo")
        r.add_channel("g1", "101", "foobar")
        res = r.resolve_channel("foo", "g1")
        assert not res.resolved
        assert len(res.candidates) == 2

    def test_contains_single_match_resolves(self):
        r = self._r()
        res = r.resolve_channel("warning", "g1")  # contains "warning" → only "warnings" matches
        assert res.resolved and res.id == "101"

    def test_member_and_role(self):
        r = self._r()
        assert r.resolve_member("Alice", "g1").id == "200"
        assert r.resolve_role("Moderator", "g1").id == "300"

    def test_is_trusted(self):
        assert IdProvenance(id="1", source=ResolutionSource.DISCORD_LOOKUP, confidence=0.9).is_trusted()
        assert not IdProvenance(id="1", source=ResolutionSource.USER_SUPPLIED, confidence=0.9).is_trusted()

    def test_empty_query(self):
        r = self._r()
        assert not r.resolve_channel("", "g1").resolved
        assert not r.resolve_channel(None, "g1").resolved


# ===================================================================
# Sections 8-11: Permission preflight
# ===================================================================

class TestPermissionPreflight:
    def test_missing_permissions(self):
        p = PermissionPreflight()
        p.set_bot_permissions("g1", ["send_messages"])
        c = p.check(guild_id="g1", tool="delete_channel", target="#general",
                    required=["manage_channels", "send_messages"])
        assert not c.allowed
        assert "manage_channels" in c.missing
        assert "missing" in c.explain().lower()

    def test_administrator_satisfies_all(self):
        p = PermissionPreflight()
        p.set_bot_permissions("g1", ["administrator"])
        c = p.check(guild_id="g1", tool="x", target="y", required=["manage_channels"])
        assert c.allowed

    def test_hierarchy_check(self):
        p = PermissionPreflight()
        p.set_role_hierarchy("g1", ["role_low", "role_bot", "role_top"])
        assert p.check_hierarchy("g1", "role_bot", "role_low")  # bot > low
        assert not p.check_hierarchy("g1", "role_low", "role_top")

    def test_role_hierarchy_engine(self):
        e = RoleHierarchyEngine()
        e.set_order("g1", ["top", "middle", "bottom"])
        assert e.bot_outranks("g1", "top", "bottom")
        assert not e.bot_outranks("g1", "bottom", "top")
        assert e.effective_position("g1", "middle") == 1

    def test_channel_perms_engine(self):
        e = ChannelPermissionEngine()
        e.set_guild("g1", PERMISSION_FLAGS["send_messages"])
        # Override allow for role
        e.set_role_overwrite("c1", "r1", allow=PERMISSION_FLAGS["manage_channels"])
        perms = e.effective("g1", "c1", "m1", ["r1"])
        assert (perms & PERMISSION_FLAGS["manage_channels"]) == PERMISSION_FLAGS["manage_channels"]
        # Member deny
        e.set_member_overwrite("c1", "m1", deny=PERMISSION_FLAGS["send_messages"])
        perms = e.effective("g1", "c1", "m1", ["r1"])
        assert (perms & PERMISSION_FLAGS["send_messages"]) == 0


# ===================================================================
# Sections 12-15, 39: Risk, dry-run, confirmation
# ===================================================================

class TestRisk:
    def test_low_default(self):
        e = RiskEngine()
        t = ToolDefinition(name="x", description="x")
        a = e.assess(t)
        assert a.level == DangerLevel.LOW

    def test_destructive_promotes(self):
        e = RiskEngine()
        t = ToolDefinition(name="delete_channel", description="x",
                           category=ToolCategory.DESTRUCTIVE, mutates_state=True,
                           danger_level=DangerLevel.MEDIUM)
        a = e.assess(t)
        assert a.level in (DangerLevel.HIGH, DangerLevel.CRITICAL)
        assert "destructive op" in a.factors

    def test_bulk_raises_to_critical(self):
        e = RiskEngine()
        t = ToolDefinition(name="bulk_delete_messages", description="x",
                           category=ToolCategory.DESTRUCTIVE, mutates_state=True,
                           danger_level=DangerLevel.HIGH)
        a = e.assess(t, target_count=20, scope=ScopeLimit.SERVER)
        assert a.level == DangerLevel.CRITICAL

    def test_mutation_promotes_low(self):
        e = RiskEngine()
        t = ToolDefinition(name="x", description="x", danger_level=DangerLevel.LOW, mutates_state=True)
        a = e.assess(t)
        assert a.level == DangerLevel.MEDIUM


class TestConfirmation:
    def test_low_auto(self):
        assert HumanConfirmationPolicy().decide(DangerLevel.LOW) == "auto"

    def test_high_asks(self):
        assert HumanConfirmationPolicy().decide(DangerLevel.HIGH) == "ask"

    def test_critical_explicit(self):
        assert HumanConfirmationPolicy().decide(DangerLevel.CRITICAL) == "explicit"

    def test_medium_asks_unless_explicit(self):
        p = HumanConfirmationPolicy()
        assert p.decide(DangerLevel.MEDIUM) == "ask"
        assert p.decide(DangerLevel.MEDIUM, explicit_request=True) == "auto"

    def test_engine_lifecycle(self):
        e = ConfirmationEngine()
        e.request("t1", summary="delete 3 channels", preview=["c1", "c2", "c3"])
        assert e.get("t1")["summary"] == "delete 3 channels"
        assert e.confirm("t1") is True
        assert e.cancel("t2") is False  # unknown


class TestDryRun:
    def test_summary_includes_risk(self):
        e = RiskEngine()
        a = e.assess(ToolDefinition(name="delete_channel", description="x",
                                    category=ToolCategory.DESTRUCTIVE,
                                    danger_level=DangerLevel.HIGH, mutates_state=True))
        d = DryRun.report("delete_channel", would_execute=[{"channel_id": "1"}], risk=a)
        s = d.summary()
        assert "DRY RUN" in s
        assert "delete_channel" in s
        assert "would execute 1 step" in s
        assert "high" in s


# ===================================================================
# Sections 16-18, 20-22: Bulk, locks, planner, jobs
# ===================================================================

class TestBulk:
    def test_best_effort_continues_on_failure(self):
        e = BulkExecutor()
        steps = [
            lambda: ToolResult.ok(),
            lambda: ToolResult.fail("boom"),
            lambda: ToolResult.ok(),
        ]
        r = e.run_sync(steps, mode=BulkMode.BEST_EFFORT)
        assert r.requested == 3
        assert r.attempted == 3
        assert r.succeeded == 2
        assert r.failed == 1
        assert r.outcome == BulkOutcome.PARTIAL

    def test_transactional_stops_on_failure(self):
        e = BulkExecutor()
        steps = [
            lambda: ToolResult.ok(),
            lambda: ToolResult.fail("boom"),
            lambda: ToolResult.ok(),
        ]
        r = e.run_sync(steps, mode=BulkMode.TRANSACTIONAL)
        assert r.outcome == BulkOutcome.FAILED
        assert r.attempted == 2  # stopped early
        assert r.succeeded == 1

    def test_complete(self):
        e = BulkExecutor()
        r = e.run_sync([lambda: ToolResult.ok() for _ in range(3)])
        assert r.outcome == BulkOutcome.COMPLETE
        assert r.ratio() == "3/3"

    def test_lock_prevents_concurrent_target(self):
        lm = LockManager()
        t1 = lm.acquire("c1")
        assert t1 is not None
        assert lm.is_locked("c1")
        # Second acquire fails while held
        assert lm.acquire("c1") is None
        assert lm.release(t1)
        assert not lm.is_locked("c1")

    def test_transaction_planner_state(self):
        tp = TransactionPlanner()
        tp.add("create_channel")
        tp.add("create_role")
        tp.add("send_message")
        tp.mark("create_channel", status=JobStatus.COMPLETED)
        tp.mark("create_role", status=JobStatus.COMPLETED)
        tp.mark("send_message", status=JobStatus.FAILED, error="missing perms")
        st = tp.state()
        assert st["state"] == "PARTIAL"
        assert "send_message" not in st["done"]
        assert st["completed"] == 2

    def test_job_control_lifecycle(self):
        j = JobControl.new(guild_id="g1", actor_id="u1", target_id="c1", tool="delete_channel")
        assert j.status == JobStatus.PENDING
        j.update(JobStatus.RUNNING)
        assert j.status == JobStatus.RUNNING
        j.update(JobStatus.COMPLETED)
        assert j.status == JobStatus.COMPLETED


# ===================================================================
# Sections 23-27: Observer, verifier, classification, recovery
# ===================================================================

class TestObserver:
    def _obs(self) -> DiscordObserver:
        o = DiscordObserver()
        o.upsert_channel({"id": "1", "guild_id": "g1", "name": "general"})
        o.upsert_role({"id": "10", "guild_id": "g1", "name": "Mod"})
        o.upsert_member("g1", {"id": "100", "name": "Alice", "roles": ["10"]})
        o.upsert_message({"id": "m1", "channel_id": "1", "content": "hi"})
        return o

    def test_observe(self):
        o = self._obs()
        assert o.observe_channel("1")["name"] == "general"
        assert o.observe_role("10")["name"] == "Mod"
        assert o.observe_member("g1", "100")["name"] == "Alice"
        assert o.observe_message("m1")["content"] == "hi"

    def test_delete_marks_observed_absent(self):
        o = self._obs()
        o.delete_channel("1")
        assert o.observe_channel("1") is None

    def test_verify_channel_exists(self):
        o = self._obs()
        assert o.verify_channel_exists({"channel_id": "1"}, {}) == VerificationOutcome.VERIFIED
        assert o.verify_channel_exists({"channel_id": "9"}, {}) == VerificationOutcome.NOT_FOUND
        assert o.verify_channel_exists({"channel_id": "1", "name": "wrong"}, {}) == VerificationOutcome.MISMATCH

    def test_verify_channel_deleted(self):
        o = self._obs()
        o.delete_channel("1")
        assert o.verify_channel_deleted({"channel_id": "1"}, {}) == VerificationOutcome.VERIFIED

    def test_verify_role_on_member(self):
        o = self._obs()
        assert o.verify_role_on_member({"guild_id": "g1", "member_id": "100", "role_id": "10"}, {}) \
            == VerificationOutcome.VERIFIED
        assert o.verify_role_on_member({"guild_id": "g1", "member_id": "100", "role_id": "99"}, {}) \
            == VerificationOutcome.MISMATCH

    def test_bounded_poll_succeeds(self):
        calls = [0]
        def check():
            calls[0] += 1
            return VerificationOutcome.VERIFIED if calls[0] >= 2 else VerificationOutcome.UNKNOWN
        out = bounded_poll(check, max_attempts=4, interval_seconds=0.0)
        assert out == VerificationOutcome.VERIFIED

    def test_bounded_poll_times_out(self):
        out = bounded_poll(lambda: VerificationOutcome.UNKNOWN, max_attempts=2, interval_seconds=0.0)
        assert out == VerificationOutcome.TIMEOUT

    def test_classify_error(self):
        cases = [
            ("Missing Permissions", ErrorClass.PERMISSION_DENIED, FailureKind.PERMANENT),
            ("Unknown Channel (404)", ErrorClass.NOT_FOUND, FailureKind.REPLAN_REQUIRED),
            ("You are being rate limited.", ErrorClass.RATE_LIMITED, FailureKind.TRANSIENT),
            ("Invalid Form Body", ErrorClass.INVALID_PARAMS, FailureKind.PERMANENT),
            ("Timeout", ErrorClass.TIMEOUT, FailureKind.TRANSIENT),
            ("Hierarchy: role above", ErrorClass.HIERARCHY_ERROR, FailureKind.PERMANENT),
        ]
        for msg, ec, fk in cases:
            assert classify_error(msg) == (ec, fk), msg

    def test_recovery_decisions(self):
        r = RecoveryEngine()
        assert r.decide("Missing Permissions") == RecoveryAction.STOP
        assert r.decide("Unknown Channel") == RecoveryAction.RE_RESOLVE
        assert r.decide("Rate limit") == RecoveryAction.RETRY
        assert r.decide("Invalid Form Body") == RecoveryAction.USER_INPUT


# ===================================================================
# Sections 28-31: Queue, rate limit, circuit breaker
# ===================================================================

class TestQueue:
    def test_rate_limit_consume(self):
        rm = RateLimitManager()
        rm.update_route("/channels", remaining=2, reset_at=0)
        assert rm.can_dispatch("/channels", None)
        assert rm.can_dispatch("/channels", None)
        assert not rm.can_dispatch("/channels", None)

    def test_guild_bucket(self):
        rm = RateLimitManager()
        rm.update_guild("g1", remaining=1, reset_at=0)
        assert rm.can_dispatch("r", "g1")
        assert not rm.can_dispatch("r", "g1")

    def test_action_queue_serializes_per_guild(self):
        q = ActionQueue(per_guild_concurrency=2)
        for i in range(4):
            q.enqueue(QueueItem(name="x", kwargs={"guild_id": "g1"}))
        first = q.next_for_guild("g1")
        second = q.next_for_guild("g1")
        assert first is not None and second is not None
        # 3rd: at limit → None
        assert q.next_for_guild("g1") is None
        q.finish("g1")
        # now ok
        assert q.next_for_guild("g1") is not None

    def test_per_guild_concurrency(self):
        c = PerGuildConcurrency(limit=2)
        assert c.try_acquire("g1") and c.try_acquire("g1")
        assert not c.try_acquire("g1")
        c.release("g1")
        assert c.try_acquire("g1")

    def test_circuit_breaker_opens(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert not cb.can_proceed()
        time.sleep(0.1)
        assert cb.can_proceed()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED


# ===================================================================
# Sections 32-34: Health, versioning, schema validation
# ===================================================================

class TestHealth:
    def test_record_and_report(self):
        m = ToolHealthMonitor()
        m.register("delete_channel", "1")
        m.record("delete_channel", success=True, latency_ms=10, verified=True)
        m.record("delete_channel", success=False, latency_ms=5)
        m.record("delete_channel", success=True, latency_ms=20, verified=False)
        h = m.health("delete_channel")
        assert h.calls == 3
        assert abs(h.success_rate() - 2/3) < 1e-6
        rep = m.report()
        assert rep and rep[0]["tool"] == "delete_channel"

    def test_version_tracking(self):
        m = ToolHealthMonitor()
        m.register("delete_channel", "1")
        assert m.version("delete_channel").version == "1"

    def test_json_schema_validator(self):
        s = {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "count": {"type": "integer", "minimum": 1},
                "tags": {"type": "array", "minItems": 1},
            },
            "required": ["channel_id"],
        }
        errs = JSONSchemaValidator.validate({}, s)
        assert "channel_id: required" in errs
        errs = JSONSchemaValidator.validate({"channel_id": "x", "count": 0, "tags": []}, s)
        assert any("below minimum" in e for e in errs)
        assert any("minItems" in e for e in errs)
        errs = JSONSchemaValidator.validate({"channel_id": 1}, s)
        assert any("expected string" in e for e in errs)
        errs = JSONSchemaValidator.validate({"channel_id": "x", "count": 1, "tags": ["a"]}, s)
        assert errs == []


# ===================================================================
# Sections 58-60: Secrets, policy
# ===================================================================

class TestSecrets:
    def test_redacts_discord_bot_token(self):
        s = "Token: Mabcdefghijklmnopqrstuvw.AAAAAAA-BBBBBBBBBB.cccccccccccccccccccccccccccccc"
        r = SecretRedactor()
        out = r.redact(s)
        assert "[REDACTED" in out

    def test_redacts_webhook_url(self):
        s = "POST to https://discord.com/api/webhooks/123/abcdefghij_secret"
        r = SecretRedactor()
        out = r.redact(s)
        assert "webhook" in out and "REDACTED" in out

    def test_redacts_nested(self):
        r = SecretRedactor()
        d = {"authorization": "Bearer abc123def456ghi789jkl012mno",
             "nested": {"x-api-key": "supersecretvalue123"}}
        out = r.redact(d)
        assert "REDACTED" in out["authorization"]
        assert "REDACTED" in out["nested"]["x-api-key"]

    def test_disabled_is_passthrough(self):
        r = SecretRedactor(enabled=False)
        assert r.redact("Bearer abc") == "Bearer abc"

    def test_policy_default_blocks_bypass(self):
        p = ToolSecurityPolicy()
        ok, reasons = p.assert_can_call(permissions_ok=False, confirmation_ok=True)
        assert not ok and "permission_denied" in reasons
        assert p.assert_audit()
        assert p.assert_verification()


# ===================================================================
# Sections 37-38: Snapshot, rollback
# ===================================================================

class TestSnapshot:
    def _obs(self) -> DiscordObserver:
        o = DiscordObserver()
        o.upsert_channel({"id": "1", "guild_id": "g1", "name": "general"})
        o.upsert_role({"id": "10", "guild_id": "g1", "name": "Mod"})
        o.upsert_server({"id": "g1", "name": "Test"})
        return o

    def test_take_snapshot(self):
        s = ServerSnapshotter(self._obs())
        snap = s.take("g1")
        assert "1" in snap.channels
        assert "10" in snap.roles
        assert snap.server["name"] == "Test"
        # Roundtrip
        d = snap.to_dict()
        assert d["channels"]["1"]["name"] == "general"
        # Last cache
        assert s.last("g1") is snap

    def test_rollback_record_and_query(self):
        rr = RollbackRegistry()
        rr.record(RollbackPlan(tool="edit_channel", target_id="1",
                               before={"topic": "old"}, after={"topic": "new"},
                               reversible=True))
        rr.record(RollbackPlan(tool="delete_channel", target_id="2",
                               before={}, after={}, reversible=False,
                               reason="irreversible"))
        all_ = rr.pending()
        assert len(all_) == 2
        assert len(rr.pending("1")) == 1


# ===================================================================
# Sections 50-52: Loop guard, budget, simulator
# ===================================================================

class TestGuards:
    def test_loop_guard_detects_repeat(self):
        lg = LoopGuard(max_repeats=3)
        lg.record("delete_channel", {"channel_id": "1"})
        lg.record("delete_channel", {"channel_id": "1"})
        assert not lg.is_looping()
        lg.record("delete_channel", {"channel_id": "1"})
        assert lg.is_looping()
        lg.reset()
        assert not lg.is_looping()

    def test_loop_guard_with_distinguishing_param(self):
        lg = LoopGuard(max_repeats=3)
        # Three different params → no loop.
        lg.record("delete_channel", {"a": 1})
        lg.record("delete_channel", {"a": 2})
        lg.record("delete_channel", {"a": 1})
        assert not lg.is_looping()

    def test_budget_exceeded(self):
        b = ToolCallBudget(max_steps=2, max_mutations=10, max_runtime_seconds=10.0,
                          max_retries_per_action=2)
        b.check(steps=1, mutations=1, started_at=time.time(), retries_used=1)
        with pytest.raises(BudgetExceeded):
            b.check(steps=3, mutations=1, started_at=time.time(), retries_used=1)

    def test_simulator_blocks_on_no_perm(self):
        s = Simulator()
        t = ToolDefinition(name="delete_channel", description="x",
                           category=ToolCategory.DESTRUCTIVE, mutates_state=True,
                           danger_level=DangerLevel.HIGH)
        r = s.simulate(t, {"channel_id": "1"}, permission_check_passed=False)
        assert not r.permissions_ok
        assert "blocked" in r.expected_outcome

    def test_simulator_irreversible(self):
        s = Simulator()
        t = ToolDefinition(name="delete_channel", description="x",
                           category=ToolCategory.DESTRUCTIVE, mutates_state=True,
                           danger_level=DangerLevel.HIGH, supports_rollback=False)
        r = s.simulate(t, {"channel_id": "1"})
        assert not r.reversible
        assert "irreversible" in r.expected_outcome


# ===================================================================
# Section 49: Completion gate
# ===================================================================

class TestCompletion:
    def test_completed_only_when_all_true(self):
        g = CompletionGate()
        g.require("delete_channel")
        c = g.check()
        assert not c.completed  # nothing recorded yet
        g.mark_goal(True)
        g.record_outcome(ToolResult.ok())
        c = g.check()
        assert c.completed

    def test_partial_records_failure(self):
        g = CompletionGate()
        g.require("delete_channel")
        g.mark_goal(True)
        g.record_outcome(ToolResult.fail("oops"))
        c = g.check()
        assert not c.no_unresolved_failures
        assert not c.completed

    def test_record_bulk_complete(self):
        g = CompletionGate()
        g.require("delete_channels")
        g.mark_goal(True)
        b = BulkResult(requested=2, attempted=2, succeeded=2, verified=2, outcome=BulkOutcome.COMPLETE)
        g.record_bulk(b)
        c = g.check()
        assert c.completed


# ===================================================================
# Section 22: Execution receipt
# ===================================================================

class TestReceipt:
    def test_round_trip(self):
        rm = ReceiptManager()
        r = ExecutionReceipt(
            execution_id="e1", job_id="j1", guild_id="g1", actor_id="u1",
            tool="delete_channel", version="1", parameters_hash="abc",
            target_ids=["c1"], started_at=time.time(), finished_at=time.time(),
            permission_result=PermissionCheck(allowed=True),
            safety_result=RiskAssessment(level=DangerLevel.HIGH),
            discord_result=ToolResult.ok(),
        )
        rm.record(r)
        d = r.to_dict()
        assert d["tool"] == "delete_channel"
        assert d["permission"]["allowed"] is True
        assert d["risk"]["level"] == "high"
        assert len(rm.for_guild("g1")) == 1
        assert len(rm.for_tool("delete_channel")) == 1


# ===================================================================
# Sections 41-45: Composite tools
# ===================================================================

class TestComposite:
    def test_setup_verification_calls_expected_tools(self):
        calls: List[str] = []
        def executor(name, params):
            calls.append(name)
            return ToolResult.ok(**params)
        c = CompositeTools(registry=None, executor=executor)
        r = c.setup_verification()
        names = {s.data.get("name") for s in r.steps if "name" in s.data}
        assert "verify" in names
        assert "Verified" in names
        assert r.success

    def test_audit_server_calls_audit_tool(self):
        calls: List[str] = []
        c = CompositeTools(registry=None, executor=lambda n, p: (calls.append(n) or ToolResult.ok()))
        c.audit_server()
        assert "query_channels" in calls
        assert "query_roles" in calls
        assert "get_server_config" in calls

    def test_module_level_helpers(self):
        calls: List[str] = []
        ex = lambda n, p: (calls.append(n) or ToolResult.ok())
        repair_system("logging", registry=None, executor=ex)
        run_system_test("tickets", registry=None, executor=ex)
        assert any("configure_logging" in c or c == "configure_logging" for c in calls)


# ===================================================================
# Sections 2, 4, 40: Intent classification + discovery
# ===================================================================

class TestDiscovery:
    def _reg(self) -> ToolRegistry:
        r = ToolRegistry()
        r.register(ToolDefinition(name="delete_channel", description="Delete a channel",
                                  category=ToolCategory.DESTRUCTIVE))
        r.register(ToolDefinition(name="find_duplicate_channels", description="Find dupes",
                                  category=ToolCategory.READ))
        r.register(ToolDefinition(name="verify_channel_deleted", description="Verify",
                                  category=ToolCategory.DIAGNOSTIC))
        r.register(ToolDefinition(name="get_channel", description="Get a channel",
                                  category=ToolCategory.READ))
        r.register(ToolDefinition(name="send_message", description="Send a message",
                                  category=ToolCategory.WRITE))
        return r

    def test_intent_delete_duplicate(self):
        assert IntentClassifier().classify("Delete the duplicate channels") == "delete_duplicate"
        assert IntentClassifier().is_destructive("delete_duplicate")
        assert IntentClassifier().is_multi_step("setup_tickets")

    def test_discovery_returns_slice(self):
        d = CapabilityDiscovery(self._reg())
        s = d.discover("delete duplicate channels", raw="delete the duplicate warning channels")
        assert s.intent == "delete_duplicate"
        names = [t.name for t in s.all()]
        assert "delete_channel" in names
        assert "find_duplicate_channels" in names

    def test_discovery_handles_unknown_intent(self):
        d = CapabilityDiscovery(self._reg())
        s = d.discover("foobarbaz")
        assert s.intent == "unknown"


# ===================================================================
# Bootstrap: auto-wrap all action_* methods
# ===================================================================

class TestBootstrap:
    def _handler(self):
        from actions import ActionHandler
        return ActionHandler.__new__(ActionHandler)

    def test_wraps_all_action_methods(self):
        h = self._handler()
        r = bootstrap_registry(h)
        # 135 action_* methods on ActionHandler
        assert len(r.all_names()) == 135

    def test_categories_assigned(self):
        h = self._handler()
        r = bootstrap_registry(h)
        caps = r.capabilities()
        # Should have a mix: destructive, write, read, security, automation, agent
        assert caps.get("destructive", 0) > 0
        assert caps.get("write", 0) > 0
        assert caps.get("read", 0) > 0
        assert caps.get("security", 0) > 0
        assert caps.get("automation", 0) > 0
        # The agent bucket holds methods that don't start with a recognised prefix
        # (claim_*, convert_*, enable_*, etc.). 70 is the current observed ceiling.
        assert caps.get("agent", 0) < 80

    def test_danger_levels(self):
        h = self._handler()
        r = bootstrap_registry(h)
        # delete_channel should be DESTRUCTIVE/HIGH
        assert r.get("delete_channel").danger_level == DangerLevel.HIGH
        # send_message should be WRITE/LOW
        assert r.get("send_message").danger_level == DangerLevel.LOW
        # bulk_delete_messages should be CRITICAL
        assert r.get("bulk_delete_messages").danger_level == DangerLevel.CRITICAL

    def test_executor_returns_toolresult(self):
        h = self._handler()
        r = bootstrap_registry(h)
        ex = r.get_executor("add_reaction")
        result = ex(parameters={})
        assert not result.success
        assert result.error_class == ErrorClass.INVALID_PARAMS

    def test_executor_with_valid_dict_return(self):
        h = self._handler()
        r = bootstrap_registry(h)
        # Simulate: bind a fake method that returns a dict
        h.fake_method = lambda **kw: {"success": True, "channel_id": kw.get("c")}
        tool = ToolDefinition(name="fake_method", description="x",
                              category=ToolCategory.WRITE)
        r.register(tool, executor=wrap_action_method(h, "fake_method").executor or (lambda p: None))
        # Wrap and exercise
        from capability.bootstrap import _make_executor
        ex = _make_executor(h, "fake_method")
        result = ex({"c": "1"})
        assert result.success
        assert result.data.get("channel_id") == "1"


# ===================================================================
# End-to-end capability pipeline
# ===================================================================

class TestPipeline:
    def test_full_delete_channel_pipeline(self):
        # Resolver → PermissionPreflight → RiskEngine → Verifier → Receipt
        from actions import ActionHandler
        h = ActionHandler.__new__(ActionHandler)
        observer = DiscordObserver()
        registry = bootstrap_registry(h, observer=observer)

        # 1. Resolve target
        resolver = Resolver()
        resolver.add_channel("g1", "999", "general")
        rr = resolver.resolve_channel("general", "g1")
        assert rr.resolved and rr.id == "999"

        # 2. Permission check
        pp = PermissionPreflight()
        pp.set_bot_permissions("g1", ["manage_channels"])
        pc = pp.check(guild_id="g1", tool="delete_channel", target="general",
                      required=registry.get("delete_channel").required_permissions)
        assert pc.allowed

        # 3. Risk assessment
        risk = RiskEngine().assess(registry.get("delete_channel"))
        assert risk.level == DangerLevel.HIGH

        # 4. Confirmation policy
        decision = HumanConfirmationPolicy().decide(risk.level)
        assert decision == "ask"

        # 5. Simulate
        sim = Simulator().simulate(registry.get("delete_channel"), {"channel_id": "999"})
        assert sim.risk.level == DangerLevel.HIGH
        assert not sim.reversible

        # 6. Receipt
        rcp = ExecutionReceipt(
            execution_id="e1", job_id="j1", guild_id="g1", actor_id="u1",
            tool="delete_channel", version="1", parameters_hash=parameters_hash({"c": "999"}),
            target_ids=["999"], started_at=time.time(), finished_at=time.time(),
            permission_result=pc, safety_result=risk, discord_result=ToolResult.ok(),
            verified=True, success=True,
        )
        assert ReceiptManager().record(rcp) is None
        assert rcp.to_dict()["permission"]["allowed"] is True

    def test_bulk_delete_with_locks_and_recovery(self):
        observer = DiscordObserver()
        observer.upsert_channel({"id": "1", "guild_id": "g1", "name": "a"})
        observer.upsert_channel({"id": "2", "guild_id": "g1", "name": "b"})
        lock = LockManager()
        lock.acquire("2")  # pre-lock
        steps = [
            lambda: ToolResult.ok(),
            lambda: ToolResult.fail("Unknown Channel"),
        ]
        r = BulkExecutor(lock_manager=lock).run_sync(steps, mode=BulkMode.BEST_EFFORT,
                                                    targets=["1", "2"])
        assert r.failed == 1
        # Recovery suggestion
        rec = RecoveryEngine().decide(r.outcomes[1].errors[0])
        assert rec == RecoveryAction.RE_RESOLVE

    def test_health_loop_budget_chain(self):
        m = ToolHealthMonitor()
        m.register("delete_channel", "1")
        m.record("delete_channel", success=True, latency_ms=5, verified=True)
        lg = LoopGuard(max_repeats=3)
        # Three identical calls → loops.
        lg.record("delete_channel", {"a": 1})
        lg.record("delete_channel", {"a": 1})
        assert not lg.is_looping()
        lg.record("delete_channel", {"a": 1})
        assert lg.is_looping()
        # Budget guards
        b = ToolCallBudget(max_steps=2)
        b.check(steps=1, mutations=0, started_at=time.time(), retries_used=0)
        with pytest.raises(BudgetExceeded):
            b.check(steps=3, mutations=0, started_at=time.time(), retries_used=0)
