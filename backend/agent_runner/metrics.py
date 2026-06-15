"""Agent Runner Prometheus metrics."""
from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge

# Task metrics
TASKS_ENQUEUED = Counter(
    "hermes_runner_tasks_enqueued_total",
    "Tasks enqueued to runner",
    ["type"],
)

TASKS_COMPLETED = Counter(
    "hermes_runner_tasks_completed_total",
    "Tasks completed by runner",
    ["type", "status"],
)

TASKS_FAILED = Counter(
    "hermes_runner_tasks_failed_total",
    "Tasks that failed permanently (sent to DLQ)",
    ["type", "error"],
)

TASK_DURATION = Histogram(
    "hermes_runner_task_duration_seconds",
    "Task execution duration",
    ["type"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
)

# Session pool metrics
ACTIVE_SESSIONS = Gauge(
    "hermes_runner_active_sessions",
    "Number of active ACP sessions",
)

SESSION_POOL_SIZE = Gauge(
    "hermes_runner_session_pool_size",
    "Total sessions in pool",
)

# Redis metrics
REDIS_OPS_FAILED = Counter(
    "hermes_runner_redis_ops_failed_total",
    "Redis operations that failed",
    ["operation"],
)

# Dead letter queue
DLQ_MESSAGES = Counter(
    "hermes_runner_dlq_messages_total",
    "Messages sent to dead letter queue",
    ["reason"],
)
