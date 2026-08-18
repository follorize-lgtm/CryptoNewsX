from telegram import BotCommand, BotCommandScopeChat
from telegram.error import TelegramError

from setup_builder import OwnerSetupBuilder


class MultiOwnerSetupBuilder(OwnerSetupBuilder):
    """Crypto News /setup restricted to configured owners in private DMs."""

    def __init__(self, owner_ids):
        super().__init__(None)
        self.owner_ids = frozenset(int(owner_id) for owner_id in owner_ids)

    async def install_owner_menu(self, app):
        # Remove /setup from the public/default command menu without deleting
        # unrelated public commands the bot may already expose.
        try:
            public_commands = list(await app.bot.get_my_commands())
            public_without_setup = [
                command for command in public_commands if command.command != "setup"
            ]
            if len(public_without_setup) != len(public_commands):
                if public_without_setup:
                    await app.bot.set_my_commands(public_without_setup)
                else:
                    await app.bot.delete_my_commands()
        except TelegramError:
            pass

        # Add /setup only to each configured owner's private-chat command scope.
        # Preserve any other owner-scoped commands already registered there.
        for owner_id in self.owner_ids:
            scope = BotCommandScopeChat(chat_id=owner_id)
            try:
                owner_commands = list(await app.bot.get_my_commands(scope=scope))
                owner_commands = [
                    command for command in owner_commands if command.command != "setup"
                ]
                owner_commands.append(BotCommand("setup", "Crypto News message builder"))
                await app.bot.set_my_commands(owner_commands, scope=scope)
            except TelegramError:
                pass
