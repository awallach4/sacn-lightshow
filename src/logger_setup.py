import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
import sys

LOG_DIR = Path("logs")

def get_node_logger(node_name: str, log_level_str: str = "INFO") -> logging.Logger:
    """Configures and returns a node-specific logger with file rotation."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    level = getattr(logging, log_level_str.upper(), logging.INFO)
    logger = logging.getLogger(node_name)
    logger.setLevel(level)
    logger.propagate = False
    
    if not logger.handlers:
        log_file = LOG_DIR / f"{node_name}.log"
        
        # 5MB per log file, keeping 3 backups to protect the Pi SD card
        handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        
        formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger

def setup_global_exception_handler(logger: logging.Logger):
    """Hooks into sys.excepthook to log any unhandled exceptions before crashing."""
    def handle_uncaught_exception(exc_type, exc_value, exc_traceback):
        # Ignore KeyboardInterrupt (Ctrl+C) so manual stops exit cleanly
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger.critical(
            "Unhandled exception caused node crash:",
            exc_info=(exc_type, exc_value, exc_traceback)
        )

    sys.excepthook = handle_uncaught_exception