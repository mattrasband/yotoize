"""Logging utilities for yotoize."""

import logging
import sys
from pathlib import Path
from typing import Optional
from datetime import datetime


class YotoizeLogger:
    """Logger for yotoize operations."""
    
    def __init__(self, log_file: Optional[Path] = None, verbose: bool = False):
        """Initialize logger.
        
        Args:
            log_file: Optional path to log file
            verbose: Enable verbose logging
        """
        self.logger = logging.getLogger('yotoize')
        self.logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        
        # Clear existing handlers
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            handler.close()
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        console_format = logging.Formatter('%(levelname)s: %(message)s')
        console_handler.setFormatter(console_format)
        self.logger.addHandler(console_handler)
        
        # File handler (if specified)
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.DEBUG)
            file_format = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(file_format)
            self.logger.addHandler(file_handler)
    
    def info(self, message: str) -> None:
        """Log info message."""
        self.logger.info(message)
    
    def debug(self, message: str) -> None:
        """Log debug message."""
        self.logger.debug(message)
    
    def warning(self, message: str) -> None:
        """Log warning message."""
        self.logger.warning(message)
    
    def error(self, message: str) -> None:
        """Log error message."""
        self.logger.error(message)
    
    def log_operation(self, operation: str, details: dict) -> None:
        """Log an operation with details.
        
        Args:
            operation: Operation name
            details: Dictionary of operation details
        """
        self.info(f"Operation: {operation}")
        for key, value in details.items():
            self.debug(f"  {key}: {value}")
