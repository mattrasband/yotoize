"""Utility functions for finding config files in standard locations."""

import os
import platform
from pathlib import Path
from typing import Optional


def get_user_config_dir() -> Path:
    """Get the user configuration directory for the current platform.
    
    Returns:
        Path to user config directory
    """
    system = platform.system()
    
    if system == "Windows":
        # Windows: %APPDATA%\yotoize
        appdata = os.getenv('APPDATA')
        if appdata:
            return Path(appdata) / 'yotoize'
        # Fallback to user home
        return Path.home() / 'AppData' / 'Roaming' / 'yotoize'
    elif system == "Darwin":  # macOS
        # macOS: ~/Library/Application Support/yotoize
        return Path.home() / 'Library' / 'Application Support' / 'yotoize'
    else:  # Linux and other Unix-like
        # Linux: ~/.config/yotoize
        xdg_config = os.getenv('XDG_CONFIG_HOME')
        if xdg_config:
            return Path(xdg_config) / 'yotoize'
        return Path.home() / '.config' / 'yotoize'


def find_config_file(config_name: Optional[str] = None) -> Optional[Path]:
    """Find a configuration file in standard locations.
    
    Search order:
    1. Current directory: yotoize.toml, .yotoize.toml
    2. User config directory: config.toml, yotoize.toml
    3. If config_name provided: current directory, then user config directory
    
    Args:
        config_name: Optional specific config filename to search for
    
    Returns:
        Path to config file if found, None otherwise
    """
    # If specific config name provided, search for it
    if config_name:
        # Check current directory first
        current_dir_config = Path(config_name)
        if current_dir_config.exists():
            return current_dir_config
        
        # Check user config directory
        user_config_dir = get_user_config_dir()
        user_config_file = user_config_dir / config_name
        if user_config_file.exists():
            return user_config_file
        
        return None
    
    # Default search order
    search_paths = []
    
    # 1. Current directory
    search_paths.extend([
        Path('yotoize.toml'),
        Path('.yotoize.toml'),
        Path('yotoize.json'),
        Path('.yotoize.json'),
    ])
    
    # 2. User config directory
    user_config_dir = get_user_config_dir()
    search_paths.extend([
        user_config_dir / 'config.toml',
        user_config_dir / 'yotoize.toml',
        user_config_dir / 'config.json',
        user_config_dir / 'yotoize.json',
    ])
    
    # Return first existing file
    for config_path in search_paths:
        if config_path.exists():
            return config_path
    
    return None
