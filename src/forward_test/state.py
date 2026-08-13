"""
Paper Forward Trading State Persistence Module.
Provides atomic JSON state reads/writes for fault-tolerant crash recovery.
Supports RESET_FORWARD_STATE and RESUME_FORWARD_STATE.
Tracks Raspberry Pi 7-day experiment timer, restart count, and recovery metadata.
"""

import os
import json
import tempfile
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from common.utils import setup_logger

logger = setup_logger("ForwardState")


class ForwardStateStore:
    """Atomic state persistence manager for Paper Forward Trading."""

    def __init__(self, state_file: str = "logs/forward_state.json"):
        self.state_file = state_file
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)

    def load_state(self, reset: bool = False) -> Optional[Dict[str, Any]]:
        if reset:
            logger.info("RESET_FORWARD_STATE=True: Wiping previous forward state.")
            self.clear_state()
            return None

        if not os.path.exists(self.state_file):
            logger.info("No existing forward state file found. Starting fresh experiment.")
            return None

        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
            logger.info(f"Successfully loaded forward state from: {self.state_file}")
            return data
        except Exception as e:
            logger.error(f"Failed to read forward state: {e}. Starting fresh.")
            return None

    def save_state_atomic(self, state_data: Dict[str, Any]) -> None:
        """Write state to a temporary file and atomically replace the target file."""
        dir_name = os.path.dirname(self.state_file)
        try:
            with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False) as tf:
                json.dump(state_data, tf, indent=2)
                temp_name = tf.name
            os.replace(temp_name, self.state_file)
        except Exception as e:
            logger.error(f"Error performing atomic state write: {e}")

    def clear_state(self) -> None:
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
