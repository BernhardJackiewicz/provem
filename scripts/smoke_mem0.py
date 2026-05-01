#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

from cognitive_memory.adapters import AdapterConfigurationError, OptionalDependencyNotInstalled
from cognitive_memory.adapters.mem0 import Mem0Backend
from cognitive_memory.models import Episode, RetrievalRequest


def main() -> int:
    try:
        backend = Mem0Backend()
    except OptionalDependencyNotInstalled as exc:
        print("Mem0 dependency is not available: %s" % exc)
        print('Install with: python3 -m pip install -e ".[mem0]"')
        return 2
    except AdapterConfigurationError as exc:
        print("Mem0 is not configured: %s" % exc)
        print("Set MEM0_API_KEY before running this smoke test.")
        return 2

    episode = Episode("FACT user|work_mode|hybrid")
    backend.ingest(episode)
    result = backend.search(RetrievalRequest(query="work mode"))
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
