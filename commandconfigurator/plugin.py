import textwrap

import discord

from dncore.command import oncommand, CommandContext
from dncore.command.errors import CommandUsageError
from dncore.plugin import Plugin
from .errors import CommandMessageError, CommandNotFoundError, CommandInfoError
from .mgrcmd import MyCommandHandler


class CommandConfiguratorPlugin(Plugin):
    def __init__(self):
        self.commands = MyCommandHandler()

    @staticmethod
    def list_all(prefix: str, label: str):
        return textwrap.dedent("""
        コマンド設定
        > {cmd} listcommands
        > {cmd} addcommand (name) [handlerId] [category]
        > {cmd} removecommand (name)
        > {cmd} command (name) info
        > {cmd} command (name) sethandler (handlerId)
        > {cmd} command (name) addalias (alias...)
        > {cmd} command (name) removealias (alias...)
        > {cmd} command (name) setusage (usageText)
        > {cmd} command (name) resetusage
        > {cmd} command (name) setcategory (category)
        > {cmd} command (name) resetcategory
        > {cmd} command (name) test (user)
        
        許可グループ設定
        > {cmd} listgroups
        > {cmd} creategroup (name) [commands...]
        > {cmd} deletegroup (name)
        > {cmd} group (name) info
        > {cmd} group (name) addcommand (command)
        > {cmd} group (name) removecommand (command)
        > {cmd} group (name) adduser (userId)
        > {cmd} group (name) removeuser (userId)
        > {cmd} group (name) addrole (roleId)
        > {cmd} group (name) removerole (roleId)
        
        カテゴリ設定
        > {cmd} listcategories
        > {cmd} addcategory (name) [label] [commands...]
        > {cmd} removecategory (name)
        > {cmd} category (name) info
        > {cmd} category (name) move (number)
        > {cmd} category (name) setlabel (label)
        > {cmd} category (name) addcommand (command)
        > {cmd} category (name) removecommand (command)
        """).format(cmd=prefix + label)

    @oncommand(category="utility")
    async def cmd_cconf(self, ctx: CommandContext):
        """
        {command}
        コマンドの登録や権限設定を操作します

        > 例1 コマンドの登録
        `{command} addCommand (コマンド)`

        > 例2 コマンドを一般許可グループに追加
        `{command} group defaults addCommand (コマンド)`

        > コマンド一覧の表示
        `{command} help`
        """

        if ctx.arguments.get(default="") == "help":
            embed = discord.Embed(
                title=":jigsaw: Command Configurator - 操作一覧 :jigsaw:",
                description=self.list_all(ctx.prefix, ctx.execute_name)
            )
            await ctx.send_info(embed)
            return

        try:
            command, kwargs = self.commands.get_command(ctx)
        except CommandMessageError as e:
            await ctx.send_warn(e.message)
            return
        except CommandInfoError as e:
            if e.command.docs:
                docs = textwrap.dedent(e.command.docs)
                await ctx.client.send_command_usage(ctx, ctx.command, docs)
                return
            raise CommandUsageError() from e

        except CommandNotFoundError as e:
            raise CommandUsageError() from e

        try:
            await command(**kwargs)
        except Exception:
            raise
