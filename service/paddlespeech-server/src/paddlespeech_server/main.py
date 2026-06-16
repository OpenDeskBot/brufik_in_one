"""启动 PaddleSpeech server，并注册音素对齐 WebSocket。"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn
from paddlespeech.cli.log import logger
from paddlespeech.server.bin.paddlespeech_server import app
from paddlespeech.server.engine.engine_pool import init_engine_pool
from paddlespeech.server.engine.engine_warmup import warm_up
from paddlespeech.server.utils.config import get_config
from paddlespeech.server.ws.api import setup_router

from .mix_tts import init_mix_phoneme_engine
from .ws_phoneme import extra_router


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PaddleSpeech server + phoneme-aligned TTS WebSocket",
    )
    parser.add_argument(
        "--config_file",
        required=True,
        help="yaml config (same as paddlespeech_server)",
    )
    args = parser.parse_args()

    config = get_config(args.config_file)
    if getattr(config, "protocol", None) != "websocket":
        logger.error("config.protocol must be websocket for this entrypoint")
        sys.exit(1)

    api_list = [engine.split("_")[0] for engine in config.engine_list]
    app.include_router(setup_router(api_list))
    app.include_router(extra_router)

    logger.info("Register extra WS: /paddlespeech/tts/streaming_phoneme")
    if not init_engine_pool(config):
        logger.error("init_engine_pool failed")
        sys.exit(1)

    for engine_and_type in config.engine_list:
        if not warm_up(engine_and_type):
            logger.error(f"warm_up failed for {engine_and_type}")
            sys.exit(1)

    try:
        init_mix_phoneme_engine(args.config_file)
    except Exception as exc:
        logger.error(f"init_mix_phoneme_engine failed: {exc}")
        sys.exit(1)

    host = (os.environ.get("PADDLESPEECH_HOST") or getattr(config, "host", None) or "0.0.0.0").strip()
    port = int(os.environ.get("PADDLESPEECH_PORT") or getattr(config, "port", None) or 8092)
    logger.info(f"Listen on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


def cli() -> None:
    main()


if __name__ == "__main__":
    main()
