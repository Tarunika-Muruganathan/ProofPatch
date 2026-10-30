#!/bin/sh
set -eu
IFS= read -r command || command="pytest -q"
case "$command" in
  "pytest -q") exec pytest -q ;;
  "python -m pytest -q") exec python -m pytest -q ;;
  "python -m unittest") exec python -m unittest ;;
  *) echo "Blocked test command." >&2; exit 64 ;;
esac
