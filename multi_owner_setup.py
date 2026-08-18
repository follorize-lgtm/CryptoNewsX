from telegram import BotCommand, BotCommandScopeChat
from telegram.constants import ChatType
from telegram.error import TelegramError

from setup_builder import OwnerSetupBuilder


class MultiOwnerSetupBuilder(OwnerSetupBuilder):
    """Owner setup builder that authorizes any configured Telegram user ID."""

    def __init__(self, owner_ids):
        super().__init__(None)
        self.owner_ids = frozenset(int(owner_id) for owner_id in owner_ids)

    async def install_owner_menu(self, app):
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
