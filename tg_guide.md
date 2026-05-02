# bishbot Guide

## Script Location
`C:\Users\Don\Documents\Claude\Telegram Chat Bot\tg_bot.py`

## Active Project
`bishbot`

All Telegram messages to this bot must be prefixed with `bishbot` followed by a space.
Example: `bishbot Hello, what are you working on?`

## Bishbot Status
`bishbot: inactive`

**How to use:** Set this to `active` when you (Claude) want to proactively send a message
to the user via Telegram. Write your message in the ## Pending Outgoing Message section below.
The bot will detect the active flag, send the pending message, and reset this to `inactive`.
Leave as `inactive` during normal back-and-forth conversation.

## Pending Outgoing Message
*Leave blank when not in use.*

When `bishbot: active`, write the message you want to send here. The bot will send it
and clear this section. Example:

> Hey Don, I need your input on the database schema. Which engine do you prefer?

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

2. **Sending proactive messages**: If you need to ask the user something outside of a
   response cycle:
   a. Set `bishbot: active` above
   b. Write your message in ## Pending Outgoing Message
   c. Save this file
   d. The bot picks it up within 5 seconds, sends it, and resets the flag

3. **Ending the session**: When the user indicates they're done:
   a. Set `close: true` above
   b. Save this file
   c. The bot detects the close signal and shuts down

4. **History awareness**: Read the ## Conversation History section below to understand
   what's been discussed. The LLM receives the compressed summary + last 20 messages
   as context for each response.

---

## Conversation History

### Compressed Summary
*Summaries of older conversation history appear here automatically when messages exceed 50.*

### Recent Messages
*New messages are appended here in chronological order.*
