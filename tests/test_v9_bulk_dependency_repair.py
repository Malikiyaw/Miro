"""Regression tests for the production failure: bulk delete without IDs."""
import asyncio
from types import SimpleNamespace

from agent.executor import _resolve_bulk_delete_ids
from agent.tools import find_all_duplicate_groups, validate_params


class FakeChannel:
    def __init__(self, cid, name, created_at):
        self.id = cid
        self.name = name
        self.category_id = None
        self.created_at = created_at


class FakeGuild:
    def __init__(self, channels):
        self.text_channels = channels


def test_duplicate_scan_returns_exact_duplicate_ids():
    guild = FakeGuild([
        FakeChannel(10, "⚠️_warned_by_warn_lords", "2026-01-01T00:00:00"),
        FakeChannel(20, "⚠️_warned_by_warn_lords", "2026-01-02T00:00:00"),
        FakeChannel(30, "⚠️_warned_by_warn_lords", "2026-01-03T00:00:00"),
    ])
    result = find_all_duplicate_groups(guild)
    assert result["group_count"] == 1
    assert [x["id"] for x in result["groups"][0]["duplicates"]] == ["20", "30"]
    assert result["groups"][0]["original"]["id"] == "10"


def test_missing_bulk_ids_are_resolved_from_named_duplicate_group():
    guild = FakeGuild([
        FakeChannel(10, "⚠️_warned_by_warn_lords", "2026-01-01T00:00:00"),
        FakeChannel(20, "⚠️_warned_by_warn_lords", "2026-01-02T00:00:00"),
        FakeChannel(30, "⚠️_warned_by_warn_lords", "2026-01-03T00:00:00"),
    ])
    ids = _resolve_bulk_delete_ids(guild, {}, "Delete the 3 duplicate ⚠️_warned_by_warn_lords channels")
    assert ids == ["20", "30"]


def test_missing_bulk_ids_fail_closed_when_multiple_groups_are_ambiguous():
    guild = FakeGuild([
        FakeChannel(10, "alpha", "2026-01-01T00:00:00"), FakeChannel(20, "alpha", "2026-01-02T00:00:00"),
        FakeChannel(30, "beta", "2026-01-01T00:00:00"), FakeChannel(40, "beta", "2026-01-02T00:00:00"),
    ])
    try:
        _resolve_bulk_delete_ids(guild, {}, "delete duplicate channels")
    except ValueError as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("ambiguous destructive resolution must fail closed")


def test_internal_dependency_context_allows_executor_repair_but_public_validation_stays_strict():
    assert validate_params("bulk_delete_channels", {"_agent_request": "delete duplicate channels"}) == (True, "")
    assert validate_params("bulk_delete_channels", {})[0] is False
