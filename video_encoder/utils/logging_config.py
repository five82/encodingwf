#!/usr/bin/env python3

import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

class Colors:
    """ANSI color codes for terminal output"""
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    LIGHTBLUE = '\033[0;94m'
    PURPLE = '\033[0;35m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'  # No Color
    GRAY = '\033[1;30m'

    @classmethod
    def disable_colors(cls) -> None:
        """Disable colors if not outputting to terminal"""
        if not sys.stdout.isatty():
            for attr in dir(cls):
                if not attr.startswith('__'):
                    setattr(cls, attr, '')

class ColoredFormatter(logging.Formatter):
    """Custom formatter adding colors to logs"""

    def __init__(self):
        super().__init__()
        self.COLORS = {
            logging.DEBUG: Colors.LIGHTBLUE,
            logging.INFO: Colors.GREEN,
            logging.WARNING: Colors.YELLOW,
            logging.ERROR: Colors.RED,
            logging.CRITICAL: Colors.PURPLE
        }

    def format(self, record):
        # Add more detailed timestamp format
        timestamp = f"{Colors.GRAY}{datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}{Colors.NC}"
        
        # Add process/thread info for debugging
        thread_info = f"{Colors.GRAY}[{record.process}:{record.thread}]{Colors.NC}" if record.levelno == logging.DEBUG else ""
        
        # Add file info with line numbers
        file_info = f"{Colors.BLUE}[{record.filename}:{record.lineno}]{Colors.NC}"
        
        # Add current file being processed if available
        current_file = f"{Colors.CYAN}[{record.current_file}]{Colors.NC}" if hasattr(record, 'current_file') and record.current_file else ""
        
        # Colorize message based on level
        msg_color = self.COLORS.get(record.levelno, Colors.NC)
        message = f"{msg_color}{record.getMessage()}{Colors.NC}"

        # Combine all parts
        log_entry = f"{timestamp} {thread_info}{file_info}{current_file} {message}"

        # Add exception info with full traceback if present
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            log_entry += f"\n{Colors.RED}{'='*50}\nException details:\n{exc_text}\n{'='*50}{Colors.NC}"

        return log_entry

class ContextLogger(logging.Logger):
    """Custom logger that can track current file being processed"""

    def __init__(self, name: str, level: int = logging.NOTSET):
        super().__init__(name, level)
        self.current_file: Optional[str] = None

    def set_current_file(self, filename: Optional[str]) -> None:
        """Set the current file being processed"""
        self.current_file = filename

    def _log(self, level, msg, args, exc_info=None, extra=None, **kwargs):
        """Override to add current_file to the log record"""
        if extra is None:
            extra = {}
        if self.current_file:
            extra['current_file'] = self.current_file
        super()._log(level, msg, args, exc_info, extra, **kwargs)

def create_log_file(log_dir: Path) -> Path:
    """Create a new log file with timestamp"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f"encode_{timestamp}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.touch()
    log_file.chmod(0o644)  # User read/write, group/others read
    return log_file

def setup_logging(log_dir: Path, debug: bool = False) -> ContextLogger:  # Change return type
    """Configure logging with file and console output"""
    # Disable colors if not outputting to terminal
    Colors.disable_colors()

    # Register custom logger class
    logging.setLoggerClass(ContextLogger)

    # Create logger
    logger = logging.getLogger('video_encoder')  # Use specific name instead of root logger
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # Remove any existing handlers
    logger.handlers = []

    # Create log file
    log_file = create_log_file(log_dir)

    # File handler with standard formatting
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s'
    ))
    file_handler.setLevel(logging.DEBUG)

    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColoredFormatter())
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Log initial message
    logger.info(f"Log file created: {log_file}")
    if debug:
        logger.info("Debug logging enabled")

    return logger  # Return the configured logger

def get_logger() -> ContextLogger:
    """Get the configured logger"""
    return logging.getLogger('video_encoder')  # Return specific logger

# Example usage of context logger
# logger = get_logger()
# logger.set_current_file("video1.mkv")
# logger.info("Processing started")  # Will include video1.mkv in output
# logger.set_current_file(None)
# logger.info("Processing complete")  # Won't include file info
