#!/usr/bin/env bash
# Start Get Meaning on macOS or Linux.
cd "$(dirname "$0")" || exit 1
exec python3 get_meaning.py "$@"
