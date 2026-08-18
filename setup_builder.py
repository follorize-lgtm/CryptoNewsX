from html import escape

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters


class OwnerSetupBuilder:
    """Owner-only Telegram message builder matching Wabble's /misucreate UX."""

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
    MAX_BUTTONS_PER_ROW = 3

    def __init__(self, owner_id):
        self.owner_id = owner_id

    def _owner_id_set(self):
        multi = getattr(self, "owner_ids", None)
        if multi is not None:
            return {int(owner_id) for owner_id in multi}
        if self.owner_id is None:
            return set()
        return {int(self.owner_id)}

    def register(self, app):
        owner_ids = self._owner_id_set()
        if not owner_ids:
            return

        owner_private = filters.ChatType.PRIVATE & filters.User(user_id=list(owner_ids))
        content_filter = (
            filters.TEXT
            | filters.PHOTO
            | filters.VIDEO
            | filters.ANIMATION
            | filters.Document.ALL
            | filters.AUDIO
            | filters.VOICE
            | filters.Sticker.ALL
        )

        # Fail closed at routing level. Non-owners do not match /setup or /cancel.
        app.add_handler(CommandHandler("setup", self.start, filters=owner_private))
        app.add_handler(CommandHandler("cancel", self.cancel, filters=owner_private))
        app.add_handler(CallbackQueryHandler(self.callback, pattern=r"^setup_"))
        app.add_handler(
            MessageHandler(owner_private & ~filters.COMMAND & content_filter, self.input)
        )

    async def install_owner_menu(self, app):
        # Only owner-chat scopes receive /setup. No public/default command scope.
        for owner_id in self._owner_id_set():
            try:
                await app.bot.set_my_commands(
                    [BotCommand("setup", "Owner message builder")],
                    scope=BotCommandScopeChat(chat_id=owner_id),
                )
            except TelegramError:
                pass

    def _owner_private(self, update):
        user, chat = update.effective_user, update.effective_chat
        return bool(
            user
            and chat
            and user.id in self._owner_id_set()
            and chat.type == ChatType.PRIVATE
        )

    @staticmethod
    def _button(
        text,
        *,
        callback_data=None,
        url=None,
        style=None,
        icon_custom_emoji_id=None,
    ):
        kwargs = {"text": text}
        if callback_data is not None:
            kwargs["callback_data"] = callback_data
        if url is not None:
            kwargs["url"] = url

        # PTB 21.6 predates these Bot API fields, so forward them directly.
        api_kwargs = {}
        if style:
            api_kwargs["style"] = style
        if icon_custom_emoji_id:
            api_kwargs["icon_custom_emoji_id"] = str(icon_custom_emoji_id)
        if api_kwargs:
            kwargs["api_kwargs"] = api_kwargs

        return InlineKeyboardButton(**kwargs)

    def _draft(self, context):
        return context.user_data.get(self.KEY)

    @staticmethod
    def _new(chat_id):
        return {
            "step": "content",
            "chat_id": chat_id,
            "html": "",
            "plain_text": "",
            "media": None,
            "media_type": None,
            "buttons": [],
            "pending_button": None,
            "force_new_row": False,
            "preview_message_id": None,
        }

    @staticmethod
    def _html(msg):
        # PTB converts all Telegram message entities to HTML, including
        # custom_emoji -> <tg-emoji emoji-id="...">...</tg-emoji>.
        if msg.text:
            return msg.text_html or ""
        if msg.caption:
            return msg.caption_html or ""
        return ""

    @staticmethod
    def _custom_emoji_id(msg):
        entities = msg.entities if msg.text else msg.caption_entities
        for entity in entities or ():
            entity_type = str(getattr(entity, "type", "")).lower()
            if entity_type.endswith("custom_emoji"):
                custom_id = getattr(entity, "custom_emoji_id", None)
                if custom_id:
                    return str(custom_id)
        return None

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

    @staticmethod
    def _normalize_button_action(raw):
        value = raw.strip()
        if not value:
            return None
        lowered = value.lower()
        if lowered.startswith(("https://", "http://", "tg://")):
            return {"url": value}
        if lowered.startswith("t.me/"):
            return {"url": "https://" + value}
        if lowered.startswith("www."):
            return {"url": "https://" + value}
        if value.startswith("@") and len(value) > 1:
            return {"url": "https://t.me/" + value[1:]}

        # Match Wabble's URL/action input: non-URL action names become callback_data.
        if len(value.encode("utf-8")) <= 64 and "\n" not in value:
            return {"callback_data": value}
        return None

    def _message_keyboard(self, draft):
        rows = []
        for row in self._rows(draft):
            built = []
            for item in row:
                built.append(
                    self._button(
                        item["text"],
                        url=item.get("url"),
                        callback_data=item.get("callback_data"),
                        style=item.get("style"),
                        icon_custom_emoji_id=item.get("icon_custom_emoji_id"),
                    )
                )
            if built:
                rows.append(built)
        return InlineKeyboardMarkup(rows) if rows else None

    def _add_pending_button(self, draft, style):
        item = dict(draft.get("pending_button") or {})
        if not item.get("text"):
            return None
        if style != "none":
            item["style"] = style

        rows = draft["buttons"]
        if (
            not draft.get("force_new_row")
            and rows
            and rows[-1]
            and len(rows[-1]) < self.MAX_BUTTONS_PER_ROW
        ):
            rows[-1].append(item)
        else:
            rows.append([item])

        draft["pending_button"] = None
        draft["force_new_row"] = False
        draft["step"] = "preview"
        return item

    def _preview_text(self, draft):
        text = "🔥 <b>Setup — Preview</b>\n\n"
        if draft["media"]:
            text += f"<b>Media:</b> {self.MEDIA_LABELS.get(draft['media_type'], draft['media_type'])}\n"
        if draft["html"]:
            text += f"<b>Text:</b>\n{draft['html']}\n"

        rows = self._rows(draft)
        if rows:
            text += "\n<b>Buttons:</b>\n"
            for index, row in enumerate(rows, 1):
                style = row[0].get("style") if row else None
                style_icon = {"success": "🟢", "danger": "🔴", "primary": "🔵"}.get(style, "⬜")
                labels = " | ".join(escape(item["text"]) for item in row)
                text += f"  {index}. {labels} [{style_icon}]\n"
        return text.rstrip()

    def _preview_keyboard(self, draft):
        media_label = "📎 Change Media" if draft["media"] else "📎 Attach Media"
        rows = [[
            self._button("📤 Send to Me", callback_data="setup_sendme", style="success"),
            self._button("📤 Send to Chat", callback_data="setup_send", style="primary"),
        ]]

        button_rows = self._rows(draft)
        if button_rows:
            rows.append([
                self._button("➕ Button", callback_data="setup_addbtn"),
                self._button("↩ New Row", callback_data="setup_newrow"),
                self._button(media_label, callback_data="setup_media"),
            ])
            rows.append([
                self._button("🗑 Clear", callback_data="setup_clearbtn"),
                self._button("❌", callback_data="setup_cancel", style="danger"),
            ])
        else:
            rows.append([
                self._button("➕ Buttons", callback_data="setup_addbtn"),
                self._button(media_label, callback_data="setup_media"),
                self._button("❌", callback_data="setup_cancel", style="danger"),
            ])

        for index in range(len(button_rows)):
            rows.append([
                self._button(f"{index + 1}: 🔴", callback_data=f"setup_rstyle:{index}:danger"),
                self._button("🟢", callback_data=f"setup_rstyle:{index}:success"),
                self._button("🔵", callback_data=f"setup_rstyle:{index}:primary"),
                self._button("⬜", callback_data=f"setup_rstyle:{index}:none"),
                self._button("🗑", callback_data=f"setup_remrow:{index}"),
            ])
        return InlineKeyboardMarkup(rows)

    async def _preview(self, context):
        draft = self._draft(context)
        if not draft:
            return
        text = self._preview_text(draft)
        keyboard = self._preview_keyboard(draft)
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
            draft["chat_id"],
            text,
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
            "🔥 <b>Setup</b>\n\n"
            "Send me the content — text, photo, video, or all at once.\n"
            "<i>Formatting and Telegram custom/animated emojis are preserved.</i>\n"
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
        draft = self._draft(context)
        msg = update.effective_message
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
                        "<i>Make sure the bot is a member, or use the numeric chat ID.</i>\n"
                        "<i>Try another target or /cancel.</i>",
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
            pending = {"text": label}
            custom_emoji_id = self._custom_emoji_id(msg)
            if custom_emoji_id:
                pending["icon_custom_emoji_id"] = custom_emoji_id
            draft["pending_button"] = pending
            draft["step"] = "button_action"
            await msg.reply_text(
                f'<b>URL/action for "{escape(label)}":</b>\n'
                "<i>Examples: t.me/channel, https://example.com, or a callback action such as pro14 / setnews / rsialert.</i>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return

        if step == "button_action":
            action = self._normalize_button_action(text)
            if not action:
                await msg.reply_text("❌ Send a valid URL, t.me link, @username, or callback action (max 64 UTF-8 bytes).")
                return
            draft["pending_button"].update(action)
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
                draft["plain_text"] = text
            if not html and not has_media:
                await msg.reply_text("❌ Send text, media, or both.")
                return
            draft["step"] = "preview"
            await self._preview(context)

    async def _label_prompt(self, context, chat_id):
        await context.bot.send_message(
            chat_id,
            "<b>Button label:</b> What text should the button show?\n"
            "<i>Custom/animated emoji in the label is preserved as the button icon.</i>",
            parse_mode=ParseMode.HTML,
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
            draft["step"] = "target"
            draft["pending_button"] = None
            await query.answer()
            await context.bot.send_message(
                chat_id,
                "<b>Where to send?</b>\n\n"
                "<i>Enter a chat ID (e.g. -1001234567890) or @username.</i>\n"
                "<i>Type</i> <code>me</code> <i>to send to yourself.</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "setup_media":
            draft["step"] = "media"
            draft["pending_button"] = None
            await query.answer()
            action = "replace the current" if draft["media"] else "add"
            await context.bot.send_message(
                chat_id,
                "<b>Send media:</b> photo, video, GIF, document, audio, voice, or sticker.\n"
                f"<i>This will {action} media.</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        if data in {"setup_addbtn", "setup_newrow"}:
            draft["force_new_row"] = data == "setup_newrow"
            draft["pending_button"] = None
            draft["step"] = "button_label"
            await query.answer("New row — enter label" if data == "setup_newrow" else None)
            await self._label_prompt(context, chat_id)
            return

        if data.startswith("setup_btncolor:"):
            style = data.split(":", 1)[1]
            if draft.get("step") != "button_color" or not draft.get("pending_button"):
                await query.answer("No button waiting for a color.", show_alert=True)
                return
            item = self._add_pending_button(draft, style)
            if not item:
                await query.answer("Button draft is missing.", show_alert=True)
                return
            await query.answer(f'Button "{item["text"]}" added!')
            try:
                if query.message:
                    await context.bot.delete_message(
                        chat_id=query.message.chat_id,
                        message_id=query.message.message_id,
                    )
            except TelegramError:
                pass
            await self._preview(context)
            await context.bot.send_message(
                chat_id,
                f'✅ <b>{escape(item["text"])}</b> added!\nTap <b>➕ Buttons</b> to add another.',
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "setup_clearbtn":
            draft["buttons"] = []
            draft["pending_button"] = None
            draft["force_new_row"] = False
            draft["step"] = "preview"
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
                real_indices = [index for index, row in enumerate(draft["buttons"]) if row]
                real_index = real_indices[visible_index]
            except (ValueError, IndexError):
                await query.answer("Row no longer exists.", show_alert=True)
                return
            del draft["buttons"][real_index]
            draft["buttons"] = self._rows(draft)
            draft["pending_button"] = None
            draft["force_new_row"] = False
            draft["step"] = "preview"
            await query.answer("Removed")
            await self._preview(context)
            return

        await query.answer()

    async def _send(self, target, context: ContextTypes.DEFAULT_TYPE):
        draft = self._draft(context)
        if not draft:
            return

        keyboard = self._message_keyboard(draft)
        html = draft["html"] or ""
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
                        target,
                        html or "\u2063",
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard,
                        disable_web_page_preview=True,
                    )
            else:
                await context.bot.send_message(
                    target,
                    html,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )

            await context.bot.send_message(
                draft["chat_id"],
                f"✅ <b>Sent to</b> <code>{escape(str(target))}</code>",
                parse_mode=ParseMode.HTML,
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
