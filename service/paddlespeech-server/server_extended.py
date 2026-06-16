#!/usr/bin/env python3
"""Backward-compatible entrypoint.

Prefer::

    python -m paddlespeech_server --config_file conf/tts_online_application.yaml

or ``./start.sh`` in this directory.
"""
from paddlespeech_server.main import main

if __name__ == "__main__":
    main()
