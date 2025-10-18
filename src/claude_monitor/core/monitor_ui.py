#!/usr/bin/env python3
"""
Claude Code Usage Monitor (Interactive Peek Edition)
by Sylvester Assiamah (AssiamahS)
───────────────────────────────────────────────
✅ Adds total tokens spent + estimated cost
✅ Shows last N messages with global numbering and text preview
✅ Press 1 | 2 | 3 to toggle last 3 / 15 / 30 messages
✅ Keeps Rich live dashboard
"""


import os
import sys
import time
import json
import threading
import termios
import tty
import sys as system
from claude_monitor.data.writer import add_keyword_to_existing_entry
from datetime import datetime, timedelta, timezone
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn
from rich.table import Table
from rich.live import Live

sys.path.append(os.path.expanduser("~/code/Claude-Code-Usage-Monitor/src"))
from claude_monitor.data.reader import load_usage_entries

console = Console()

# ─────────────────────────────────────────────
# Global State
# ─────────────────────────────────────────────
last_n_display = 3
stop_listen = False

# ─────────────────────────────────────────────
# Keyboard listener
# ─────────────────────────────────────────────
def listen_for_keys():
    global last_n_display, stop_listen
    fd = system.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        while not stop_listen:
            if system.stdin in select([system.stdin], [], [], 0.1)[0]:
                ch = system.stdin.read(1)
                if ch == "1":
                    last_n_display = 3
                elif ch == "2":
                    last_n_display = 15
                elif ch == "3":
                    last_n_display = 30
                elif ch.lower() == "q":
                    stop_listen = True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def get_recent_entries(hours=24):
    entries, _ = load_usage_entries(include_raw=True)
    cutoff = datetime.now(timezone.utc)
    safe = []
    for e in entries:
        ts = e.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > cutoff - timedelta(hours=hours):
            safe.append(e)
    return safe


def load_recent_raw_prompts(limit=50):
    paths = [
        os.path.expanduser("~/.claude/code/usage/usage.jsonl"),
        os.path.expanduser("~/Library/Application Support/Claude/code/usage/usage.jsonl"),
    ]
    path = next((p for p in paths if os.path.exists(p)), None)
    if not path:
        return []

    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                lines.append(json.loads(line))
            except Exception:
                pass
    lines = lines[-limit:]
    result = []
    for entry in lines:
        ts = entry.get("timestamp")
        text = entry.get("text") or entry.get("prompt") or ""
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            result.append({"timestamp": dt, "text": text})
    return result


# ─────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────
def render_usage_panel():
    entries = get_recent_entries()
    if not entries:
        return Panel("No usage data found yet.",
                     title="📊 Claude Code Usage Monitor",
                     border_style="yellow")

    total_tokens = sum(e.input_tokens + e.output_tokens for e in entries)
    total_messages = len(entries)
    cost_per_token = 0.000002
    total_cost = total_tokens * cost_per_token
    limit = 19000
    remaining = max(0, limit - total_tokens)
    percent = min(total_tokens / limit, 1.0)

    progress = Progress(
        TextColumn("💰 Tokens Used:"),
        BarColumn(bar_width=40),
        TextColumn(f"{total_tokens:,} / {limit:,}"),
        expand=False,
    )
    progress.add_task("", total=1.0, completed=percent)

    tbl = Table.grid(expand=True)
    tbl.add_row(f"🕒 Last {len(entries)} messages", f"💲 Est. Cost: ${total_cost:.2f}")
    tbl.add_row(f"🧮 Tokens Remaining: {remaining:,}", f"📨 Messages: {total_messages}")
    tbl.add_row(
        f"[cyan]🧠 Tokens Spent (Total):[/cyan] {total_tokens:,}",
        f"[magenta]💲 Total Approx. Cost:[/magenta] ${total_cost:.4f}"
    )

    return Panel(Group(progress, tbl),
                 title="✦ ✧ CLAUDE CODE USAGE MONITOR ✦ ✧",
                 border_style="green")


def render_overlay():
    """Recent messages overlay showing prompt snippet."""
    entries, _ = load_usage_entries(include_raw=True)
    if not entries:
        return Panel("⚠️ No token data yet.",
                     title="🧠 Recent Messages",
                     border_style="yellow")

    total_entries = len(entries)
    recent = list(enumerate(entries[-last_n_display:], start=total_entries - last_n_display + 1))
    raw_prompts = load_recent_raw_prompts(limit=last_n_display * 2)

    msgs = []
    for idx, e in recent:
        tokens_used = e.input_tokens + e.output_tokens
        t = e.timestamp.strftime("%H:%M:%S")
        model = e.model
        keyword = getattr(e, "keyword", None)
        keyword_text = f" - {keyword}" if keyword else ""

        # find snippet
        raw_text = getattr(e, "text", "") or getattr(e, "raw", "")
        if not raw_text and raw_prompts:
            closest = min(
                raw_prompts,
                key=lambda x: abs((x["timestamp"] - e.timestamp).total_seconds()),
                default=None,
            )
            if closest:
                raw_text = closest["text"]

        preview = ""
        if raw_text:
            words = raw_text.strip().split()
            snippet = " ".join(words[:8])
            preview = f' [dim italic]– “{snippet}{"..." if len(words) > 8 else ""}”[/dim italic]'

        msgs.append(f"{idx}. [bold]{t}[/bold] | {tokens_used} tokens ({model}){keyword_text}{preview}")

    total = sum(e.input_tokens + e.output_tokens for _, e in recent)
    avg = total / len(recent)
    lines = "\n".join(msgs + [
        "─" * 45,
        f"Total: {total} | Avg/msg: {avg:.1f}",
        f"[dim]Press 1=3 msgs • 2=15 msgs • 3=30 msgs • Q=Quit[/dim]"
    ])
    return Panel(lines, title=f"🧠 Recent Message Tokens (last {last_n_display})", border_style="cyan")


def render_combined_layout():
    return Group(render_usage_panel(), render_overlay())


# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────
from select import select

def main():
    global stop_listen
    console.clear()
    console.print("[dim]Claude Code Usage Monitor running — press 1 | 2 | 3 to change view, Q to quit[/dim]\n")

    listener = threading.Thread(target=listen_for_keys, daemon=True)
    listener.start()

    with Live(render_combined_layout(), refresh_per_second=2, console=console) as live:
        try:
            while not stop_listen:
                live.update(render_combined_layout())
                time.sleep(2)
        except KeyboardInterrupt:
            stop_listen = True
        finally:
            console.clear()
            console.print("[red]👋 Exiting Claude Code Usage Monitor.[/red]")


if __name__ == "__main__":
    main()
