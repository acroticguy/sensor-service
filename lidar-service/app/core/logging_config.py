import logging
import logging.handlers
import os
from pathlib import Path
from .config import settings


def setup_logging():
    log_dir = Path(settings.LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler = logging.handlers.RotatingFileHandler(
        settings.LOG_FILE,
        maxBytes=settings.LOG_MAX_SIZE,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # For Windows compatibility, ensure UTF-8 encoding
    try:
        if hasattr(console_handler.stream, 'reconfigure'):
            console_handler.stream.reconfigure(encoding='utf-8', errors='replace')
    except:
        pass  # Fallback for older Python versions
    
    logger = logging.getLogger("lidar_service")
    logger.setLevel(getattr(logging, settings.LOG_LEVEL))
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    api_logger = logging.getLogger("api_requests")
    api_logger.setLevel(getattr(logging, settings.LOG_LEVEL))
    api_logger.addHandler(file_handler)
    api_logger.addHandler(console_handler)
    
    return logger


logger = setup_logging()