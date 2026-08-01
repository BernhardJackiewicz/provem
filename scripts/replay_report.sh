#!/bin/sh
# Kept as the documented entry point; the replay is now self-checking —
# it ASSERTS every published number instead of just printing them.
exec sh "$(dirname "$0")/verify_repro.sh" "$@"
