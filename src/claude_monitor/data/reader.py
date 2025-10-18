#!/usr/bin/env python3
"""
Simplified data reader for Claude Monitor.
───────────────────────────────────────────────
✅ Combines file loading, mapping, and filtering
✅ Adds full keyword propagation to UsageEntry
✅ Works seamlessly with monitor overlay
"""

import json
import logging
from datetime import datetime, timedelta, timezone as tz
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from claude_monitor.core.data_processors import (
    DataConverter,
    TimestampProcessor,
    TokenExtractor,
)
from claude_monitor.core.models import CostMode, UsageEntry
from claude_monitor.core.pricing import PricingCalculator
from claude_monitor.error_handling import report_file_error
from claude_monitor.utils.time_utils import TimezoneHandler

FIELD_COST_USD = "cost_usd"
FIELD_MODEL = "model"
TOKEN_INPUT = "input_tokens"
TOKEN_OUTPUT = "output_tokens"

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Public: load usage entries
# ─────────────────────────────────────────────
def load_usage_entries(
    data_path: Optional[str] = None,
    hours_back: Optional[int] = None,
    mode: CostMode = CostMode.AUTO,
    include_raw: bool = False,
) -> Tuple[List[UsageEntry], Optional[List[Dict[str, Any]]]]:
    """Load and convert JSONL files to UsageEntry objects."""
    data_path = Path(data_path or "~/.claude/projects").expanduser()
    timezone_handler = TimezoneHandler()
    pricing_calculator = PricingCalculator()

    cutoff_time = None
    if hours_back:
        cutoff_time = datetime.now(tz.utc) - timedelta(hours=hours_back)

    jsonl_files = _find_jsonl_files(data_path)
    if not jsonl_files:
        logger.warning("No JSONL files found in %s", data_path)
        return [], None

    all_entries: List[UsageEntry] = []
    raw_entries: Optional[List[Dict[str, Any]]] = [] if include_raw else None
    processed_hashes: Set[str] = set()

    for file_path in jsonl_files:
        entries, raw_data = _process_single_file(
            file_path,
            mode,
            cutoff_time,
            processed_hashes,
            include_raw,
            timezone_handler,
            pricing_calculator,
        )
        all_entries.extend(entries)
        if include_raw and raw_data:
            raw_entries.extend(raw_data)

    all_entries.sort(key=lambda e: e.timestamp)
    logger.info("Processed %d entries from %d files", len(all_entries), len(jsonl_files))
    return all_entries, raw_entries


