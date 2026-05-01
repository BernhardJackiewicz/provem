"""Adapter contracts, local implementations, mocks, and guarded integrations.

The default package remains dependency-free. Graphiti and Letta are explicit
stubs. Mem0 is an optional baseline adapter behind lazy imports/configuration.
"""

from .base import (
    AdapterConfigurationError,
    ExtractorPort,
    ExternalMemoryBackend,
    LLMExtractorPort,
    OptionalDependencyNotInstalled,
    StatefulAgentBackend,
    TemporalGraphBackend,
)
from .local import LocalTemporalGraphBackend
from .mocks import MockGraphitiBackend, MockLettaBackend, MockMem0Backend

TemporalGraphPort = TemporalGraphBackend
ActiveStatePort = StatefulAgentBackend

__all__ = [
    "AdapterConfigurationError",
    "ActiveStatePort",
    "ExtractorPort",
    "ExternalMemoryBackend",
    "LLMExtractorPort",
    "LocalTemporalGraphBackend",
    "MockGraphitiBackend",
    "MockLettaBackend",
    "MockMem0Backend",
    "OptionalDependencyNotInstalled",
    "StatefulAgentBackend",
    "TemporalGraphBackend",
    "TemporalGraphPort",
]
