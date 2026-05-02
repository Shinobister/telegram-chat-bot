#!/usr/bin/env python3
"""
bishbot — Universal Telegram bot with LLM integration.

A modular Telegram bot that listens for incoming messages, forwards them to an
LLM (DeepSeek via OpenAI-compatible API), and sends back responses. Also supports
proactive outgoing messages triggered by a control flag in the companion guide file.

Usage:
    python tg_bot.py

Requires:
    - tg_config.json (Telegram token + chat_id)
    - .env file with DEEPSEEK_API_KEY
    - tg_guide.md (created automatically if missing)

Architecture (single file, modular classes):
    Config        — loads credentials and project settings
    LLMClient     — communicates with DeepSeek/OpenAI-compatible API
    GuideManager  — reads/writes the guide file (shared state + history)
    BotHandlers   — Telegram event handlers and periodic guide watcher
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import openai
from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "tg_config.json"
GUIDE_PATH = BASE_DIR / "tg_guide.md"
ENV_PATH = BASE_DIR / ".env"

DEFAULT_PROJECT = "bishbot"
POLL_INTERVAL = 5  # seconds between guide file checks

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
)
log = logging.getLogger("bishbot")


# ═══════════════════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════════════════

class Config:
    """Loads and caches all configuration from tg_config.json, .env, and the guide file."""

    def __init__(self):
        self._tg = None   # telegram config cache
        self._env = None  # env vars cache
        self._project = None
        self._guide_dirty = False  # track if we need to re-read project name

    # ── Telegram ──────────────────────────────────────────────────────────

    @property
    def telegram_token(self) -> str:
        if self._tg is None:
            self._load_tg_config()
        return self._tg["token"]

    @property
    def chat_id(self) -> int:
        if self._tg is None:
            self._load_tg_config()
        return int(self._tg["chat_id"])

    def _load_tg_config(self):
        if not CONFIG_PATH.exists():
            log.error("Telegram config not found at %s", CONFIG_PATH)
            raise FileNotFoundError(
                f"Create {CONFIG_PATH} with: {{\"token\": \"...\", \"chat_id\": \"...\"}}"
            )
        with open(CONFIG_PATH) as f:
            self._tg = json.load(f)

    # ── LLM (DeepSeek) ────────────────────────────────────────────────────

    @property
    def deepseek_api_key(self) -> str:
        return self._get_env("DEEPSEEK_API_KEY", "")

    @property
    def deepseek_base_url(self) -> str:
        return self._get_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    @property
    def deepseek_model(self) -> str:
        return self._get_env("DEEPSEEK_MODEL", "deepseek-chat")

    def _get_env(self, key: str, default: str = "") -> str:
        if self._env is None:
            self._load_env()
        return self._env.get(key, default)

    def _load_env(self):
        self._env = {}
        # .env file (lowest priority)
        if ENV_PATH.exists():
            with open(ENV_PATH) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    self._env[k.strip()] = v.strip().strip("\"'")
        # process environment overrides
        for k in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
            if k in os.environ:
                self._env[k] = os.environ[k]

    # ── Project name (from guide file) ─────────────────────────────────────

    @property
    def project_name(self) -> str:
        if self._project is None and GUIDE_PATH.exists():
            self._project = self._read_project_name()
        return self._project or DEFAULT_PROJECT

    @project_name.setter
    def project_name(self, value: str):
        self._project = value

    def _read_project_name(self) -> Optional[str]:
        try:
            content = GUIDE_PATH.read_text(encoding="utf-8")
            m = re.search(r"## Active Project\n`(.+?)`", content)
            return m.group(1).strip() if m else None
        except Exception:
            return None

    @property
    def kill_keyword(self) -> str:
        return f"{self.project_name} /kill"

    # ── Validation ────────────────────────────────────────────────────────

    def validate(self):
        """Ensure all required config is present; exit with error if not."""
        _ = self.telegram_token  # raises FileNotFoundError if missing
        if not self.deepseek_api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY not found.\n"
                f"Create {ENV_PATH} with:\n"
                "DEEPSEEK_API_KEY=sk-your-key-here"
            )


# ═══════════════════════════════════════════════════════════════════════════════
#  LLMClient
# ═══════════════════════════════════════════════════════════════════════════════

class LLMClient:
    """Wraps the DeepSeek (OpenAI-compatible) API for chat and compression."""

    def __init__(self, config: Config):
        self.client = AsyncOpenAI(
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url,
        )
        self.model = config.deepseek_model
        self.project = config.project_name

    # ── System prompt ─────────────────────────────────────────────────────

    def build_system_prompt(self) -> str:
        """Static system prompt for DeepSeek cache efficiency."""
        return (
            f"You are bishbot, a helpful Telegram assistant for project \"{self.project}\".\n\n"
            "Rules:\n"
            f"- User messages are prefixed with \"{self.project} \" — ignore this prefix.\n"
            "- Keep responses concise and conversational.\n"
            "- If the user says they are done or says goodbye, respond warmly.\n"
            f"- Send \"{self.project} /kill\" as a message to stop the bot.\n"
        )

    # ── Response generation ───────────────────────────────────────────────

    async def generate_response(
        self, messages: list[dict], timeout: int = 60
    ) -> str:
        """Send conversation history to the LLM and return the response text."""
        payload = [
            {"role": "system", "content": self.build_system_prompt()},
            *messages,
        ]
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=payload,
                timeout=timeout,
                max_tokens=2000,
            )
            return resp.choices[0].message.content.strip()
        except openai.AuthenticationError:
            log.error("LLM auth failed — check DEEPSEEK_API_KEY")
            return "Error: API authentication failed. Check the API key."
        except openai.RateLimitError:
            log.warning("LLM rate limited, retrying once after delay")
            try:
                await asyncio.sleep(5)
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=payload,
                    timeout=timeout,
                    max_tokens=2000,
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                log.error("Retry failed: %s", e)
                return "Error: API rate limited. Please try again later."
        except Exception as e:
            log.error("LLM API error: %s", e)
            return f"Error: Unable to reach AI service. {e}"

    # ── History compression ───────────────────────────────────────────────

    async def compress(self, text: str) -> str:
        """Compress conversation history into a 3-5 sentence summary."""
        prompt = (
            "Compress the following conversation into a concise 3-5 sentence summary.\n"
            "Keep key decisions, configuration values, action items, and important context.\n"
            "Omit greetings, small talk, and redundant filler.\n"
            "Output ONLY the summary paragraph — no labels, no introductions.\n\n"
            f"History:\n{text}"
        )
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                timeout=60,
                max_tokens=1000,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            log.error("Compression API error: %s", e)
            # Fallback: truncate
            return text[:600] + "\n... [truncated, compression unavailable]"


# ═══════════════════════════════════════════════════════════════════════════════
#  GuideManager
# ═══════════════════════════════════════════════════════════════════════════════

class GuideManager:
    """
    Reads and writes the shared guide file (tg_guide.md).

    The guide file serves two purposes:
    1. Control plane — Claude Code sets flags (bishbot, close) that the bot reads.
    2. History store — conversation history with compressed summaries.
    """

    def __init__(self):
        self._path = GUIDE_PATH
        self._ensure_exists()

    # ── File I/O ──────────────────────────────────────────────────────────

    def _ensure_exists(self):
        """Create a default guide file if one does not exist."""
        if self._path.exists():
            return
        content = _DEFAULT_GUIDE_TEMPLATE
        self._write(content)
        log.info("Created default guide file at %s", self._path)

    def _read(self) -> str:
        return self._path.read_text(encoding="utf-8")

    def _write(self, content: str):
        """Atomic write via temp file + replace to prevent corruption."""
        tmp = self._path.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, self._path)

    # ── Context for LLM ───────────────────────────────────────────────────

    def get_context(self) -> list[dict]:
        """
        Return messages for LLM context: optional summary + recent messages.

        Returns a list of dicts with 'role' and 'content' keys, ready to pass
        to LLMClient.generate_response().
        """
        content = self._read()
        msgs: list[dict] = []

        # Extract compressed summary
        m = re.search(
            r"### Compressed Summary\n(.*?)(?=\n### |\n## |\Z)", content, re.DOTALL
        )
        if m and not m.group(1).strip().startswith("*"):
            summary = m.group(1).strip()
            if summary:
                msgs.append({"role": "system", "content": f"[Previous context] {summary}"})

        # Extract recent messages
        m = re.search(
            r"### Recent Messages\n(.*?)(?=\n## |\Z)", content, re.DOTALL
        )
        if m:
            for line in m.group(1).split("\n"):
                matched = re.match(
                    r"- \*\*(user|assistant)\*\*[^(]*\([^)]*\):\s*(.*)", line
                )
                if matched:
                    msgs.append({"role": matched.group(1), "content": matched.group(2).strip()})

        return msgs

    # ── Message management ────────────────────────────────────────────────

    async def add_message(self, role: str, text: str):
        """Append a message entry to the Recent Messages section."""
        if not text:
            return
        content = self._read()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"- **{role}** ({timestamp}): {text}\n"

        # Append to end of Recent Messages section
        m = re.search(r"(### Recent Messages\n)", content)
        if m:
            insert_at = m.end()
            # Check if there's content after header already
            rest = content[insert_at:]
            next_sec = re.search(r"\n(?=##)", rest)
            if next_sec:
                insert_at += next_sec.start()
                content = content[:insert_at] + "\n" + entry + content[insert_at:]
            else:
                # No next section — append after header
                content = content[:insert_at] + "\n" + entry + rest
        else:
            content += f"\n### Recent Messages\n{entry}"

        self._write(content)

    def count_messages(self) -> int:
        """Count the number of user/assistant entries in Recent Messages."""
        content = self._read()
        m = re.search(r"### Recent Messages\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if not m:
            return 0
        return len([
            l for l in m.group(1).split("\n")
            if re.match(r"- \*\*(user|assistant)\*\*", l)
        ])

    async def compress_if_needed(self, llm: LLMClient, threshold: int = 50, keep: int = 20):
        """
        If total messages exceed *threshold*, compress the oldest N-*keep*
        into a summary and retain only the most recent *keep* messages.
        """
        content = self._read()

        # Locate Recent Messages section
        rm = re.search(
            r"### Recent Messages\n(.*?)(?=\n## |\Z)", content, re.DOTALL
        )
        if not rm:
            return
        entries = [
            l for l in rm.group(1).split("\n")
            if re.match(r"- \*\*(user|assistant)\*\*", l)
        ]
        if len(entries) <= threshold:
            return

        to_compress = entries[:-keep]
        to_keep = entries[-keep:]

        # Gather existing summary
        sm = re.search(
            r"### Compressed Summary\n(.*?)(?=\n### |\n## |\Z)", content, re.DOTALL
        )
        existing = ""
        if sm and not sm.group(1).strip().startswith("*"):
            existing = sm.group(1).strip()

        history = "\n".join([existing, *to_compress] if existing else to_compress)

        log.info("Compressing %d messages into summary...", len(to_compress))
        new_summary = await llm.compress(history)

        # Rebuild file content
        before = content[: rm.start()]
        after_section = content[rm.end():]
        after = f"### Compressed Summary\n{new_summary}\n\n### Recent Messages\n"
        after += "\n".join(to_keep) + "\n"
        after += after_section

        self._write(before + after)
        log.info("Compression done — kept %d recent messages", keep)

    # ── Bishbot control flag ──────────────────────────────────────────────

    def read_bishbot_status(self) -> str:
        """Return 'active' or 'inactive'."""
        content = self._read()
        m = re.search(r"## Bishbot Status\n`bishbot: (active|inactive)`", content)
        return m.group(1) if m else "inactive"

    def read_pending_outgoing(self) -> Optional[str]:
        """Return the pending proactive message text, or None."""
        content = self._read()
        m = re.search(
            r"## Pending Outgoing Message\n(.*?)(?=\n## |\Z)", content, re.DOTALL
        )
        if not m:
            return None
        lines = [
            l for l in m.group(1).split("\n")
            if l.strip() and "*Leave blank" not in l and not l.strip().startswith(">")
        ]
        # Also capture blockquote lines
        quote_lines = [
            l.strip().lstrip("> ").strip()
            for l in m.group(1).split("\n")
            if l.strip().startswith(">")
        ]
        all_lines = lines + quote_lines
        text = "\n".join(all_lines).strip()
        return text if text else None

    def clear_pending_outgoing(self):
        """Reset bishbot to inactive and clear the pending message slot."""
        content = self._read()
        content = re.sub(r"`bishbot: active`", "`bishbot: inactive`", content)
        content = re.sub(
            r"(## Pending Outgoing Message\n).*?(?=\n## |\Z)",
            r"\1*Leave blank when not in use.*\n",
            content,
            flags=re.DOTALL,
        )
        self._write(content)
        log.info("Cleared pending outgoing message, bishbot set to inactive")

    # ── Close signal ──────────────────────────────────────────────────────

    def read_close_signal(self) -> bool:
        content = self._read()
        m = re.search(r"## Close Signal\n`close: (true|false)`", content)
        return m.group(1) == "true" if m else False

    def set_close_signal(self, value: bool):
        content = self._read()
        content = re.sub(
            r"`close: (true|false)`",
            f"`close: {'true' if value else 'false'}`",
            content,
        )
        self._write(content)


# ═══════════════════════════════════════════════════════════════════════════════
#  BotHandlers
# ═══════════════════════════════════════════════════════════════════════════════

class BotHandlers:
    """PTB event handlers and the periodic guide file watcher."""

    def __init__(self, config: Config, llm: LLMClient, guide: GuideManager):
        self.config = config
        self.llm = llm
        self.guide = guide
        self.project = config.project_name
        self._app: Optional[Application] = None

    def bind(self, app: Application):
        """Store a reference to the Application for graceful shutdown."""
        self._app = app

    # ── /start command ──────────────────────────────────────────────────

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"*bishbot* active for project *{self.project}*.\n\n"
            f"Send `{self.project} your message` to chat.\n"
            f"Send `{self.project} /kill` to stop.\n\n"
            "Claude Code can also send me proactive messages by setting the "
            "bishbot flag in the guide file.",
            parse_mode="Markdown",
        )

    # ── Incoming message handler ─────────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process an incoming Telegram message."""
        if not update.message or not update.message.text:
            return

        raw = update.message.text.strip()
        prefix = f"{self.project} "

        # Silently ignore messages without the project prefix
        if not raw.lower().startswith(prefix.lower()):
            return

        body = raw[len(prefix):].strip()
        if not body:
            return

        # ── Kill keyword ──────────────────────────────────────────────
        if body.lower() == "/kill":
            await update.message.reply_text("Shutting down. Goodbye!")
            log.info("Kill keyword received, shutting down")
            self.guide.set_close_signal(True)
            asyncio.get_event_loop().call_later(1, self._shutdown)
            return

        # ── Typing indicator ──────────────────────────────────────────
        await update.message.chat.send_action(action="typing")

        # ── Store & respond ───────────────────────────────────────────
        await self.guide.add_message("user", body)
        ctx = self.guide.get_context()
        response = await self.llm.generate_response(ctx)
        await self.guide.add_message("assistant", response)

        # ── Compression check ─────────────────────────────────────────
        count = self.guide.count_messages()
        if count > 50:
            await self.guide.compress_if_needed(self.llm)

        # ── Send reply ────────────────────────────────────────────────
        await self._send_long(update, response)

    # ── Periodic guide file watcher ──────────────────────────────────────

    async def guide_watcher(self, context: ContextTypes.DEFAULT_TYPE):
        """
        Runs every POLL_INTERVAL seconds.

        Checks the guide file for:
        - close: true → graceful shutdown
        - bishbot: active + pending message → send proactive message
        """
        try:
            # ── Close signal ──────────────────────────────────────────
            if self.guide.read_close_signal():
                log.info("Close signal detected, shutting down")
                try:
                    await context.bot.send_message(
                        chat_id=self.config.chat_id,
                        text="Close signal received. bishbot shutting down. Goodbye!",
                    )
                except Exception:
                    pass
                asyncio.get_event_loop().call_later(1, self._shutdown)
                return

            # ── Proactive outgoing message ────────────────────────────
            if self.guide.read_bishbot_status() == "active":
                pending = self.guide.read_pending_outgoing()
                if pending:
                    log.info("Sending proactive message: %.60s...", pending)
                    await context.bot.send_message(
                        chat_id=self.config.chat_id,
                        text=pending,
                    )
                    await self.guide.add_message("assistant", f"[Proactive] {pending}")
                    self.guide.clear_pending_outgoing()

        except Exception as e:
            log.error("Guide watcher error: %s", e)

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _send_long(self, update: Update, text: str):
        """Send reply, splitting into chunks if >4096 characters."""
        MAX_TG = 4096
        if len(text) <= MAX_TG:
            await update.message.reply_text(text)
            return
        for i in range(0, len(text), MAX_TG):
            chunk = text[i : i + MAX_TG]
            await update.message.reply_text(chunk)

    def _shutdown(self):
        """Schedule graceful Application shutdown."""
        if self._app is None:
            return
        loop = asyncio.get_event_loop()
        loop.create_task(self._do_shutdown())

    async def _do_shutdown(self):
        """Stop the application and its event loop."""
        await asyncio.sleep(0.5)
        log.info("Stopping application...")
        try:
            await self._app.stop()
            await self._app.shutdown()
        except Exception as e:
            log.error("Shutdown error: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
#  main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """Entry point: initialise config, classes, and start polling."""

    # Validate configuration
    try:
        cfg = Config()
        cfg.validate()
    except (FileNotFoundError, ValueError) as e:
        log.error("%s", e)
        sys.exit(1)

    llm = LLMClient(cfg)
    guide = GuideManager()
    handlers = BotHandlers(cfg, llm, guide)

    # Build PTB application
    app = (
        Application.builder()
        .token(cfg.telegram_token)
        .build()
    )
    handlers.bind(app)

    # Register handlers
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))

    # Periodic guide file watcher
    if app.job_queue:
        app.job_queue.run_repeating(
            handlers.guide_watcher,
            interval=POLL_INTERVAL,
            first=POLL_INTERVAL,
        )

    log.info("─" * 50)
    log.info("bishbot started")
    log.info("  Project  : %s", cfg.project_name)
    log.info("  Model    : %s", cfg.deepseek_model)
    log.info("  Endpoint : %s", cfg.deepseek_base_url)
    log.info("  Guide    : %s", GUIDE_PATH)
    log.info("  Kill     : send \"%s\" via Telegram", cfg.kill_keyword)
    log.info("  Close    : set `close: true` in the guide file")
    log.info("  Proactive: set `bishbot: active` + write msg in guide file")
    log.info("─" * 50)

    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt received")
    finally:
        log.info("bishbot stopped")


if __name__ == "__main__":
    main()


# ── Default guide template ───────────────────────────────────────────────────

_DEFAULT_GUIDE_TEMPLATE = """# bishbot Guide

## Script Location
`C:\\Users\\Don\\Documents\\Claude\\Telegram Chat Bot\\tg_bot.py`

## Active Project
`bishbot`

Prefix all Telegram messages with `bishbot ` followed by your message.

## Bishbot Status
`bishbot: inactive`

Set to `active` when Claude Code wants to send a proactive message to the user.
Write the message in the ## Pending Outgoing Message section below.

## Pending Outgoing Message
*Leave blank when not in use.*

Claude Code writes the message here to send it via Telegram.

## Close Signal
`close: false`

Set `close: true` when the session is done. The bot shuts down gracefully.

## Kill Keyword
`bishbot /kill`

Send this as a Telegram message to stop the bot immediately.

---

## Conversation History

### Compressed Summary
*Older conversation summaries appear here automatically.*

### Recent Messages
*New messages are appended here.*
"""
