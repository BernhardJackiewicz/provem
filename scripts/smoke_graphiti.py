#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Callable, Dict, Optional

from cognitive_memory.adapters import AdapterConfigurationError, OptionalDependencyNotInstalled
from cognitive_memory.adapters.graphiti import GraphitiBackend
from cognitive_memory.graphiti_env import (
    check_graphiti_environment,
    dumps_graphiti_env_report,
    graphiti_connection_config,
)
from cognitive_memory.models import Episode, RetrievalRequest, TemporalFact


def run_smoke(
    environment_checker: Callable[[], Dict[str, object]] = check_graphiti_environment,
    config_loader: Callable[[], Dict[str, str]] = graphiti_connection_config,
    backend_factory: Optional[Callable[..., object]] = None,
) -> int:
    report = environment_checker()
    if not report.get("ready"):
        print(dumps_graphiti_env_report(report))
        print('Install with: python3 -m pip install -e ".[graphiti]"')
        print("Configure Neo4j with GRAPHITI_NEO4J_URI, GRAPHITI_NEO4J_USER and GRAPHITI_NEO4J_PASSWORD.")
        return 2

    config = config_loader()
    factory = backend_factory or GraphitiBackend
    try:
        backend = factory(
            neo4j_uri=config.get("neo4j_uri", ""),
            neo4j_user=config.get("neo4j_user", ""),
            neo4j_password=config.get("neo4j_password", ""),
        )
        episode = Episode("FACT user|work_mode|hybrid")
        fact = TemporalFact(
            subject="user",
            relation="work_mode",
            object="hybrid",
            valid_at=episode.timestamp,
            evidence=[episode.id],
        )
        backend.write_episode(episode)
        backend.write_temporal_fact(fact)
        result = backend.query_current_facts(RetrievalRequest(query="work mode"))
    except OptionalDependencyNotInstalled as exc:
        print("Graphiti dependency is not available: %s" % exc)
        print('Install with: python3 -m pip install -e ".[graphiti]"')
        return 2
    except AdapterConfigurationError as exc:
        print("Graphiti is not configured: %s" % exc)
        print("Set GRAPHITI_NEO4J_URI, GRAPHITI_NEO4J_USER and GRAPHITI_NEO4J_PASSWORD.")
        return 2
    except NotImplementedError as exc:
        print("Graphiti adapter is not implemented: %s" % exc)
        print("No live Graphiti result was produced.")
        return 2

    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


def main() -> int:
    return run_smoke()


if __name__ == "__main__":
    sys.exit(main())
