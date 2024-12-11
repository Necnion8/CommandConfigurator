from typing import TYPE_CHECKING

from dncore.command.errors import CommandError

if TYPE_CHECKING:
    from .mgrcmd import Handler


class CommandMessageError(CommandError):
    def __init__(self, message: str):
        self.message = message


class CommandNotFoundError(CommandError):
    pass


class CommandInfoError(CommandError):
    def __init__(self, command: "Handler"):
        self.command = command
