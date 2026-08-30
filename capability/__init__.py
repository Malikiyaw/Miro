"""MIRO V11 — Discord Action Fabric.

The capability package turns every Miro Discord tool into a first-class
runtime capability. Discord is just the execution target; AI Agent,
/configpanel, /autosetup, diagnostics, and audit all route through the
same canonical pipeline.

Public surface:
    ToolDefinition, ToolResult, ResolutionResult, PermissionCheck
    ExecutionReceipt, BulkResult, ToolHealth, ServerSnapshot
    ToolRegistry, ToolCategory, DangerLevel, Idempotency, ErrorClass
    IntentClassifier, CapabilityDiscovery
    Resolver, PermissionPreflight, RoleHierarchyEngine, ChannelPermissionEngine
    RiskEngine, ConfirmationEngine, DryRun
    BulkExecutor, ActionLock, JobControl
    DiscordObserver, Verifier, VerificationError, RecoveryEngine
    RateLimitManager, ActionQueue, PerGuildConcurrency, CircuitBreaker
    ToolHealthMonitor, ToolVersion, JSONSchemaValidator
    IdProvenance, SecretRedactor, ToolSecurityPolicy
    ServerSnapshotter, RollbackRegistry
    CompositeTools, audit_server, repair_system, test_system
    LoopGuard, ToolCallBudget, Simulator
"""

from .contract import (
    ToolDefinition,
    ToolResult,
    ToolCategory,
    DangerLevel,
    Idempotency,
    ErrorClass,
    FailureKind,
)
from .registry import ToolRegistry, GLOBAL_REGISTRY
from .resolver import (
    Resolver,
    ResolutionResult,
    ResolutionSource,
    IdProvenance,
)
from .permissions import (
    PermissionPreflight,
    PermissionCheck,
    RoleHierarchyEngine,
    ChannelPermissionEngine,
)
from .risk import (
    RiskEngine,
    ConfirmationEngine,
    HumanConfirmationPolicy,
    DryRun,
    ScopeLimit,
)
from .bulk import (
    BulkExecutor,
    BulkResult,
    BulkMode,
    BulkOutcome,
    TransactionPlanner,
    ActionLock,
    LockManager,
    JobControl,
    JobStatus,
)
from .observer import (
    DiscordObserver,
    Verifier,
    VerificationOutcome,
    bounded_poll,
    classify_error,
    RecoveryEngine,
    RecoveryAction,
)
from .queue import (
    RateLimitManager,
    RateLimitBucket,
    ActionQueue,
    PerGuildConcurrency,
    CircuitBreaker,
    CircuitState,
)
from .health import (
    ToolHealth,
    ToolHealthMonitor,
    ToolVersion,
    JSONSchemaValidator,
    parameters_hash,
)
from .secrets import SecretRedactor, ToolSecurityPolicy
from .snapshot import ServerSnapshotter, RollbackRegistry, RollbackPlan
from .composite import (
    CompositeTools,
    audit_server,
    repair_system,
    test_system,
)
from .guards import (
    LoopGuard,
    ToolCallBudget,
    BudgetExceeded,
    Simulator,
    SimulationResult,
)
from .completion import CompletionGate
from .receipt import ExecutionReceipt, ReceiptManager
from .discovery import IntentClassifier, CapabilityDiscovery
from .bootstrap import bootstrap_registry, get_default_registry, wrap_action_method

__all__ = [
    "ToolDefinition",
    "ToolResult",
    "ToolCategory",
    "DangerLevel",
    "Idempotency",
    "ErrorClass",
    "FailureKind",
    "ToolRegistry",
    "GLOBAL_REGISTRY",
    "Resolver",
    "ResolutionResult",
    "ResolutionSource",
    "IdProvenance",
    "PermissionPreflight",
    "PermissionCheck",
    "RoleHierarchyEngine",
    "ChannelPermissionEngine",
    "RiskEngine",
    "ConfirmationEngine",
    "HumanConfirmationPolicy",
    "DryRun",
    "ScopeLimit",
    "BulkExecutor",
    "BulkResult",
    "BulkMode",
    "BulkOutcome",
    "TransactionPlanner",
    "ActionLock",
    "LockManager",
    "JobControl",
    "JobStatus",
    "DiscordObserver",
    "Verifier",
    "VerificationOutcome",
    "bounded_poll",
    "classify_error",
    "RecoveryEngine",
    "RecoveryAction",
    "RateLimitManager",
    "RateLimitBucket",
    "ActionQueue",
    "PerGuildConcurrency",
    "CircuitBreaker",
    "CircuitState",
    "ToolHealth",
    "ToolHealthMonitor",
    "ToolVersion",
    "JSONSchemaValidator",
    "SecretRedactor",
    "ToolSecurityPolicy",
    "ServerSnapshotter",
    "RollbackRegistry",
    "RollbackPlan",
    "CompositeTools",
    "audit_server",
    "repair_system",
    "test_system",
    "LoopGuard",
    "ToolCallBudget",
    "BudgetExceeded",
    "Simulator",
    "SimulationResult",
    "CompletionGate",
    "ExecutionReceipt",
    "ReceiptManager",
    "IntentClassifier",
    "CapabilityDiscovery",
    "bootstrap_registry",
    "get_default_registry",
    "wrap_action_method",
]
