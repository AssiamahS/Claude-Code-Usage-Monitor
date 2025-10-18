#!/usr/bin/env python3
"""
Claude Monitor Data Writer
───────────────────────────────────────────────
Handles usage logging and keyword tagging for the Claude Code Usage Monitor.

Features:
✅ Logs model usage entries with metadata
✅ Adds or updates keywords for existing messages
✅ Compatible with both ~/.claude/projects and ~/.claude/code paths
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default log file search paths (works for both Claude Desktop and Claude Code)
USAGE_PATHS = [
    os.path.expanduser("~/.claude/projects/usage.jsonl"),
    os.path.expanduser("~/.claude/code/usage/usage.jsonl"),
    os.path.expanduser("~/Library/Application Support/Claude/code/usage/usage.jsonl"),
]


# ─────────────────────────────────────────────
# Log a new usage entry
# ─────────────────────────────────────────────
def log_usage_entry(
    timestamp: datetime,
    model: str,
    input_tokens: int,
    output_tokens: int,
    keyword: Optional[str] = None,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    cost_usd: float = 0.0,
    message_id: str = "",
    request_id: str = "",
    log_path: Optional[str] = None,
) -> bool:
    """
    Log a usage entry with optional keyword summary.

    Example:
        log_usage_entry(
            timestamp=datetime.now(),
            model="claude-sonnet-4-5",
            input_tokens=300,
            output_tokens=150,
            keyword="site fix",
        )
    """
    if log_path is None:
        # Prefer ~/.claude/projects, fallback to ~/.claude/code
        log_path = next((Path(p) for p in USAGE_PATHS if os.path.exists(os.path.dirname(p))), Path(USAGE_PATHS[0]))

    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": timestamp.isoformat(),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cost_usd": cost_usd,
        "message_id": message_id,
        "request_id": request_id,
    }

    if keyword:
        entry["keyword"] = keyword

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info("✅ Logged usage entry (%s) with keyword: %s", message_id, keyword or "(none)")
        return True
    except Exception as e:
        logger.error("❌ Failed to write usage entry: %s", e)
        return False


# ─────────────────────────────────────────────
# Add or update keyword on existing entries
# ─────────────────────────────────────────────
def add_keyword_to_existing_entry(message_id: str, keyword: str) -> bool:
    """
    Add or update a keyword for a specific message in any valid usage.jsonl file.
    Example:
        add_keyword_to_existing_entry("msg_12345", "login error")
    """
    # Find whichever usage log actually exists
    path = next((p for p in USAGE_PATHS if os.path.exists(p)), None)
    if not path:
        print("❌ No usage log file found in known paths.")
        return False

    lines = []
    found = False

    # Read all entries
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    entry_msg_id = entry.get("message_id") or (
                        entry.get("message", {}).get("id") if isinstance(entry.get("message"), dict) else None
                    )
                    if entry_msg_id == message_id:
                        entry["keyword"] = keyword
                        found = True
                    lines.append(entry)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        print("❌ Log file not found.")
        return False

    if not found:
        print(f"⚠️ Message ID {message_id} not found in log.")
        return False

    # Rewrite file safely
    try:
        with open(path, "w", encoding="utf-8") as f:
            for e in lines:
                f.write(json.dumps(e) + "\n")
        print(f"✅ Added keyword '{keyword}' to message {message_id}")
        return True
    except Exception as e:
        print(f"❌ Failed to update log: {e}")
        return False
