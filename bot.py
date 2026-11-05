#!/usr/bin/env python3
"""Telegram group mention bot using only the Python standard library."""

import html
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DB_PATH = Path(os.environ.get("BOT_DB_PATH", "participants.sqlite3"))
COOLDOWN_SECONDS = 300
MENTIONS_PER_MESSAGE = 25
GROUP_MESSAGE_DELAY = 3
LOG = logging.getLogger("all-bot")


class TelegramError(Exception):
    pass


def api(method, **params):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    payload = urllib.parse.urlencode(
        {key: json.dumps(value) if isinstance(value, (list, dict)) else value
         for key, value in params.items()}
    ).encode()
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, data=payload, timeout=45) as response:
                result = json.load(response)
            if result.get("ok"):
                return result["result"]
            raise TelegramError(result.get("description", "Unknown Telegram error"))
        except urllib.error.HTTPError as exc:
            try:
                error = json.load(exc)
            except (ValueError, OSError):
                error = {}
            if exc.code == 429 and attempt < 4:
                delay = min(int(error.get("parameters", {}).get("retry_after", 3)), 60)
                time.sleep(delay)
                continue
            if 500 <= exc.code < 600 and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise TelegramError(error.get("description", f"HTTP {exc.code}")) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 4:
                raise TelegramError(str(exc)) from exc
            time.sleep(2 ** attempt)
    raise TelegramError(f"{method} failed after retries")


def init_db(db):
    db.execute("CREATE TABLE IF NOT EXISTS members (chat_id INTEGER, user_id INTEGER, name TEXT, active INTEGER, PRIMARY KEY (chat_id, user_id))")
    db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER)")
    db.commit()


def save_member(db, chat_id, user, active=True):
    if not user or user.get("is_bot"):
        return
    name = " ".join(filter(None, (user.get("first_name"), user.get("last_name"))))
    name = name or user.get("username") or "участник"
    db.execute(
        "INSERT INTO members VALUES (?, ?, ?, ?) ON CONFLICT(chat_id, user_id) DO UPDATE SET name=excluded.name, active=excluded.active",
        (chat_id, user["id"], name, int(active)),
    )
    db.commit()


def set_state(db, key, value):
    db.execute("INSERT INTO state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    db.commit()


def get_state(db, key, default=0):
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def mention_batches(members):
    batch = []
    for user_id, name in members:
        label = html.escape(name, quote=True)
        mention = f'<a href="tg://user?id={user_id}">{label}</a>'
        if batch and (len(batch) >= MENTIONS_PER_MESSAGE or len(" ".join(batch)) + len(mention) + 1 > 3500):
            yield " ".join(batch)
            batch = []
        batch.append(mention)
    if batch:
        yield " ".join(batch)


def is_all_command(text, bot_username):
    text = text.strip()
    if text.lower() == "@all":
        return True
    match = re.fullmatch(r"/all(?:@([A-Za-z0-9_]+))?", text, re.IGNORECASE)
    return bool(match and (not match.group(1) or match.group(1).lower() == bot_username.lower()))


def send(chat_id, text, **kwargs):
    return api("sendMessage", chat_id=chat_id, text=text, **kwargs)


def handle_all(db, message, bot_username):
    chat = message["chat"]
    if chat["type"] not in ("group", "supergroup"):
        send(chat["id"], "Добавьте меня администратором в группу и вызовите там /all.")
        return

    user = message.get("from")
    anonymous_admin = message.get("sender_chat", {}).get("id") == chat["id"]
    if not anonymous_admin:
        if not user:
            return
        member = api("getChatMember", chat_id=chat["id"], user_id=user["id"])
        if member["status"] not in ("administrator", "creator"):
            send(chat["id"], "Команду /all может вызвать только администратор группы.")
            return

    key = f"last_all:{chat['id']}"
    remaining = COOLDOWN_SECONDS - (int(time.time()) - get_state(db, key))
    if remaining > 0:
        send(chat["id"], f"Повторить /all можно через {remaining} сек.")
        return

    members = db.execute(
        "SELECT user_id, name FROM members WHERE chat_id=? AND active=1 ORDER BY user_id", (chat["id"],)
    ).fetchall()
    if not members:
        send(chat["id"], "Пока не знаю участников. Я запоминаю их, когда они пишут или входят в группу.")
        return

    set_state(db, key, int(time.time()))
    for index, batch in enumerate(mention_batches(members)):
        if index:
            time.sleep(GROUP_MESSAGE_DELAY)
        send(chat["id"], batch, parse_mode="HTML")
    LOG.info("Mentioned %d participants in chat %s", len(members), chat["id"])


def handle_update(db, update, bot_username):
    membership = update.get("chat_member")
    if membership:
        chat_id = membership["chat"]["id"]
        new = membership["new_chat_member"]
        user = new["user"]
        active = new["status"] in ("creator", "administrator", "member") or (
            new["status"] == "restricted" and new.get("is_member", False)
        )
        save_member(db, chat_id, user, active)

    message = update.get("message")
    if not message:
        return
    chat = message["chat"]
    if chat["type"] in ("group", "supergroup"):
        # Anonymous admins and channel posts may carry a synthetic sender in `from`.
        if not message.get("sender_chat"):
            save_member(db, chat["id"], message.get("from"))
        for user in message.get("new_chat_members", []):
            save_member(db, chat["id"], user)
        if "left_chat_member" in message:
            save_member(db, chat["id"], message["left_chat_member"], False)
    text = message.get("text", "")
    if is_all_command(text, bot_username):
        handle_all(db, message, bot_username)
    elif chat["type"] == "private" and text.strip().lower() in ("/start", "/help"):
        send(chat["id"], "Добавьте меня администратором в группу. Администратор группы может вызвать /all или @all. Я упомяну участников, которых успел запомнить.")


def main():
    if not TOKEN:
        sys.exit("Укажите TELEGRAM_BOT_TOKEN в переменной окружения.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = sqlite3.connect(DB_PATH)
    init_db(db)
    me = api("getMe")
    username = me["username"]
    LOG.info("Started @%s", username)
    offset = get_state(db, "offset")
    while True:
        try:
            updates = api("getUpdates", offset=offset, timeout=30, allowed_updates=["message", "chat_member"])
            for update in updates:
                try:
                    handle_update(db, update, username)
                except (TelegramError, KeyError, ValueError, sqlite3.Error):
                    LOG.exception("Could not handle update %s", update["update_id"])
                offset = update["update_id"] + 1
                set_state(db, "offset", offset)
        except (TelegramError, sqlite3.Error):
            LOG.exception("Polling failed; retrying in 5 seconds")
            time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nОстановлено")
