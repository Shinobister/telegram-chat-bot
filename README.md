# bishbot — Telegram Bot + LLM Integration

A universal, modular Telegram bot that listens for incoming messages, forwards them to an LLM (DeepSeek V4 Flash via OpenAI-compatible API), and sends back responses. Designed to work alongside Claude Code via a shared Markdown guide file.

## Files

| File | Purpose |
|------|---------|
| `tg_bot.py` | Main bot script (Config, LLMClient, GuideManager, BotHandlers) |
| `tg_guide.md` | Claude-facing instructions + conversation history with compressed summaries |
| `tg_config.json` | Telegram bot token and chat ID (not committed to git) |
| `.env` | DeepSeek API key (not committed to git) |
| `requirements.txt` | Python dependencies |
| `start-prompt.txt` | Original project specification |

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Create `tg_config.json`** in the project directory:
   ```json
   {
     "token": "YOUR_TELEGRAM_BOT_TOKEN",
     "chat_id": "YOUR_CHAT_ID"
   }
   ```

3. **Create `.env`** in the project directory:
   ```
   DEEPSEEK_API_KEY=sk-your-key-here
   ```

4. **Run the bot:**
   ```bash
   python tg_bot.py
   ```

## Usage

### Sending Messages to the Bot

All messages must be prefixed with the project name (`bishbot`):
```
bishbot Hello, what are you working on?
```

### Kill Keyword

Send this as a Telegram message to stop the bot:
```
bishbot /kill
```

### How Claude Code Uses This

The bot is controlled by Claude Code through `tg_guide.md`:

- **Proactive messaging**: Claude sets `bishbot: active` in the guide file and writes a pending message. The bot detects this every 5 seconds, sends the message, and resets the flag.
- **Close signal**: When conversation is done, Claude sets `close: true`. The bot sends a goodbye message and shuts down.
- **History compression**: After 50+ messages, older history is compressed into a summary via the LLM, keeping the last 20 messages intact.

## Architecture

```
Telegram User ←→ tg_bot.py (polling) ←→ DeepSeek API
                      ↕
                tg_guide.md (shared state)
                      ↕
                Claude Code (reads/writes guide)
```

All components are in a single file (`tg_bot.py`) for easy deployment:
- **Config** — loads Telegram token, API keys, project name
- **LLMClient** — wraps AsyncOpenAI for chat completions and history compression
- **GuideManager** — reads/writes `tg_guide.md` (history, control flags)
- **BotHandlers** — PTB message handlers + periodic guide watcher
