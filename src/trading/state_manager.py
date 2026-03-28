"""Minimal state persistence — only stores what cannot be reconstructed from data files."""

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class StateManager:
    """Saves and loads daemon state to a YAML file.

    The state file contains:
    - pending_order: in-flight fulfillment details (or null)
    - strategy_state: serialized strategy mutable state (from export_state)
    - last_updated: timestamp of last save

    Portfolio is reconstructed from the trade log on startup.
    Indicators are rebuilt by prepare() on startup.
    """

    def __init__(self, state_file: str):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        """Load state from YAML file.

        Returns a dict with keys: pending_order, strategy_state, broker_state.
        Supports both v2 (pending_order) and v3 (broker_state) formats.
        If the file is missing or corrupt, returns empty state with a warning.
        """
        if not self.state_file.exists():
            logger.info("No state file found at %s — starting fresh.", self.state_file)
            return {"pending_order": None, "strategy_state": None, "broker_state": None}

        try:
            with open(self.state_file) as f:
                data = yaml.safe_load(f)

            if data is None or not isinstance(data, dict):
                logger.warning("State file %s is empty or invalid — starting fresh.", self.state_file)
                return {"pending_order": None, "strategy_state": None, "broker_state": None}

            pending = data.get("pending_order")
            strategy_state = data.get("strategy_state")
            broker_state = data.get("broker_state")
            version = data.get("version", 2)
            logger.info(
                "Loaded state v%d from %s (last_updated: %s, pending_order: %s, "
                "broker_state: %s, strategy_state: %s).",
                version,
                self.state_file,
                data.get("last_updated", "unknown"),
                pending.get("action") if pending else "none",
                "present" if broker_state else "none",
                "present" if strategy_state else "none",
            )
            return {
                "pending_order": pending,
                "strategy_state": strategy_state,
                "broker_state": broker_state,
            }

        except (yaml.YAMLError, OSError) as e:
            logger.warning("Failed to read state file %s (%s) — starting fresh.", self.state_file, e)
            return {"pending_order": None, "strategy_state": None, "broker_state": None}

    def save(self, pending_order: dict | None = None,
             strategy_state: dict | None = None,
             broker_state: dict | None = None):
        """Save state to YAML file using atomic write (temp file + rename).

        Args:
            pending_order: Fulfillment order details (v2 format), or None.
            strategy_state: Serialized strategy state from export_state(), or None.
            broker_state: Serialized broker state from broker.export_state(), or None.
        """
        # Use v3 format if broker_state is provided, otherwise v2
        version = 3 if broker_state is not None else 2
        state = {
            "version": version,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "strategy_state": strategy_state,
        }
        if broker_state is not None:
            state["broker_state"] = broker_state
        if pending_order is not None:
            state["pending_order"] = pending_order

        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=self.state_file.parent,
                prefix=".state_",
                suffix=".yaml.tmp",
            )
            try:
                with os.fdopen(fd, "w") as f:
                    yaml.dump(state, f, default_flow_style=False, sort_keys=False)
                os.replace(tmp_path, self.state_file)
            except Exception:
                os.unlink(tmp_path)
                raise
        except OSError as e:
            logger.error("Failed to save state to %s: %s", self.state_file, e)
            raise

    def clear_pending_order(self):
        """Convenience: save state with no pending order."""
        self.save(pending_order=None)
