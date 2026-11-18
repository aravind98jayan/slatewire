#!/bin/bash
# Launches SLATEWIRE from wherever this script lives, regardless of
# which directory you call it from.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
python3 app.py
