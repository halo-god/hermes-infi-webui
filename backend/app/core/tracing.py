"""OpenTelemetry tracing configuration.

Provides distributed tracing for the Hermes API, with support for
exporting traces to Jaeger, Zipkin, or OTLP-compatible backends.

Usage:
    from app.core.tracing import setup_tracing, get_tracer

    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("my_operation") as span:
        span.set_attribute("user.id", user_id)
        # ... do work ...
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry.sdk.trace.export.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter,
)

from app.core.logging import logger

_tracer_provider: TracerProvider | None = None


def setup_tracing(service_name: str = "hermes-api") -> None:
    """Initialize OpenTelemetry tracing.

    Configure via environment variables:
        OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint (e.g., http://jaeger:4317)
        OTEL_SERVICE_NAME: Override service name
        OTEL_CONSOLE_EXPORT: Set to "true" to export to console
    """
    global _tracer_provider

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    service = os.environ.get("OTEL_SERVICE_NAME", service_name)
    console_export = os.environ.get("OTEL_CONSOLE_EXPORT", "false").lower() == "true"

    resource = Resource.create({SERVICE_NAME: service})
    provider = TracerProvider(resource=resource)

    if endpoint:
        try:
            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            processor = BatchSpanProcessor(exporter)
            provider.add_span_processor(processor)
            logger.info("OpenTelemetry tracing enabled: endpoint=%s", endpoint)
        except Exception as e:
            logger.warning("Failed to setup OTLP exporter: %s", e)

    if console_export:
        console_exporter = ConsoleSpanExporter()
        processor = BatchSpanProcessor(console_exporter)
        provider.add_span_processor(processor)
        logger.info("OpenTelemetry console export enabled")

    trace.set_tracer_provider(provider)
    _tracer_provider = provider


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer instance."""
    return trace.get_tracer(name)


@contextmanager
def trace_span(
    name: str,
    attributes: dict | None = None,
) -> Generator[trace.Span, None, None]:
    """Context manager for creating a trace span."""
    tracer = get_tracer("hermes")
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        yield span


def shutdown_tracing() -> None:
    """Shutdown the tracer provider, flushing any pending spans."""
    global _tracer_provider
    if _tracer_provider:
        _tracer_provider.shutdown()
        _tracer_provider = None
