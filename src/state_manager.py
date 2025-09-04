"""
Unified state manager for coordinating TOML file access between multiple modules.
Prevents race conditions and data loss when multiple components write to the same file.
"""
import asyncio
import os
import time
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

import aiofiles
import toml

from log import log


class StateManager:
    """
    Unified state manager that coordinates access to TOML state files.
    Ensures atomic writes and prevents race conditions between modules.
    """
    
    def __init__(self, state_file_path: str):
        self.state_file_path = state_file_path
        self._lock = asyncio.Lock()
        self._cached_state: Optional[Dict[str, Any]] = None
        self._last_load_time = 0
        self._cache_ttl = 5.0  # Cache for 5 seconds
        
    async def _load_state(self) -> Dict[str, Any]:
        """Load state from file with caching."""
        current_time = time.time()
        
        # Use cached state if still valid
        if (self._cached_state is not None and 
            current_time - self._last_load_time < self._cache_ttl):
            return self._cached_state.copy()
        
        # Load from file
        try:
            if os.path.exists(self.state_file_path):
                async with aiofiles.open(self.state_file_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                state = toml.loads(content)
            else:
                state = {}
            
            self._cached_state = state
            self._last_load_time = current_time
            return state.copy()
            
        except Exception as e:
            log.error(f"Failed to load state from {self.state_file_path}: {e}")
            return {}
    
    async def _save_state(self, state: Dict[str, Any]):
        """Save state to file."""
        try:
            os.makedirs(os.path.dirname(self.state_file_path), exist_ok=True)
            async with aiofiles.open(self.state_file_path, "w", encoding="utf-8") as f:
                await f.write(toml.dumps(state))
            
            # Update cache
            self._cached_state = state.copy()
            self._last_load_time = time.time()
            
        except Exception as e:
            log.error(f"Failed to save state to {self.state_file_path}: {e}")
            raise
    
    @asynccontextmanager
    async def transaction(self):
        """
        Context manager for atomic state transactions.
        Usage:
            async with state_manager.transaction() as state:
                state['key'] = 'value'
                # State is automatically saved on exit
        """
        async with self._lock:
            state = await self._load_state()
            try:
                yield state
                await self._save_state(state)
            except Exception:
                # Don't save if there was an error
                raise
    
    async def read_file_state(self, filename: str) -> Dict[str, Any]:
        """Read state for a specific file."""
        async with self._lock:
            state = await self._load_state()
            return state.get(filename, {}).copy()
    
    async def update_file_state(self, filename: str, updates: Dict[str, Any]):
        """Update state for a specific file."""
        async with self.transaction() as state:
            if filename not in state:
                state[filename] = {}
            state[filename].update(updates)
    
    async def batch_update(self, updates: Dict[str, Dict[str, Any]]):
        """Batch update multiple files at once."""
        async with self.transaction() as state:
            for filename, file_updates in updates.items():
                if filename not in state:
                    state[filename] = {}
                state[filename].update(file_updates)


# Global state manager instances
_state_managers: Dict[str, StateManager] = {}

def get_state_manager(state_file_path: str) -> StateManager:
    """Get or create a state manager for the given file path."""
    if state_file_path not in _state_managers:
        _state_managers[state_file_path] = StateManager(state_file_path)
    return _state_managers[state_file_path]