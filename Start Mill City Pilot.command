#!/bin/zsh
cd -- "${0:A:h}"
exec .venv/bin/python scripts/run_pilot.py
