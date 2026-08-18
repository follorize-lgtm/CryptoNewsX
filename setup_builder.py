from html import escape

from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters


class OwnerSetupBuilder:
    """Owner-only Telegram message builder modeled after Wabble's /misucreate."""

    KEY = "setup_draft"
    MEDIA_LABELS = {
        "photo": "📷 Photo",
        "video": "🎬 Video",
        "animation": "🎞 GIF",
        "document": "📎 Document",
        "audio": "🎵 Audio",
        "voice": "🎤 Voice",
        "sticker": "🎨 Sticker",
    }

    def __init__(self, owner_id):
        self.owner_id = owner_id

    def register(self, app):
        app.add_handler(CommandHandler("setup", self.start))
        app.add_handler(CommandHandler("cancel", self.cancel))
        app.add_handler(CallbackQueryHandler(self.callback, pattern=r"^setup_"))
        app.add_handler(
            MessageHandler(
                filters.ChatType.PRIVATE
                & ~filters.COMMAND
                & (
                    filters.TEXT
                    | filters.PHOTO
                    | filters.VIDEO
                    | filters.ANIMATION
                    | filters.Document.ALL
                    | filters.AUDIO
                    | filters.VOICE
                    | filters.Sticker.ALL
                ),
                self.input,
            )
        )

    async def install_owner_menu(self, app):
        if self.owner_id is None:
            return
        try:
            await app.bot.set_my_commands(
                [BotCommand("setup", "Owner message builder")],
                scope=BotCommandScopeChat(chat_id=self.owner_id),
            )
        except TelegramError:
            pass

    def _owner_private(self, update):
        user, chat = update.effective_user, update.effective_chat
        return bool(
            self.owner_id is not None
            and user
            and chat
            and user.id == self.owner_id
            and chat.type == ChatType.PRIVATE
        )

    @staticmethod
    def _button(text, *, callback_data=None, url=None, style=None):
        kwargs = {"text": text}
        if callback_data is not None:
            kwargs["callback_data"] = callback_data
        if url is not None:
            kwargs["url"] = url
        if style:
            # Forward modern Bot API button styling while keeping PTB 21.6.
            kwargs["api_kwargs"] = {"style": style}
        return InlineKeyboardButton(**kwargs)

    def _draft(self, context):
        return context.user_data.get(self.KEY)

    @staticmethod
    def _new(chat_id):
        return {
            "step": "content",
            "chat_id": chat_id,
            "html": "",
            "media": None,
            "media_type": None,
            "buttons": [],
            "button_row": 0,
            "pending_button": None,
            "preview_message_id": None,
        }

    @staticmethod
    def _html(msg):
        if msg.text:
            return msg.text_html or ""
        if msg.caption:
            return msg.caption_html or ""
        return ""

    @staticmethod
    def _capture_media(msg, draft):
        if msg.photo:
            draft["media"], draft["media_type"] = msg.photo[-1].file_id, "photo"
        elif msg.video:
            draft["media"], draft["media_type"] = msg.video.file_id, "video"
        elif msg.animation:
            draft["media"], draft["media_type"] = msg.animation.file_id, "animation"
        elif msg.document:
            draft["media"], draft["media_type"] = msg.document.file_id, "document"
        elif msg.audio:
            draft["media"], draft["media_type"] = msg.audio.file_id, "audio"
        elif msg.voice:
            draft["media"], draft["media_type"] = msg.voice.file_id, "voice"
        elif msg.sticker:
            draft["media"], draft["media_type"] = msg.sticker.file_id, "sticker"
        else:
            return False
        return True

    @staticmethod
    def _rows(draft):
        return [row for row in draft["buttons"] if row]

    def _url_keyboard(self, draft):
        rows = []
        for row in self._rows(draft):
            rows.append([
                self._button(item["text"], url=item["url"], style=item.get("style"))
                for item in row
            ])
        return InlineKeyboardMarkup(rows) if rows else None

    @staticmethod
    def _ensure_row(draft):
        idx = int(draft.get("button_row", 0))
        while len(draft["buttons"]) <= idx:
            draft["buttons"].append([])
        return draft["buttons"][idx]

    def _preview_text(self, draft):
        parts = ["📝 <b>Setup — Preview</b>"]
        if draft["media"]:
            parts.append(f"<b>Media:</b> {self.MEDIA_LABELS.get(draft['media_type'], draft['media_type'])}")
        if draft["html"]:
            parts.append(f"<b>Text:</b>\n{draft['html']}")
        rows = self._rows(draft)
        if rows:
            lines = ["<b>Buttons:</b>"]
            for i, row in enumerate(rows, 1):
                style = row[0].get("style") if row else None
                icon = {"success": "🟢", "danger": "🔴", "primary": "🔵"}.get(style, "⬜")
                lines.append(f"{i}. {' | '.join(escape(x['text']) for x in row)} {icon}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    def _preview_keyboard(self, draft):
        media = "📎 Change Media" if draft["media"] else "📎 Attach Media"
        rows = [[
            self._button("📤 Send to Me", callback_data="setup_sendme", style="success"),
            self._button("📤 Send to Chat", callback_data="setup_send", style="primary"),
        ]]
        button_rows = self._rows(draft)
        if button_rows:
            rows += [
                [
                    self._button("➕ Button", callback_data="setup_addbtn"),
                    self._button("↩ New Row", callback_data="setup_newrow"),
                    self._button(media, callback_data="setup_media"),
                ],
                [
                    self._button("🗑 Clear", callback_data="setup_clearbtn"),
                    self._button("❌ Cancel", callback_data="setup_cancel", style="danger"),
                ],
            ]
            for i in range(len(button_rows)):
                rows.append([
                    self._button(f"{i + 1}: 🔴", callback_data=f"setup_rstyle:{i}:danger"),
                    self._button("🟢", callback_data=f"setup_rstyle:{i}:success"),
                    self._button("🔵", callback_data=f"setup_rstyle:{i}:primary"),
                    self._button("⬜", callback_data=f"setup_rstyle:{i}:none"),
                    self._button("🗑", callback_data=f"setup_remrow:{i}"),
                ])
        else:
            rows.append([
                self._button("➕ Buttons", callback_data="setup_addbtn"),
                self._button(media, callback_data="setup_media"),
                self._button("❌ Cancel", callback_data="setup_cancel", style="danger"),
            ])
        return InlineKeyboardMarkup(rows)

    async def _preview(self, context):
        draft = self._draft(context)
        if not draft:
            return
        text, keyboard = self._preview_text(draft), self._preview_keyboard(draft)
        old_id = draft.get("preview_message_id")
        if old_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=draft["chat_id"],
                    message_id=old_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                return
            except BadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    return
            except TelegramError:
                pass
            draft["preview_message_id"] = None
        sent = await context.bot.send_message(
            draft["chat_id"], text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        draft["preview_message_id"] = sent.message_id

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._owner_private(update):
            return
        context.user_data[self.KEY] = self._new(update.effective_chat.id)
        await update.effective_message.reply_text(
            "📝 <b>Setup</b>\n\n"
            "Send the content — text, photo, video, GIF, document, audio, voice, sticker, or text + media together.\n\n"
            "<i>/cancel to abort</i>",
            parse_mode=ParseMode.HTML,
        )

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._owner_private(update):
            return
        if context.user_data.pop(self.KEY, None) is not None:
            await update.effective_message.reply_text("❌ <b>Setup cancelled.</b>", parse_mode=ParseMode.HTML)

    async def input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._owner_private(update):
            return
        draft, msg = self._draft(context), update.effective_message
        if not draft or msg is None:
            return
        text = msg.text or msg.caption or ""
        step = draft.get("step", "content")

        if step == "target":
            target = text.strip()
            if not target:
                await msg.reply_text("Send a numeric chat ID, @username, or <b>me</b>.", parse_mode=ParseMode.HTML)
                return
            if target.lower() == "me":
                target = str(draft["chat_id"])
            elif target.startswith("@"):
                try:
                    target = str((await context.bot.get_chat(target)).id)
                except TelegramError:
                    await msg.reply_text(
                        f"❌ <b>Could not find</b> <code>{escape(target)}</code>.\n"
                        "<i>Make sure the bot is a member, or use the numeric chat ID. Try another target or /cancel.</i>",
                        parse_mode=ParseMode.HTML,
                    )
                    return
            await self._send(target, context)
            return

        if step == "media":
            if not self._capture_media(msg, draft):
                await msg.reply_text("❌ Send a photo, video, GIF, document, audio, voice, or sticker.")
                return
            draft["step"] = "preview"
            draft["preview_message_id"] = None
            await self._preview(context)
            return

        if step == "button_label":
            label = text.strip()
            if not label:
                await msg.reply_text("❌ Send a non-empty button label.")
                return
            draft["pending_button"] = {"text": label}
            draft["step"] = "button_url"
            await msg.reply_text(
                "<b>Button URL</b>\n\nSend an <code>https://</code> or <code>http://</code> link.",
                parse_mode=ParseMode.HTML,
            )
            return

        if step == "button_url":
            url = text.strip()
            if not url.startswith(("https://", "http://")):
                await msg.reply_text("❌ URL must start with <code>https://</code> or <code>http://</code>.", parse_mode=ParseMode.HTML)
                return
            draft["pending_button"]["url"] = url
            draft["step"] = "button_color"
            await self._color_prompt(context, draft["chat_id"])
            return

        if step == "button_color":
            await msg.reply_text("Choose the button color from the buttons above, or /cancel.")
            return

        if step == "content":
            has_media = self._capture_media(msg, draft)
            html = self._html(msg)
            if html:
                draft["html"] = html
            if not html and not has_media:
                await msg.reply_text("❌ Send text, media, or both.")
                return
            draft["step"] = "preview"
            await self._preview(context)

    async def _label_prompt(self, context, chat_id):
        await context.bot.send_message(
            chat_id, "<b>Button label</b>\n\nSend the text shown on the button.", parse_mode=ParseMode.HTML
        )

    async def _color_prompt(self, context, chat_id):
        keyboard = InlineKeyboardMarkup([[
            self._button("🟢 Green", callback_data="setup_btncolor:success"),
            self._button("🔴 Red", callback_data="setup_btncolor:danger"),
            self._button("🔵 Blue", callback_data="setup_btncolor:primary"),
            self._button("⬜ None", callback_data="setup_btncolor:none"),
        ]])
        await context.bot.send_message(chat_id, "<b>Button color</b>", parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if query is None:
            return
        if not self._owner_private(update):
            await query.answer("Owner only.", show_alert=True)
            return
        data = query.data or ""

        if data == "setup_cancel":
            context.user_data.pop(self.KEY, None)
            await query.answer("Cancelled")
            try:
                await query.edit_message_text("❌ <b>Setup cancelled.</b>", parse_mode=ParseMode.HTML)
            except TelegramError:
                pass
            return

        draft = self._draft(context)
        if not draft:
            await query.answer("Session expired. Use /setup again.", show_alert=True)
            return
        chat_id = draft["chat_id"]

        if data == "setup_sendme":
            await query.answer("Sending to you...")
            await self._send(str(chat_id), context)
            return
        if data == "setup_send":
            draft["step"], draft["pending_button"] = "target", None
            await query.answer()
            await context.bot.send_message(
                chat_id,
                "<b>Where to send?</b>\n\nEnter a numeric chat ID or @username. Type <code>me</code> to send to yourself.",
                parse_mode=ParseMode.HTML,
            )
            return
        if data == "setup_media":
            draft["step"], draft["pending_button"] = "media", None
            await query.answer()
            action = "replace the current" if draft["media"] else "add"
            await context.bot.send_message(
                chat_id,
                f"<b>Send media:</b> photo, video, GIF, document, audio, voice, or sticker.\n<i>This will {action} media.</i>",
                parse_mode=ParseMode.HTML,
            )
            return
        if data in {"setup_addbtn", "setup_newrow"}:
            if data == "setup_newrow" or not draft["buttons"]:
                if not draft["buttons"] or draft["buttons"][-1]:
                    draft["buttons"].append([])
                draft["button_row"] = len(draft["buttons"]) - 1
            else:
                draft["button_row"] = min(draft.get("button_row", 0), len(draft["buttons"]) - 1)
            draft["pending_button"], draft["step"] = None, "button_label"
            await query.answer("New row" if data == "setup_newrow" else None)
            await self._label_prompt(context, chat_id)
            return
        if data.startswith("setup_btncolor:"):
            style = data.split(":", 1)[1]
            pending = draft.get("pending_button") or {}
            if draft.get("step") != "button_color" or not pending.get("url"):
                await query.answer("No button waiting for a color.", show_alert=True)
                return
            item = dict(pending)
            if style != "none":
                item["style"] = style
            self._ensure_row(draft).append(item)
            draft["pending_button"], draft["step"] = None, "preview"
            await query.answer("Button added")
            await self._preview(context)
            return
        if data == "setup_clearbtn":
            draft["buttons"], draft["button_row"], draft["pending_button"], draft["step"] = [], 0, None, "preview"
            await query.answer("Cleared")
            await self._preview(context)
            return
        if data.startswith("setup_rstyle:"):
            try:
                _, row_text, style = data.split(":", 2)
                row_index = int(row_text)
                row = self._rows(draft)[row_index]
            except (ValueError, IndexError):
                await query.answer("Row no longer exists.", show_alert=True)
                return
            for item in row:
                if style == "none":
                    item.pop("style", None)
                else:
                    item["style"] = style
            await query.answer(f"Row {row_index + 1} updated")
            await self._preview(context)
            return
        if data.startswith("setup_remrow:"):
            try:
                visible_index = int(data.split(":", 1)[1])
                real_index = [i for i, row in enumerate(draft["buttons"]) if row][visible_index]
            except (ValueError, IndexError):
                await query.answer("Row no longer exists.", show_alert=True)
                return
            del draft["buttons"][real_index]
            draft["buttons"] = self._rows(draft)
            draft["button_row"] = max(0, len(draft["buttons"]) - 1)
            await query.answer("Removed")
            await self._preview(context)
            return
        await query.answer()

    async def _send(self, target, context: ContextTypes.DEFAULT_TYPE):
        draft = self._draft(context)
        if not draft:
            return
        keyboard, html = self._url_keyboard(draft), draft["html"] or ""
        common = {"parse_mode": ParseMode.HTML}
        if keyboard:
            common["reply_markup"] = keyboard
        try:
            media, kind = draft["media"], draft["media_type"]
            if media and kind != "sticker":
                sender = {
                    "photo": context.bot.send_photo,
                    "video": context.bot.send_video,
                    "animation": context.bot.send_animation,
                    "document": context.bot.send_document,
                    "audio": context.bot.send_audio,
                    "voice": context.bot.send_voice,
                }.get(kind)
                if sender:
                    await sender(target, media, caption=html or None, **common)
                else:
                    await context.bot.send_message(target, html or "Message", disable_web_page_preview=True, **common)
            elif media and kind == "sticker":
                await context.bot.send_sticker(target, media)
                if html or keyboard:
                    await context.bot.send_message(
                        target, html or "\u2063", parse_mode=ParseMode.HTML,
                        reply_markup=keyboard, disable_web_page_preview=True,
                    )
            else:
                await context.bot.send_message(
                    target, html, parse_mode=ParseMode.HTML,
                    reply_markup=keyboard, disable_web_page_preview=True,
                )
            await context.bot.send_message(
                draft["chat_id"], f"✅ <b>Sent to</b> <code>{escape(str(target))}</code>", parse_mode=ParseMode.HTML
            )
            context.user_data.pop(self.KEY, None)
        except (Forbidden, BadRequest, TelegramError) as exc:
            draft["step"] = "target"
            error_text = str(exc)
            lowered = error_text.lower()
            hint = ""
            if "chat not found" in lowered:
                hint = "\n<i>Make sure the bot is a member of the target chat; numeric chat ID is safest.</i>"
            elif "blocked" in lowered:
                hint = "\n<i>The target user blocked the bot.</i>"
            await context.bot.send_message(
                draft["chat_id"],
                f"❌ <b>Failed:</b> {escape(error_text)}{hint}\n\n<i>Try another target or /cancel.</i>",
                parse_mode=ParseMode.HTML,
            )