# ─────────────────────────────────────────────
# Public: load raw entries (for debugging)
# ─────────────────────────────────────────────
def load_all_raw_entries(data_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load all raw JSONL entries without processing."""
    data_path = Path(data_path or "~/.claude/projects").expanduser()
    jsonl_files = _find_jsonl_files(data_path)
    all_raw_entries: List[Dict[str, Any]] = []

    for file_path in jsonl_files:
        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        all_raw_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.exception("Error loading raw entries from %s: %s", file_path, e)
    return all_raw_entries


# ─────────────────────────────────────────────
# Internal: file handling and filtering
# ─────────────────────────────────────────────
def _find_jsonl_files(data_path: Path) -> List[Path]:
    """Find all .jsonl files in the data directory."""
    if not data_path.exists():
        logger.warning("Data path does not exist: %s", data_path)
        return []
    return list(data_path.rglob("*.jsonl"))


def _process_single_file(
    file_path: Path,
    mode: CostMode,
    cutoff_time: Optional[datetime],
    processed_hashes: Set[str],
    include_raw: bool,
    timezone_handler: TimezoneHandler,
    pricing_calculator: PricingCalculator,
) -> Tuple[List[UsageEntry], Optional[List[Dict[str, Any]]]]:
    """Process a single JSONL file into UsageEntry objects."""
    entries: List[UsageEntry] = []
    raw_data: Optional[List[Dict[str, Any]]] = [] if include_raw else None

    try:
        # First pass: build a map of user messages by UUID
        user_messages = {}
        all_lines = []

        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    all_lines.append(data)

                    # Store user messages for later lookup
                    if data.get("type") == "user":
                        uuid = data.get("uuid")
                        message = data.get("message", {})
                        content = message.get("content", "")

                        # Extract text from user message
                        text = ""
                        if isinstance(content, str):
                            text = content
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text = block.get("text", "")
                                    break

                        if uuid and text:
                            user_messages[uuid] = text

                except json.JSONDecodeError:
                    continue

        # Second pass: process assistant messages and link to user messages
        for data in all_lines:
            try:
                if not _should_process_entry(data, cutoff_time, processed_hashes, timezone_handler):
                    continue

                # Add user text to assistant message if available
                if data.get("type") == "assistant":
                    parent_uuid = data.get("parentUuid")
                    if parent_uuid and parent_uuid in user_messages:
                        data["_user_text"] = user_messages[parent_uuid]

                entry = _map_to_usage_entry(data, mode, timezone_handler, pricing_calculator)
                if entry:
                    entries.append(entry)
                    _update_processed_hashes(data, processed_hashes)
                if include_raw:
                    raw_data.append(data)
            except json.JSONDecodeError:
                continue

    except Exception as e:
        logger.warning("Failed to read file %s: %s", file_path, e)
        report_file_error(
            exception=e,
            file_path=str(file_path),
            operation="read",
            additional_context={"file_exists": file_path.exists()},
        )
        return [], None

    return entries, raw_data


def _should_process_entry(
    data: Dict[str, Any],
    cutoff_time: Optional[datetime],
    processed_hashes: Set[str],
    timezone_handler: TimezoneHandler,
) -> bool:
    """Check if entry should be processed based on time and uniqueness."""
    if cutoff_time:
        timestamp_str = data.get("timestamp")
        if timestamp_str:
            processor = TimestampProcessor(timezone_handler)
            timestamp = processor.parse_timestamp(timestamp_str)
            if timestamp and timestamp < cutoff_time:
                return False
    unique_hash = _create_unique_hash(data)
    return not (unique_hash and unique_hash in processed_hashes)


def _create_unique_hash(data: Dict[str, Any]) -> Optional[str]:
    """Create unique hash for deduplication."""
    message_id = data.get("message_id") or (
        data.get("message", {}).get("id") if isinstance(data.get("message"), dict) else None
    )
    request_id = data.get("requestId") or data.get("request_id")
    return f"{message_id}:{request_id}" if message_id and request_id else None


def _update_processed_hashes(data: Dict[str, Any], processed_hashes: Set[str]) -> None:
    """Update the processed hashes set with current entry's hash."""
    unique_hash = _create_unique_hash(data)
    if unique_hash:
        processed_hashes.add(unique_hash)


# ─────────────────────────────────────────────
# Mapping: raw JSON to UsageEntry
# ─────────────────────────────────────────────
def _map_to_usage_entry(
    data: Dict[str, Any],
    mode: CostMode,
    timezone_handler: TimezoneHandler,
    pricing_calculator: PricingCalculator,
) -> Optional[UsageEntry]:
    """Map raw data to UsageEntry with full keyword and text support."""
    try:
        timestamp_processor = TimestampProcessor(timezone_handler)
        timestamp = timestamp_processor.parse_timestamp(data.get("timestamp", ""))
        if not timestamp:
            return None

        token_data = TokenExtractor.extract_tokens(data)
        if not any(v for k, v in token_data.items() if k != "total_tokens"):
            return None

        model = DataConverter.extract_model_name(data, default="unknown")
        entry_data: Dict[str, Any] = {
            FIELD_MODEL: model,
            TOKEN_INPUT: token_data["input_tokens"],
            TOKEN_OUTPUT: token_data["output_tokens"],
            "cache_creation_tokens": token_data.get("cache_creation_tokens", 0),
            "cache_read_tokens": token_data.get("cache_read_tokens", 0),
            FIELD_COST_USD: data.get("cost") or data.get(FIELD_COST_USD),
        }

        cost_usd = pricing_calculator.calculate_cost_for_entry(entry_data, mode)

        message = data.get("message", {})
        message_id = data.get("message_id") or message.get("id") or ""
        request_id = data.get("request_id") or data.get("requestId") or "unknown"

        # Extract keyword and text from message content
        keyword = data.get("keyword")
        text = data.get("_user_text", "")  # Use linked user text if available

        # If no user text, try extracting from message content directly
        if not text:
            content = message.get("content", "")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                # Get first text content block
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        break

        # Generate keyword from text if not already set (first 3-5 words)
        if not keyword and text:
            words = text.strip().split()
            keyword = " ".join(words[:5]) if len(words) <= 5 else " ".join(words[:3])
            if len(keyword) > 40:
                keyword = keyword[:37] + "..."

        entry = UsageEntry(
            timestamp=timestamp,
            input_tokens=token_data["input_tokens"],
            output_tokens=token_data["output_tokens"],
            cache_creation_tokens=token_data.get("cache_creation_tokens", 0),
            cache_read_tokens=token_data.get("cache_read_tokens", 0),
            cost_usd=cost_usd,
            model=model,
            message_id=message_id,
            request_id=request_id,
            keyword=keyword,
        )

        # Attach text field if supported by your UsageEntry model
        if hasattr(entry, "__dict__"):
            entry.text = text

        return entry

    except (KeyError, ValueError, TypeError, AttributeError) as e:
        logger.debug("Failed to map entry: %s: %s", type(e).__name__, e)
        return None


# ─────────────────────────────────────────────
# Compatibility wrapper for legacy interfaces
# ─────────────────────────────────────────────
class UsageEntryMapper:
    """Compatibility wrapper for legacy UsageEntryMapper interface."""

    def __init__(self, pricing_calculator: PricingCalculator, timezone_handler: TimezoneHandler):
        self.pricing_calculator = pricing_calculator
        self.timezone_handler = timezone_handler

    def map(self, data: Dict[str, Any], mode: CostMode) -> Optional[UsageEntry]:
        return _map_to_usage_entry(data, mode, self.timezone_handler, self.pricing_calculator)

    def _has_valid_tokens(self, tokens: Dict[str, int]) -> bool:
        return any(v > 0 for v in tokens.values())

    def _extract_timestamp(self, data: Dict[str, Any]) -> Optional[datetime]:
        if "timestamp" not in data:
            return None
        processor = TimestampProcessor(self.timezone_handler)
        return processor.parse_timestamp(data["timestamp"])

    def _extract_model(self, data: Dict[str, Any]) -> str:
        return DataConverter.extract_model_name(data, default="unknown")

    def _extract_metadata(self, data: Dict[str, Any]) -> Dict[str, str]:
        message = data.get("message", {})
        return {
            "message_id": data.get("message_id") or message.get("id", ""),
            "request_id": data.get("request_id") or data.get("requestId", "unknown"),
        }
