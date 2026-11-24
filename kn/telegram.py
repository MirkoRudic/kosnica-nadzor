"""Telegram bot: alerts with a photo and buttons, /status and /slika from the phone.

Only outgoing connections (long polling) - nothing on the printer PC is opened to the internet.
The token lives in .env (F6) and is never written to the log: errors are scrubbed of it.
The first chat that sends /start is bound; every other chat is ignored.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import applog

log = applog.get("telegram")
API = os.environ.get("KN_TG_API", "https://api.telegram.org") + "/bot{}/{}"


class Bot:
    def __init__(self, token, get_chat, set_chat, on_command, on_button):
        self.token = token
        self.get_chat, self.set_chat = get_chat, set_chat
        self.on_command, self.on_button = on_command, on_button
        self.offset = 0
        self.ok = False
        self.name = ""
        self._stop = False
        if token:
            threading.Thread(target=self._poll, name="telegram", daemon=True).start()
        else:
            log.warning("no TELEGRAM_TOKEN in .env, alerts go only to the page")

    def _migrate(self, new_id):
        old = self.get_chat()
        if old and str(old) != str(new_id):
            log.warning("group moved to supergroup: %s -> %s", old, new_id)
            self.set_chat(new_id, "")

    def stop(self):
        self._stop = True

    def _scrub(self, e) -> str:
        return str(e).replace(self.token, "<token>") if self.token else str(e)

    def _call(self, method, data=None, files=None, timeout=20):
        url = API.format(self.token, method)
        if files:
            b = uuid.uuid4().hex
            body = b""
            for k, v in (data or {}).items():
                body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            for k, (fname, content) in files.items():
                body += (f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                         f"Content-Type: image/jpeg\r\n\r\n").encode() + content + b"\r\n"
            body += f"--{b}--\r\n".encode()
            req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={b}"})
        else:
            req = urllib.request.Request(url, json.dumps(data or {}).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                d = json.loads(e.read())
            except Exception:
                d = {"ok": False, "description": f"HTTP {e.code}"}
        if not d.get("ok"):
            new = (d.get("parameters") or {}).get("migrate_to_chat_id")
            if new:  # group became a supergroup (e.g. after making the bot an admin): new chat id
                self._migrate(new)
            raise RuntimeError(d.get("description", "telegram error"))
        return d.get("result")

    # -- sending --
    def send(self, text, photo: bytes | None = None, buttons=None) -> int:
        """-> message_id (0 = not sent)."""
        chat = self.get_chat()
        if not (self.token and chat):
            return 0
        markup = json.dumps({"inline_keyboard": buttons}) if buttons else None
        try:
            if photo:
                d = {"chat_id": chat, "caption": text, "parse_mode": "HTML"}
                if markup:
                    d["reply_markup"] = markup
                r = self._call("sendPhoto", d, {"photo": ("slika.jpg", photo)}, timeout=60)
            else:
                d = {"chat_id": chat, "text": text, "parse_mode": "HTML",
                     "link_preview_options": {"is_disabled": True}}
                if markup:
                    d["reply_markup"] = json.loads(markup)
                r = self._call("sendMessage", d)
            return int((r or {}).get("message_id") or 1)
        except Exception as e:
            if "upgraded to a supergroup" in str(e) and not getattr(self, "_retry", False):
                self._retry = True  # chat id was just updated by _migrate: send once more
                try:
                    return self.send(text, photo, buttons)
                finally:
                    self._retry = False
            log.error("send failed: %s", self._scrub(e))
            return 0

    def group_link(self):
        """Link that opens the group: its invite link (the bot needs to be an admin to create one)."""
        chat = self.get_chat()
        if not (self.token and chat):
            return ""
        try:
            info = self._call("getChat", {"chat_id": chat})
            if info.get("title"):
                self.title = info["title"]
            if info.get("username"):
                return f"https://t.me/{info['username']}"
            if info.get("invite_link"):
                return info["invite_link"]
            # an extra link (createChatInviteLink), so the group's own invite link is never revoked
            r = self._call("createChatInviteLink", {"chat_id": chat, "name": "Košnica Nadzor"})
            return (r or {}).get("invite_link", "")
        except Exception as e:
            log.info("no group link: %s", self._scrub(e))
            return ""

    def delete(self, msg_ids) -> int:
        """Deletes the bot's messages (Telegram allows it for 48 h). -> how many were deleted."""
        chat, n = self.get_chat(), 0
        for m in msg_ids:
            try:
                self._call("deleteMessage", {"chat_id": chat, "message_id": m})
                n += 1
            except Exception as e:
                log.info("delete %s: %s", m, self._scrub(e))
        return n

    def edit_buttons(self, msg_id, buttons):
        chat = self.get_chat()
        if not (self.token and chat and msg_id):
            return
        try:
            self._call("editMessageReplyMarkup", {"chat_id": chat, "message_id": msg_id,
                                                  "reply_markup": {"inline_keyboard": buttons or []}})
        except Exception as e:
            if "not modified" not in str(e):
                log.warning("edit buttons: %s", self._scrub(e))

    def answer(self, cq_id, text):
        try:
            self._call("answerCallbackQuery", {"callback_query_id": cq_id, "text": text[:190]})
        except Exception as e:
            log.warning("answerCallbackQuery: %s", self._scrub(e))

    # -- receiving --
    def _poll(self):
        back = 3
        while not self._stop:
            try:
                if not self.ok:
                    me = self._call("getMe")
                    self.name = me.get("username", "")
                    self.ok = True
                    log.info("bot @%s connected", self.name)
                    try:
                        self._call("setMyCommands", {"commands": [
                            {"command": "status", "description": "Stanje štampača"},
                            {"command": "slika", "description": "Slika sa kamera"},
                            {"command": "strana", "description": "Link strane nadzora"}]})
                    except Exception as e:
                        log.warning("setMyCommands: %s", self._scrub(e))
                ups = self._call("getUpdates", {"offset": self.offset, "timeout": 25,
                                                "allowed_updates": ["message", "callback_query"]}, timeout=40)
                back = 3
                for u in ups:
                    self.offset = u["update_id"] + 1
                    self._handle(u)
            except Exception as e:
                if self.ok:
                    log.warning("poll error: %s", self._scrub(e))
                self.ok = "Unauthorized" not in str(e) and self.ok
                time.sleep(back)
                back = min(back * 2, 60)

    def _handle(self, u):
        if "callback_query" in u:
            cq = u["callback_query"]
            chat = cq.get("message", {}).get("chat", {}).get("id")
            if str(chat) != str(self.get_chat()):
                self.answer(cq["id"], "Nije dozvoljeno.")
                return
            who = cq.get("from", {}).get("first_name", "?")
            reply = self.on_button(cq.get("data", ""), who, cq.get("message", {}).get("message_id"))
            self.answer(cq["id"], reply or "Primljeno.")
            return
        msg = u.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat", {})
        if msg.get("migrate_to_chat_id") and str(chat.get("id")) == str(self.get_chat()):
            self._migrate(msg["migrate_to_chat_id"])
            return
        if msg.get("migrate_from_chat_id") and str(msg["migrate_from_chat_id"]) == str(self.get_chat()):
            self._migrate(chat.get("id"))
            return
        if not text.startswith("/"):
            return
        cmd = text.split()[0].split("@")[0][1:].lower()
        if not self.get_chat():
            if cmd == "start":
                self.set_chat(chat["id"], chat.get("title") or chat.get("first_name") or "")
                log.info("bound to chat %s (%s)", chat["id"], chat.get("type"))
                self.send("Košnica Nadzor je povezan. Komande: /status, /slika, /strana")
            return
        if str(chat.get("id")) != str(self.get_chat()):
            log.info("ignored command from other chat %s", chat.get("id"))
            return
        self.on_command(cmd)
