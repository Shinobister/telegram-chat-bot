# bishbot Guide

## Script Location
`C:\Users\Don\Documents\Claude\Telegram Chat Bot\tg_bot.py`

## Active Project
`bishbot`

All Telegram messages to this bot SHOULD be prefixed with `bishbot` followed by a space.
Messages without the prefix will still be processed — the bot uses conversation context
to interpret them. All outgoing messages include the `bishbot` prefix.

## Bishbot Status
`bishbot: inactive`

**How to use:** Set this to `active` when you (Claude) want to proactively send a message
to the user via Telegram. Write your message in the ## Pending Outgoing Message section below.
The bot will detect the active flag, send the pending message, and reset this to `inactive`.
Leave as `inactive` during normal back-and-forth conversation.

## Pending Outgoing Message
*Leave blank when not in use.*

## Close Signal
`close: false`

Set `close: true` when the conversation is complete (e.g., user acknowledges "we're good").
The bot will send a goodbye message and shut down gracefully.

## Kill Keyword
`bishbot /kill`

Sending this as a Telegram message immediately terminates the bot.

## How Claude Should Use This File

1. **Receiving messages**: When the user sends a message via Telegram, the bot forwards it
   to the LLM and sends the response back. Conversation history is stored below.

2. **Prefix enforcement**: All incoming messages SHOULD start with `bishbot ` but the bot
   will also process messages without the prefix by using LLM context to interpret them.
   All outgoing messages ALWAYS include the `bishbot ` prefix for identification.

3. **Unexpected messages**: If a message arrives without the expected prefix, the bot
   reads the conversation history from this file and uses the LLM API to interpret the
   user's intent and respond appropriately. These interactions are also recorded in history.

4. **Sending proactive messages**: If you need to ask the user something outside of a
   response cycle:
   a. Set `bishbot: active` above
   b. Write your message in ## Pending Outgoing Message
   c. The bot detects the flag, sends your message (with `bishbot ` prefix), and resets to `inactive`
*Leave blank when not in use.*

## Conversation History

### Compressed Summary
*Summaries of older conversation history appear here automatically when messages exceed 50.*

### Recent Messages
*New messages are appended here in chronological order.*
