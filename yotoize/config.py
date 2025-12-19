"""Configuration file support for yotoize."""

import json
from pathlib import Path
from typing import Dict, Any, Optional

# Try tomllib first (Python 3.11+), then fall back to tomli
try:
    import tomllib
    HAS_TOMLLIB = True
except ImportError:
    HAS_TOMLLIB = False
    try:
        import tomli as tomllib
        HAS_TOMLLIB = True
    except ImportError:
        HAS_TOMLLIB = False

try:
    import tomli_w
    HAS_TOMLI_W = True
except ImportError:
    HAS_TOMLI_W = False


class Config:
    """Configuration manager for yotoize."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize config manager.
        
        Args:
            config_path: Optional path to config file
        """
        self.config_path = config_path
        self.data: Dict[str, Any] = {}
        
        if config_path and config_path.exists():
            self.load(config_path)
    
    def load(self, config_path: Path) -> None:
        """Load configuration from file.
        
        Args:
            config_path: Path to config file (TOML or JSON)
        """
        if config_path.suffix.lower() == '.toml':
            self._load_toml(config_path)
        elif config_path.suffix.lower() == '.json':
            self._load_json(config_path)
        else:
            raise ValueError(f"Unsupported config format: {config_path.suffix}")
    
    def _load_toml(self, config_path: Path) -> None:
        """Load TOML configuration file."""
        if not HAS_TOMLLIB:
            raise ImportError("tomllib (Python 3.11+) or tomli is required for TOML config files. Install with: pip install tomli")
        
        with open(config_path, 'rb') as f:
            self.data = tomllib.load(f)
    
    def _load_json(self, config_path: Path) -> None:
        """Load JSON configuration file."""
        with open(config_path, 'r') as f:
            self.data = json.load(f)
    
    def save(self, config_path: Path, format: str = 'toml') -> None:
        """Save configuration to file.
        
        Args:
            config_path: Path to save config file
            format: Format to save as ('toml' or 'json')
        """
        if format == 'toml':
            self._save_toml(config_path)
        elif format == 'json':
            self._save_json(config_path)
        else:
            raise ValueError(f"Unsupported config format: {format}")
    
    def _save_toml(self, config_path: Path) -> None:
        """Save TOML configuration file."""
        if not HAS_TOMLI_W:
            raise ImportError("tomli-w is required for saving TOML config files. Install with: pip install tomli-w")
        
        with open(config_path, 'wb') as f:
            tomli_w.dump(self.data, f)
    
    def _save_json(self, config_path: Path) -> None:
        """Save JSON configuration file."""
        with open(config_path, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value.
        
        Args:
            key: Configuration key (supports dot notation, e.g., 'split.format')
            default: Default value if key not found
        
        Returns:
            Configuration value or default
        """
        keys = key.split('.')
        value = self.data
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any) -> None:
        """Set configuration value.
        
        Args:
            key: Configuration key (supports dot notation)
            value: Value to set
        """
        keys = key.split('.')
        data = self.data
        
        for k in keys[:-1]:
            if k not in data:
                data[k] = {}
            data = data[k]
        
        data[keys[-1]] = value
    
    def merge(self, other: Dict[str, Any]) -> None:
        """Merge another dictionary into this config.
        
        Args:
            other: Dictionary to merge
        """
        self._merge_dict(self.data, other)
    
    def _merge_dict(self, base: Dict[str, Any], update: Dict[str, Any]) -> None:
        """Recursively merge dictionaries."""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_dict(base[key], value)
            else:
                base[key] = value
