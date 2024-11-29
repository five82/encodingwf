#!/usr/bin/env python3

# This is a work in progress.

# There are much better tools available for VMAF target quality chunked encoding
# such as Av1an. But I needed something faster.

# Credit to Reddit user asm-c for the idea and commands.
# https://www.reddit.com/r/ffmpeg/comments/14dk6zl/chunk_based_encoding_system_abav1/

# This workflow splits a video into chunks and uses ab-av1 to encode each chunk to a
# target quality value with VMAF. The chunks are reassembled and the audio tracks are
# encoded seperately and remuxed.

import sys
import logging
from pathlib import Path

from .config import EncoderConfig
from .core.encoder import VideoEncoder
from .utils.logging_config import setup_logging, get_logger, ContextLogger  # Add ContextLogger import
from .utils.exceptions import VideoEncoderError

def init() -> tuple[EncoderConfig, ContextLogger]:
    """Initialize application configuration"""
    config = EncoderConfig()

    # Create all required directories
    for dir_path in config.get_all_dirs().values():
        dir_path.mkdir(parents=True, exist_ok=True)

    # Setup logging and get the logger
    logger = setup_logging(config.get_dir("logs"))

    logger.info("Initialization complete")

    return config, logger

def main() -> int:
    """Main application entry point"""
    try:
        config, logger = init()
        encoder = VideoEncoder(config, logger)  # Pass logger to encoder
        encoder.run()
        return 0

    except VideoEncoderError as e:
        logging.error(f"Encoder error: {e}")
        return 1
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)
        return 1

if __name__ == "__main__":
    sys.exit(main())
