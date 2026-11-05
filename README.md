# Telegram `/all` Bot

The bot keeps track of people who send messages in a group or join it. When a group administrator sends `/all`, the bot mentions the people it knows. It also accepts `@all`. The command can be used once every five minutes per group.

## Setup

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, and save the token it gives you. Do not share the token with anyone.
2. Add the bot to your group **as an administrator**. It needs permission to post messages and receive membership updates. It does not need permission to delete messages, ban users, or perform other administrative actions.
3. Install Python 3.9 or newer. No third-party packages are required.

## Run

From the project directory, run these commands in a terminal:

```sh
export TELEGRAM_BOT_TOKEN='your_token_from_BotFather'
python3 bot.py
```

The bot responds on Telegram while this process is running. To keep it available, run it on a computer that stays on or on a VPS. This version uses long polling, so it does not need a public address or an HTTPS endpoint. Participants and bot state are stored in the local `participants.sqlite3` file. You can change its location with `BOT_DB_PATH`. Run only one bot instance per database.

## Usage

A group administrator sends `/all` or `@all`. If the group has multiple bots, they can use `/all@your_bot_username`. Mentions are sent in batches of 25 people, with a pause between messages.

**Telegram limitation:** The Bot API does not provide a complete list of group members. The bot cannot immediately mention people who were already in the group before it was added and have not posted since. Ask those people to send any message to the group. If the bot is offline long enough to miss leave events, its local list may temporarily include people who have left.

If Telegram returns an error while the bot is sending messages, the error appears in the terminal. The bot keeps running and processes later commands. If a mention run stops early, try `/all` again after five minutes.
