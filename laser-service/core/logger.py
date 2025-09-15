import logging
import sys
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
import json
from typing import Dict, Any, Optional
from core.config import settings


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for console output"""
    
    grey = "\x1b[38;20m"
    green = "\x1b[32;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    cyan = "\x1b[36;20m"
    reset = "\x1b[0m"
    
    FORMATS = {
        logging.DEBUG: grey + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.INFO: green + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.WARNING: yellow + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.ERROR: red + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.CRITICAL: bold_red + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset
    }
    
    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt='%Y-%m-%d %H:%M:%S')
        return formatter.format(record)


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging"""
    
    def format(self, record):
        log_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno
        }
        
        # Add extra fields if present
        if hasattr(record, 'extra_data'):
            log_data.update(record.extra_data)
            
        return json.dumps(log_data)


class LoggerManager:
    """Manages different loggers with rotating file handlers"""
    
    _instance = None
    _loggers: Dict[str, logging.Logger] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LoggerManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        self.log_dir = Path("logs")
        self.log_dir.mkdir(exist_ok=True)
        
        # Create subdirectories for different log categories
        self.api_log_dir = self.log_dir / "api"
        self.device_log_dir = self.log_dir / "device"
        self.command_log_dir = self.log_dir / "commands"
        self.measurement_log_dir = self.log_dir / "measurements"
        
        for dir_path in [self.api_log_dir, self.device_log_dir, 
                        self.command_log_dir, self.measurement_log_dir]:
            dir_path.mkdir(exist_ok=True)
    
    def get_rotating_handler(self, 
                           filename: str, 
                           max_bytes: int = 10*1024*1024,  # 10MB
                           backup_count: int = 10,
                           formatter: Optional[logging.Formatter] = None) -> RotatingFileHandler:
        """Create a rotating file handler"""
        handler = RotatingFileHandler(
            filename=filename,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        handler.setFormatter(formatter or logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        return handler
    
    def get_timed_rotating_handler(self,
                                 filename: str,
                                 when: str = 'midnight',
                                 interval: int = 1,
                                 backup_count: int = 30,
                                 formatter: Optional[logging.Formatter] = None) -> TimedRotatingFileHandler:
        """Create a time-based rotating file handler"""
        handler = TimedRotatingFileHandler(
            filename=filename,
            when=when,
            interval=interval,
            backupCount=backup_count,
            encoding='utf-8'
        )
        handler.setFormatter(formatter or logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        return handler
    
    def setup_logger(self, 
                    name: str, 
                    log_file: Optional[str] = None,
                    console: bool = True,
                    json_format: bool = False,
                    rotation_type: str = 'size') -> logging.Logger:
        """Setup a logger with optional file and console handlers"""
        
        if name in self._loggers:
            return self._loggers[name]
        
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, settings.log_level))
        logger.handlers = []  # Clear existing handlers
        
        # Console handler
        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(ColoredFormatter())
            logger.addHandler(console_handler)
        
        # File handler
        if log_file:
            formatter = JSONFormatter() if json_format else None
            
            if rotation_type == 'size':
                file_handler = self.get_rotating_handler(log_file, formatter=formatter)
            else:  # time-based rotation
                file_handler = self.get_timed_rotating_handler(log_file, formatter=formatter)
            
            logger.addHandler(file_handler)
        
        self._loggers[name] = logger
        return logger


# Singleton instance
logger_manager = LoggerManager()


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with colored console output"""
    return logger_manager.setup_logger(name)


def get_api_logger() -> logging.Logger:
    """Get logger for API requests/responses"""
    return logger_manager.setup_logger(
        'api',
        log_file=logger_manager.api_log_dir / 'api.log',
        json_format=True,
        rotation_type='time'
    )


def get_device_logger() -> logging.Logger:
    """Get logger for device communication"""
    return logger_manager.setup_logger(
        'device',
        log_file=logger_manager.device_log_dir / 'device.log',
        rotation_type='size'
    )


def get_command_logger() -> logging.Logger:
    """Get logger for laser commands"""
    return logger_manager.setup_logger(
        'commands',
        log_file=logger_manager.command_log_dir / 'commands.log',
        json_format=True,
        rotation_type='size'
    )


def get_measurement_logger() -> logging.Logger:
    """Get logger for measurements"""
    return logger_manager.setup_logger(
        'measurements',
        log_file=logger_manager.measurement_log_dir / 'measurements.log',
        json_format=True,
        rotation_type='time'
    )


def log_api_request(method: str, 
                   path: str, 
                   params: Optional[Dict] = None,
                   body: Optional[Any] = None,
                   headers: Optional[Dict] = None):
    """Log API request details"""
    logger = get_api_logger()
    logger.info("API Request", extra={'extra_data': {
        'method': method,
        'path': path,
        'params': params,
        'body': body,
        'headers': {k: v for k, v in (headers or {}).items() 
                   if k.lower() not in ['authorization', 'cookie']}
    }})


def log_api_response(method: str,
                    path: str,
                    status_code: int,
                    response_time_ms: float,
                    response_body: Optional[Any] = None):
    """Log API response details"""
    logger = get_api_logger()
    level = logging.INFO if status_code < 400 else logging.ERROR
    logger.log(level, "API Response", extra={'extra_data': {
        'method': method,
        'path': path,
        'status_code': status_code,
        'response_time_ms': response_time_ms,
        'response_body': response_body
    }})


def log_command(command: str,
               parameters: Optional[str] = None,
               response: Optional[str] = None,
               device_port: Optional[str] = None,
               success: bool = True):
    """Log laser command execution"""
    logger = get_command_logger()
    level = logging.INFO if success else logging.ERROR
    logger.log(level, f"Command: {command}", extra={'extra_data': {
        'command': command,
        'parameters': parameters,
        'response': response,
        'device_port': device_port,
        'success': success
    }})


def log_measurement(measurement_type: str,
                   value: Optional[float] = None,
                   signal_strength: Optional[int] = None,
                   temperature: Optional[float] = None,
                   error_code: Optional[str] = None,
                   device_port: Optional[str] = None):
    """Log measurement data"""
    logger = get_measurement_logger()
    logger.info(f"Measurement: {measurement_type}", extra={'extra_data': {
        'type': measurement_type,
        'value': value,
        'signal_strength': signal_strength,
        'temperature': temperature,
        'error_code': error_code,
        'device_port': device_port,
        'timestamp': datetime.utcnow().isoformat()
    }})