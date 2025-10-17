#!/usr/bin/env python3
"""Interactive terminal UI for displaying Claude token usage"""

import os
import sys
import time
import json
import select
import termios
import tty
from datetime import datetime
from pathlib import Path


class ClaudeTokenUI:
    def __init__(self):
        self.history_file = Path.home() / ".claude_message_history.json"
        self.messages = self.load_history()
        self.visible = True
        self.mode = "1"  # default view: last message

    def load_history(self):
        if self.history_file.exists():
            with open(self.history_file, "r") as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return []
        return []

    def get_last_messages(self, n=3):
        return self.messages[-n:] if self.messages else []

    def show(self):
        os.system("clear")

        if not self.visible:
            print("🧠 Token monitor hidden. Press (h) to show again.")
            print("(q) quit")
            return

        msgs = self.get_last_messages(10)
        if not msgs:
            print("No token data found yet.")
            print("(h) hide | (q) quit")
            return

        if self.mode == "1":
            msg = msgs[-1]
            print(f"🧠 Used {msg['tokens_used']:,} tokens in last message.")
            print(f"   Total tokens after: {msg['tokens_after']:,}")

        elif self.mode == "2":
            for m in msgs[-3:]:
                print(f"🧠 Used {m['tokens_used']:,} tokens in last message.")
                print(f"   Total tokens after: {m['tokens_after']:,}\n")
            self._summary(msgs[-3:])

        elif self.mode == "3":
            self._summary(self.messages)

        print("\nPress (1) last msg | (2) last 3 | (3) avg | (h) hide/show | (q) quit")

    def _summary(self, messages):
        total = sum(m["tokens_used"] for m in messages)
        avg = total / len(messages) if messages else 0

        print("=" * 60)
        print(f"Recent Message Token Usage (Last {len(messages)} messages)")
        print("=" * 60 + "\n")

        for i, msg in enumerate(messages, 1):
            t = datetime.fromisoformat(msg["timestamp"]).strftime("%H:%M:%S")
            print(f"Message {i} [{t}] - {msg['tokens_used']:,} tokens")
            if msg.get("preview"):
                print(f"  Preview: {msg['preview']}...")
            print()

        print("-" * 60)
        print(f"Total tokens across last {len(messages)} messages: {total:,}")
        print(f"Average per message: {avg:.1f}")
        print("=" * 60)

    def key_listener(self):
        """Listen for user key presses and refresh UI"""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while True:
                if sys.stdin in select.select([sys.stdin], [], [], 0.2)[0]:
                    key = sys.stdin.read(1)
                    if key == "q":
                        os.system("clear")
                        print("👋 Exiting Claude token monitor.")
                        break
                    elif key == "1":
                        self.mode = "1"
                    elif key == "2":
                        self.mode = "2"
                    elif key == "3":
                        self.mode = "3"
                    elif key == "h":
                        self.visible = not self.visible
                    # reload history every time we show
                    self.messages = self.load_history()
                    self.show()
                else:
                    time.sleep(0.05)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


if __name__ == "__main__":
    ui = ClaudeTokenUI()
    ui.show()
    ui.key_listener()
