"""
Logging configuration for ZERO Terminal
"""
import logging
import sys
from pythonjsonlogger import jsonlogger
from datetime import datetime
import os


def setup_logging():
    """Configure application logging."""
    # Ensure logs directory exists
    os.makedirs("logs", exist_ok=True)
    
    # Get log level from settings or default to INFO
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)
    
    # Log file configuration
    log_file = f"logs/zero_terminal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    console_formatter = logging.Formatter(console_format)
    console_handler.setFormatter(console_formatter)
    
    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(level)
    file_format = '%(asctime)s - %(name)s - %(levelname)s - %(pathname)s:%(lineno)d - %(message)s'
    file_formatter = logging.Formatter(file_format)
    file_handler.setFormatter(file_formatter)
    
    # Logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Disable noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    # Specific logger configuration
    loggers = {
        "ZERO": logging.INFO,
        "ZERO_Auth": logging.INFO,
        "ZERO_API": logging.INFO,
        "ZERO_Tasks": logging.INFO,
        "ZERO_Cache": logging.INFO,
        "ZERO_WebSocket": logging.INFO,
        "uvicorn": logging.WARNING,
    }
    
    for name, level in loggers.items():
        logger = logging.getLogger(name)
        logger.setLevel(level)
        
        # Add handlers if not already added
        has_console = any(isinstance(h, logging.StreamHandler) for h in logger.handlers)
        has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)
        
        if not has_console:
            logger.addHandler(console_handler)
        if not has_file:
            logger.addHandler(file_handler)
    
    # Return log file path
    return log_file


def get_logger(name: str) -> logging.Logger:
    """Get logger with given name."""
    return logging.getLogger(name)


def log_metrics(metrics: dict):
    """Log performance metrics."""
    logger = get_logger("ZERO_Metrics")
    logger.info(
        f"METRICS: "
        f"connections={metrics.get('connections', 0)}, "
        f"cache_hits={metrics.get('cache_hits', 0)}, "
        f"cache_misses={metrics.get('cache_misses', 0)}, "
        f"api_calls={metrics.get('api_calls', 0)}, "
        f"errors={metrics.get('errors', 0)}"
    )


if __name__ == "__main__":
    setup_logging()
    logger = get_logger(__name__)
    logger.info("Logging configured successfully")
