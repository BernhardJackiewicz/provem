#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from cognitive_memory.graphiti_env import check_graphiti_environment, dumps_graphiti_env_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether optional Graphiti live evaluation can run safely.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    report = check_graphiti_environment()
    print(dumps_graphiti_env_report(report, as_json=args.json))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
