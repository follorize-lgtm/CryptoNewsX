from html import escape

from telegram import BotCommand, BotCommandScopeChat
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError

from setup_builder import OwnerSetupBuilder


class MultiOwnerSetupBuilder(OwnerSetupBuilder):
    """Wabble-style /setup restricted to configured owners in private DMs."""

    def __init__(self, owner_ids):
        super().__init__(None)
        self.owner_ids = frozenset(int(owner_id) for owner_id in owner_ids)

    async def install_owner_menu(self, app):
        # Never expose /setup in the public/default bot command menu.
        try:
            await app.bot.delete_my_commands()
        except TelegramError:
            pass

        # Only configured owners get /setup in their private-chat command menu.
        for owner_id in self.owner_ids:
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
            and user.id in self.owner_ids
            and chat.type == ChatType.PRIVATE
        )

    async def start(self, update, context):
        if not self._owner_private(update):
            return
        context.user_data[self.KEY] = self._new(update.effective_chat.id)
        await update.effective_message.reply_text(
            "🔥 <b>MisuCreate</b>\n\n"
            "Send me the content — text, photo, video, GIF, document, audio, or all at once.\n"
            "<i>/cancel to abort</i>",
            parse_mode=ParseMode.HTML,
        )

    async def cancel(self, update, context):
        if not self._owner_private(update):
            return
        if context.user_data.pop(self.KEY, None) is not None:
            await update.effective_message.reply_text(
                "❌ <b>MisuCreate cancelled.</b>", parse_mode=ParseMode.HTML
            )

    def _preview_text(self, draft):
        # Keep the original Wabble/MisuCreate branding in the preview.
        return super()._preview_text(draft).replace(
            "<b>Setup — Preview</b>", "<b>MisuCreate — Preview</b>", 1
        )

    async def _label_prompt(self, context, chat_id):
        await context.bot.send_message(
            chat_id,
            "🔘 <b>Button label:</b> What text should the button show?",
            parse_mode=ParseMode.HTML,
        )

    async def callback(self, update, context):
        query = update.callback_query
        if query is not None and (query.data or "") == "setup_cancel":
            if not self._owner_private(update):
                await query.answer("Owner only.", show_alert=True)
                return
            context.user_data.pop(self.KEY, None)
            await query.answer("Cancelled")
            try:
                await query.edit_message_text(
                    "❌ <b>MisuCreate cancelled.</b>", parse_mode=ParseMode.HTML
                )
            except TelegramError:
                pass
            return
        await super().callback(update, context)

    async def input(self, update, context):
        # Match Wabble's exact button-label -> URL/action prompt while keeping
        # the base builder's media, formatting, custom-emoji and send logic.
        draft = self._draft(context)
        msg = update.effective_message
        if (
            self._owner_private(update)
            and draft
            and msg is not None
            and draft.get("step") == "button_label"
        ):
            label = (msg.text or msg.caption or "").strip()
            if not label:
                await msg.reply_text("Send the button label as plain text.")
                return

            pending = {"text": label}
            custom_emoji_id = self._custom_emoji_id(msg)
            if custom_emoji_id:
                pending["icon_custom_emoji_id"] = custom_emoji_id

            draft["pending_button"] = pending
            draft["step"] = "button_action"
            await msg.reply_text(
                f'URL/action for "<b>{escape(label)}</b>":\n'
                "<i>e.g. <code>t.me/wabblenewsbot</code>, "
                "<code>https://example.com</code>, <code>pro14</code> for Premium trial, "
                "<code>setnews</code> for news setup, or <code>rsialert</code> for RSI setup</i>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return

        await super().input(update, context)
