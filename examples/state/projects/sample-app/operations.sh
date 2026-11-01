#!/bin/sh
set -eu
case ${1:-} in
  plan)
    printf '%s\n' "Synthetic plan: inspect application source and verify its project marker."
    ;;
  inspect|smoke)
    test -f "$VPS_OPS_SOURCE_DIR/.ops-project"
    printf '%s\n' "Synthetic local check passed."
    ;;
  *)
    printf '%s\n' "Supported example actions: plan, inspect, smoke" >&2
    exit 2
    ;;
esac
