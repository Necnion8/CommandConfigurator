import inspect
import types
import typing
from logging import getLogger

import discord

from dncore import DNCoreAPI
from dncore.appconfig import CommandsConfig
from dncore.appconfig.commands import CommandEntry, PermissionGroup, CommandCategory
from dncore.command import CommandHandler, CommandContext, CommandManager, DEFAULT_CATEGORY
from .errors import CommandMessageError, CommandNotFoundError, CommandInfoError

log = getLogger(__name__)  #


class ArgumentParser:
    def parse(self, argument: str):
        raise NotImplemented


class CommandEntryArgument(ArgumentParser):
    def parse(self, name: str) -> tuple[str, CommandEntry]:
        for category in DNCoreAPI.commands().config.categories.values():
            if name.lower() in category.commands:
                return name.lower(), category.commands[name.lower()]
        raise CommandMessageError(f":grey_exclamation: コマンド `{name.lower()}` は定義されていません")


class HandlerArgument(ArgumentParser):
    def parse(self, name: str) -> CommandHandler:
        try:
            return DNCoreAPI.commands().handlers[name.lower()]
        except KeyError:
            raise CommandMessageError(f":grey_exclamation: ハンドラID `{name.lower()}` は登録されていません")


class GroupArgument(ArgumentParser):
    def parse(self, name: str) -> tuple[str, PermissionGroup]:
        try:
            return name.lower(), DNCoreAPI.commands().config.groups[name.lower()]
        except KeyError:
            raise CommandMessageError(f":grey_exclamation: グループ `{name.lower()}` は定義されていません")


class CategoryArgument(ArgumentParser):
    def parse(self, argument: str) -> tuple[str, CommandCategory]:
        try:
            return argument.lower(), DNCoreAPI.commands().config.categories[argument.lower()]
        except KeyError:
            raise CommandMessageError(f":grey_exclamation: カテゴリ `{argument.lower()}` は定義されていません")


class Handler(object):
    handlers = []  # type: list[Handler]

    def __init__(self, *args):
        # self.name = name.lower()
        self.args = args
        self.handler = None
        self.docs = None

    def __call__(self, func):
        self.handler = func
        self.docs = func.__doc__
        self.handlers.append(self)

    @property
    def ignore_args_size(self):
        if self.args:
            typ = self.args[-1]
            return typ is list or typ == list[str]
        return False


class MyCommandHandler(object):
    def __init__(self):
        pass

    def get_command(self, ctx: CommandContext):
        arguments = ctx.arguments

        if not arguments:
            raise CommandNotFoundError()

        handlers = list(Handler.handlers)
        _args = {hdl: [] for hdl in handlers}

        for idx, arg in enumerate(arguments):
            for cmd in list(handlers):
                try:
                    typ = cmd.args[idx]
                except IndexError:
                    if not cmd.ignore_args_size:
                        handlers.remove(cmd)
                    continue

                if isinstance(typ, types.UnionType) and type(None) in typing.get_args(typ):
                    typ = typing.get_args(typ)[0]  # first only

                if isinstance(typ, str) and typ.lower() != arg.lower():
                    handlers.remove(cmd)

                elif isinstance(typ, ArgumentParser):
                    _args[cmd].append(typ.parse(arg))

                elif typ is str:
                    _args[cmd].append(arg)

                elif typ is list or typ == list[str]:
                    _args[cmd].append(arguments[idx:])

        if not handlers:
            raise CommandNotFoundError()

        arg_errors = []
        for cmd in list(handlers):
            for typ in cmd.args[len(arguments):]:
                if isinstance(typ, types.UnionType) and type(None) in typing.get_args(typ):
                    continue
                handlers.remove(cmd)
                arg_errors.append(cmd)
                break

        if not handlers:
            if len(arg_errors) == 1:
                raise CommandInfoError(arg_errors[0])
            raise CommandNotFoundError()

        cmd = handlers[0]
        try:
            kwargs = self.get_handler_params(ctx, cmd.handler, _args[cmd])
        except IndexError:
            raise CommandInfoError(cmd)

        return cmd.handler, kwargs

    def get_handler_params(self, ctx: CommandContext, function, args: list):
        args = list(args)
        parameters = list(inspect.signature(function).parameters.values())
        parameters.pop(0)  # self
        parameters.pop(0)  # ctx: CommandContext

        kwargs = dict(self=self, ctx=ctx)
        for idx, parameter in enumerate(parameters):
            try:
                kwargs[parameter.name] = args.pop(0)
            except IndexError:
                if isinstance(parameter.annotation, types.UnionType) and type(None) in typing.get_args(parameter.annotation):
                    kwargs[parameter.name] = None

                elif parameter.default is not inspect.Signature.empty:
                    kwargs[parameter.name] = parameter.default

                else:
                    log.debug(f"parameters: {parameters}")
                    log.debug(f"parameter: {parameter}")
                    log.debug(f"kwargs: {kwargs}")
                    raise

        return kwargs

    @property
    def cmdmgr(self) -> CommandManager:
        return DNCoreAPI.commands()

    @property
    def cmdconf(self) -> CommandsConfig:
        return DNCoreAPI.commands().config

    @Handler("listCommands")
    async def handler(self, ctx: CommandContext):
        handlers = set(self.cmdmgr.handlers.keys())
        handlers.update(self.cmdmgr.commands.values())
        handlers.discard(None)

        if not handlers:
            await ctx.send_warn(":grey_exclamation: コマンドがありません")
            return

        unused_handlers = sorted(handlers)
        used_handlers = []

        for handler_id in list(unused_handlers):
            names = [name for name, hid in self.cmdmgr.commands.items() if hid == handler_id]
            if names:
                unloaded = handler_id not in self.cmdmgr.handlers
                used_handlers.append(dict(handler_id=handler_id, names=sorted(names), unloaded=unloaded))
                unused_handlers.remove(handler_id)

        lines = []
        for used in used_handlers:
            lines.append("{0}  {2}`({1})`{2}".format(
                used["handler_id"], ", ".join(used["names"]), ["", "~~"][used["unloaded"]]))
        lines.extend(unused_handlers)

        embed = discord.Embed(
            title=":jigsaw: コマンド一覧 :jigsaw:",
            description="\n".join(lines)
        )
        await ctx.send_info(embed)

    @Handler("addCommand", str, str | None, str | None)
    async def handler(self, ctx: CommandContext, name: str, handler_id: str | None, category: str | None):
        """
        {command} addCommand (コマンド名) [ハンドラID] [カテゴリ]

        コマンドを追加登録します。
        ※ ハンドラIDを省略した場合は、
        　 コマンド名に一致するハンドラを1つ選択します
        """
        name = name.lower()
        category_name, category = CategoryArgument().parse(category or DEFAULT_CATEGORY)

        if handler_id is None:
            handler = None
            for hid, handler in self.cmdmgr.handlers.items():
                if handler.name and name == handler.name.lower():
                    break
        else:
            handler = HandlerArgument().parse(handler_id)

        if name in category.commands:
            await ctx.send_warn(":grey_exclamation: 既に追加されています")

        else:
            category.commands[name] = entry = CommandEntry()
            entry.handler = handler.id
            self.cmdmgr.remap(force_save=True)
            await ctx.send_info(f":ok_hand: コマンド **`{name}`** を追加しました ({handler.id})")

    @Handler("removeCommand", CommandEntryArgument())
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandEntry]):
        """
        {command} removeCommand (コマンド名)

        コマンドを削除します
        ※ 別名などのコマンド設定も削除されます
        """
        name, command = name

        removed = 0
        for category in self.cmdconf.categories.values():
            if name in category.commands and category.commands[name].handler:
                category.commands[name].handler = None
                removed += 1

        if not removed:
            await ctx.send_info(":grey_exclamation: コマンド **`{name}`** は設定されていません")

        else:
            self.cmdmgr.remap(force_save=True)
            await ctx.send_info(f":ok_hand: コマンド **`{name}`** を削除しました")

    @Handler("command", CommandEntryArgument())
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandEntry]):
        """
        {command} command (コマンド) info

        コマンド情報を表示します
        """
        name, entry = name

        await ctx.send_info("\n".join([
            f":white_small_square: コマンド: **`{name}`**",
            f":white_small_square: ハンドラ: {entry.handler or ''}",
            f":white_small_square: 別名　　: {', '.join(entry.aliases)}",
            f":white_small_square: 説明文　: {['初期値', '設定済み'][bool(entry.usage)]}"
        ]))

    @Handler("command", CommandEntryArgument(), "info")
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandEntry]):
        """
        {command} command (コマンド) info

        コマンド情報を表示します
        """
        name, entry = name

        await ctx.send_info("\n".join([
            f":white_small_square: コマンド: **`{name}`**",
            f":white_small_square: ハンドラ: {entry.handler or ''}",
            f":white_small_square: 別名　　: {', '.join(entry.aliases)}",
            f":white_small_square: 説明文　: {['初期値', '設定済み'][bool(entry.usage)]}"
        ]))

    @Handler("command", CommandEntryArgument(), "setHandler", HandlerArgument())
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandEntry], handler_id: CommandHandler):
        """
        {command} command (コマンド) setHandler (ハンドラID)

        コマンドの実行元ハンドラを設定します
        """
        name, entry = name
        entry.handler = handler_id.id
        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(f":ok_hand: コマンド **`{name}`** の実行ハンドラを `{entry.handler}` に設定しました")

    @Handler("command", CommandEntryArgument(), "addAlias", list[str])
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandEntry], alias: list[str]):
        """
        {command} command (コマンド) addAlias (別名...)

        コマンドに別名を追加します
        """
        name, entry = name
        if not alias:
            await ctx.send_warn(":grey_exclamation: 追加する別名を指定してください")
            return

        added = 0
        for item in alias:
            if item.lower() not in entry.aliases:
                added += 1
                entry.aliases.append(item.lower())

        if not added:
            await ctx.send_info(":grey_exclamation: 既に追加されています")

        else:
            self.cmdmgr.remap(force_save=True)
            await ctx.send_info(
                f":ok_hand: 別名コマンド **`{alias[0].lower()}`** を追加しました" if added == 1 else
                f":ok_hand: 別名コマンド {added}個 を追加しました"
            )

    @Handler("command", CommandEntryArgument(), "removeAlias", list[str])
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandEntry], alias: list[str]):
        """
        {command} command (コマンド) removeAlias (別名...)

        コマンドの別名を削除します
        """
        name, entry = name
        if not alias:
            await ctx.send_warn(":grey_exclamation: 削除する別名を指定してください")
            return

        removed = 0
        for item in alias:
            if item.lower() in entry.aliases:
                removed += 1
                entry.aliases.remove(item.lower())

        if not removed:
            await ctx.send_info(":grey_exclamation: 指定された別名は削除されませんでした")

        else:
            self.cmdmgr.remap(force_save=True)
            await ctx.send_info(f":ok_hand: 別名コマンド {removed}個 を削除しました")

    @Handler("command", CommandEntryArgument(), "setUsage", str)
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandEntry], usage: str):
        """
        {command} command (コマンド) setUsage (使用法)

        コマンド使用法文を設定します
        """
        name, entry = name

        if entry.usage == usage:
            await ctx.send_warn(":grey_exclamation: 既に同じカスタム使用法文が設定されています")
            return

        entry.usage = usage
        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(":ok_hand: カスタム使用法を設定しました")

    @Handler("command", CommandEntryArgument(), "resetUsage")
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandEntry]):
        """
        {command} command (コマンド) resetUsage

        コマンド使用法文をデフォルトに設定します
        """
        name, entry = name

        if entry.usage is None:
            await ctx.send_warn(":grey_exclamation: カスタム使用法は設定されていません")
            return

        entry.usage = None
        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(":ok_hand: カスタム使用法を削除しました")

    @Handler("command", CommandEntryArgument(), "setCategory", CategoryArgument())
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandEntry], category: tuple[str, CommandCategory]):
        """
        {command} command (コマンド) setCategory (カテゴリ)

        コマンドを指定カテゴリに設定します
        """
        name, entry = name
        category_name, category = category

        if name in category.commands:
            await ctx.send_warn(":grey_exclamation: 既に指定カテゴリに設定されています")
            return

        # delete other
        for _category in self.cmdconf.categories.values():
            _category.commands.pop(name, None)

        category.commands[name] = entry

        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(f":ok_hand: `{category.label or category_name}` カテゴリに設定しました")

    @Handler("command", CommandEntryArgument(), "resetCategory")
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandEntry]):
        """
        {command} command (コマンド) resetCategory

        コマンドをその他カテゴリに設定します
        """
        name, entry = name

        removed = 0
        for category_name, category in self.cmdconf.categories.items():
            if DEFAULT_CATEGORY != category_name and category.commands.pop(name, None):
                removed += 1

        if not removed:
            await ctx.send_warn(":grey_exclamation: カテゴリが設定されていません")
            return

        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(f":ok_hand: その他カテゴリに設定しました")

    @Handler("command", CommandEntryArgument(), "test", str)
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandEntry], user: str):
        """
        {command} command (コマンド) test (ユーザー)

        指定ユーザーがコマンドの実行権限を持っているかテストします
        """
        name, command = name

        user_id = ctx.arguments.get_user(3, default=None)
        if user_id is None:
            await ctx.send_warn(":grey_exclamation: 指定されたユーザーを特定できませんでした。数字IDを指定してください。")
            return

        try:
            user = await ctx.client.fetch_user(user_id)

        except discord.HTTPException:
            await ctx.send_warn(":exclamation: ユーザーが見つかりませんでした")
            return

        command_handler = self.cmdmgr.get_command(name)
        if command_handler is None:
            await ctx.send_warn(":grey_exclamation: コマンドが有効化されていません")
            return

        allowed = ctx.client.allowed(command_handler, user, ctx.guild)
        await ctx.send_info(f":ok: {user} は `{command_handler.name}` コマンドが許可されています" if allowed else
                            f":ng: {user} は `{command_handler.name}` コマンドが許可されていません")

    @Handler("listGroups")
    async def handler(self, ctx: CommandContext):
        """
        {command} listGroups

        グループを一覧します
        """
        groups = self.cmdconf.groups
        if not groups:
            await ctx.send_warn(":grey_exclamation: グループがありません")
            return

        lines = []
        for name, group in groups.items():
            roles = sum(1 for _, _grp in self.cmdconf.roles.items() if _grp == name)

            line = f"・{name}"
            line += f" (コマンド: 全て" if group.allowed_all() else f" (コマンド: {len(group.commands)}"
            line += f"、ユーザー: {len(group.users)}" if group.users else ""
            line += f"、役職: {roles})" if roles else ")"
            lines.append(line)

        await ctx.send_info(discord.Embed(
            title=":jigsaw: グループ一覧 :jigsaw:",
            description="\n".join(lines)
        ))

    @Handler("createGroup", str, list[str] | None)
    async def handler(self, ctx: CommandContext, name: str, allowed_commands: list[str] | None):
        """
        {command} createGroup (グループ名) [許可コマンド...]

        許可グループを作成します
        """
        name = name.lower()

        if name in self.cmdconf.groups:
            await ctx.send_warn(":grey_exclamation: 既に存在するグループ名です")
            return

        group = PermissionGroup()
        if allowed_commands:
            group.commands.extend(allowed_commands)

        self.cmdconf.groups[name] = group
        self.cmdmgr.remap(force_save=True)

        await ctx.send_info(f":ok_hand: 権限グループ `{name}` を作成しました")

    @Handler("deleteGroup", GroupArgument())
    async def handler(self, ctx: CommandContext, name: tuple[str, PermissionGroup]):
        """
        {command} deleteGroup (グループ名)

        許可グループを削除します
        """
        name, group = name

        self.cmdconf.groups.pop(name)
        self.cmdmgr.remap(force_save=True)

        await ctx.send_info(f":ok_hand: 権限グループ `{name}` を削除しました")

    @Handler("group", GroupArgument())
    async def handler(self, ctx: CommandContext, name: tuple[str, PermissionGroup]):
        """
        {command} group (グループ) info

        グループの情報を表示します
        """
        name, group = name

        if group.allowed_all():
            command_lines = [f":white_small_square: 許可コマンド:\n　全て許可"]
        else:
            command_lines = [f":white_small_square: 許可コマンド({len(group.commands)}):\n　" + ", ".join(group.commands)]

        users = [f"<@{user_id}>" for user_id in group.users]
        roles = [f"<@&{str(role_id)}>" for role_id, grp in self.cmdconf.roles.items() if grp == name and role_id.isdigit()]

        user_lines = [f":white_small_square: 許可ユーザー({len(group.users)}):\n　" + ", ".join(users)]
        role_lines = [f":white_small_square: 許可役職({len(roles)}):\n　" + ", ".join(roles)]
        await ctx.send_info("\n".join([
            f":white_small_square: グループ: **`{name}`**", *command_lines, *user_lines, *role_lines,
        ]))

    @Handler("group", GroupArgument(), "info")
    async def handler(self, ctx: CommandContext, name: tuple[str, PermissionGroup]):
        """
        {command} group (グループ) info

        グループの情報を表示します
        """
        name, group = name

        if group.allowed_all():
            command_lines = [f":white_small_square: 許可コマンド:\n　全て許可"]
        else:
            command_lines = [f":white_small_square: 許可コマンド({len(group.commands)}):\n　" + ", ".join(group.commands)]

        users = [f"<@{user_id}>" for user_id in group.users]
        roles = [f"<@&{str(role_id)}>" for role_id, grp in self.cmdconf.roles.items() if grp == name and role_id.isdigit()]

        user_lines = [f":white_small_square: 許可ユーザー({len(group.users)}):\n　" + ", ".join(users)]
        role_lines = [f":white_small_square: 許可役職({len(roles)}):\n　" + ", ".join(roles)]
        await ctx.send_info("\n".join([
            f":white_small_square: グループ: **`{name}`**", *command_lines, *user_lines, *role_lines,
        ]))

    @Handler("group", GroupArgument(), "addCommand", CommandEntryArgument())
    async def handler(self, ctx: CommandContext, name: tuple[str, PermissionGroup], command: tuple[str, CommandEntry]):
        """
        {command} group (グループ) addCommand (コマンド...)

        許可するコマンドをグループに追加します
        """
        name, group = name
        command_name, command = command

        if group.allowed_all():
            await ctx.send_warn(":grey_exclamation: 全許可グループのためコマンドを指定できません")
            return

        elif command_name in group.commands:
            await ctx.send_warn(f":grey_exclamation: 既に `{command_name}` コマンドは許可されています")
            return

        group.commands.append(command_name)
        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(f":ok_hand: `{name}` グループの `{command_name}` コマンドを許可しました")

    @Handler("group", GroupArgument(), "removeCommand", CommandEntryArgument())
    async def handler(self, ctx: CommandContext, name: tuple[str, PermissionGroup], command: tuple[str, CommandEntry]):
        """
        {command} group (グループ) removeCommand (コマンド)

        許可されているコマンドをグループから削除します
        """
        name, group = name
        command_name, command = command

        if group.allowed_all():
            await ctx.send_warn(":grey_exclamation: 全許可グループのためコマンドを指定できません")
            return

        elif command_name not in group.commands:
            await ctx.send_warn(f":grey_exclamation: `{command_name}` コマンドは許可されていません")
            return

        group.commands.remove(command_name)
        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(f":ok_hand: `{name}` グループの `{command_name}` コマンドを剝奪しました")

    @Handler("group", GroupArgument(), "addUser", str)
    async def handler(self, ctx: CommandContext, name: tuple[str, PermissionGroup], user: str):
        """
        {command} group (グループ) addUser (ユーザー)

        指定ユーザーをグループに追加します
        """
        name, group = name

        user = ctx.arguments.get_user(3, default=None)
        if user is None:
            await ctx.send_warn(":grey_exclamation: 指定されたユーザーを特定できませんでした。数字IDを指定してください。")
            return

        if user in group.users:
            await ctx.send_warn(f":grey_exclamation: 既にグループに設定されています")
            return

        group.users.append(user)
        self.cmdmgr.remap(force_save=True)

        try:
            user_name = ctx.client.cached_users[user]
        except KeyError:
            user_name = str(user)

        await ctx.send_info(f":ok_hand: `{name}` グループにユーザー `{user_name}` を追加しました")

    @Handler("group", GroupArgument(), "removeUser", str)
    async def handler(self, ctx: CommandContext, name: tuple[str, PermissionGroup], user: str):
        """
        {command} group (グループ) removeUser (ユーザー)

        指定ユーザーをグループから削除します
        """
        name, group = name

        user = ctx.arguments.get_user(3, default=None)
        if user is None:
            await ctx.send_warn(":grey_exclamation: 指定されたユーザーを特定できませんでした。数字IDを指定してください。")
            return

        if user not in group.users:
            await ctx.send_warn(f":grey_exclamation: 指定されたユーザーは所属していません")
            return

        group.users.remove(user)
        self.cmdmgr.remap(force_save=True)

        try:
            user_name = ctx.client.cached_users[user]
        except KeyError:
            user_name = str(user)

        await ctx.send_info(f":ok_hand: `{name}` グループのユーザー `{user_name}` を削除しました")

    @Handler("group", GroupArgument(), "addRole", str)
    async def handler(self, ctx: CommandContext, name: tuple[str, PermissionGroup], role: str):
        """
        {command} group (グループ) addRole (役職)

        指定役職をグループに設定します
        """
        name, group = name

        role = ctx.arguments.get_role(3, default=None)
        if role is None:
            await ctx.send_warn(":grey_exclamation: 指定された役職を特定できませんでした。数字IDを指定してください。")
            return

        roles = self.cmdconf.roles  # role: groupName
        if roles.get(str(role)) == name:
            await ctx.send_warn(f":grey_exclamation: 既に設定されています")
            return

        self.cmdconf.roles[str(role)] = name
        self.cmdmgr.remap(force_save=True)

        await ctx.send_info(f":ok_hand: 役職 `{role}` を `{name}` グループに割り当てました")

    @Handler("group", GroupArgument(), "removeRole", str)
    async def handler(self, ctx: CommandContext, name: tuple[str, PermissionGroup], role: str):
        """
        {command} group (グループ) removeRole (役職)

        指定役職のグループを削除します
        """
        name, group = name

        role = ctx.arguments.get_role(3, default=None)
        if role is None:
            await ctx.send_warn(":grey_exclamation: 指定された役職を特定できませんでした。数字IDを指定してください。")
            return

        roles = self.cmdconf.roles  # role: groupName
        if roles.get(str(role)) != name:
            await ctx.send_warn(f":grey_exclamation: 設定されていません")
            return

        self.cmdconf.roles.pop(str(role))
        self.cmdmgr.remap(force_save=True)

        await ctx.send_info(f":ok_hand: 役職 `{role}` のグループ割り当てを解除しました")

    @Handler("listCategories")
    async def handle(self, ctx: CommandContext):
        """
        {command} listCategories

        カテゴリを一覧します
        """
        categories = self.cmdconf.categories
        if not categories:
            await ctx.send_warn(":grey_exclamation: カテゴリがありません")
            return

        lines = []
        for name, category in categories.items():
            line = f"・{name}"
            if category.label:
                line += f" - {category.label}"
            line += f"  (コマンド: {len(category.commands)})"
            lines.append(line)

        await ctx.send_info(discord.Embed(
            title=":jigsaw: カテゴリ一覧 :jigsaw:",
            description="\n".join(lines)
        ))

    @Handler("addCategory", str, str | None, list[str] | None)
    async def handler(self, ctx: CommandContext, name: str, label: str | None, commands: list[str] | None):
        """
        {command} addCategory (カテゴリ名) [表示名] [コマンド...]

        カテゴリを作成します
        """
        name = name.lower()
        if name in self.cmdconf.categories:
            await ctx.send_warn(f":grey_exclamation: カテゴリ `{name}` は既に存在します")
            return

        category = CommandCategory(label=label)
        added_commands = 0

        if commands:
            for cmd_name in commands:
                for _category in self.cmdconf.categories.values():
                    cmd_entry = _category.commands.pop(cmd_name.lower(), None)

                    if cmd_entry is None:
                        cmd_entry = CommandEntry()
                    else:
                        added_commands += 1

                    category.commands[cmd_name.lower()] = cmd_entry

        self.cmdconf.categories[name] = category
        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(f":ok_hand: カテゴリ `{name}` を作成しました" +
                            (f" (コマンド: {added_commands}個)" if added_commands else ""))

    @Handler("removeCategory", CategoryArgument())
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandCategory]):
        """
        {command} removeCategory (カテゴリ名)

        カテゴリを削除します
        ※ 設定されていたコマンドはその他カテゴリに移動されます
        """
        name, category = name

        if name == DEFAULT_CATEGORY:
            await ctx.send_warn(":grey_exclamation: デフォルトカテゴリは削除できません")
            return

        if DEFAULT_CATEGORY in self.cmdconf.categories:
            self.cmdconf.categories[DEFAULT_CATEGORY].commands.update(category.commands)

        self.cmdconf.categories.pop(name)
        self.cmdmgr.remap(force_save=True)

        await ctx.send_info(f":ok_hand: カテゴリ `{name}` を削除しました")

    @Handler("category", CategoryArgument())
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandCategory]):
        """
        {command} category (カテゴリ) info

        カテゴリの情報を表示します
        """
        name, category = name

        await ctx.send_info("\n".join([
            f":white_small_square: カテゴリ: **`{name}`**" + (f" (表示名: {category.label})" if category.label else ""),
            f":white_small_square: コマンド({len(category.commands)}):\n　" + ", ".join(category.commands.keys()),
        ]))

    @Handler("category", CategoryArgument(), "info")
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandCategory]):
        """
        {command} category (カテゴリ) info

        カテゴリの情報を表示します
        """
        name, category = name

        await ctx.send_info("\n".join([
            f":white_small_square: カテゴリ: **`{name}`**" + (f" (表示名: {category.label})" if category.label else ""),
            f":white_small_square: コマンド({len(category.commands)}):\n　" + ", ".join(category.commands.keys()),
        ]))

    @Handler("category", CategoryArgument(), "move", str)
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandCategory], number: str):
        """
        {command} category (カテゴリ) move (番号)

        グループを指定番号に並び替えます
        """
        name, category = name

        try:
            number = int(number)
        except ValueError:
            await ctx.send_warn(":grey_exclamation: 数値を指定してください")
            return

        self.cmdconf.categories.pop(name)
        categories = list(self.cmdconf.categories.items())
        index = max(0, min(number - 1, len(categories)))

        categories.insert(index, (name, category))
        self.cmdconf.categories.clear()
        self.cmdconf.categories.update(categories)

        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(f":ok_hand: カテゴリ `{name}` を**{index+1}**番目に移動しました")

    @Handler("category", CategoryArgument(), "setLabel", str)
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandCategory], label: str):
        """
        {command} category (カテゴリ) setLabel (表示名)

        カテゴリ表示名を設定します
        """
        name, category = name

        if category.label == label:
            await ctx.send_warn(":grey_exclamation: 既に表示名が設定されています")
            return

        category.label = label
        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(f":ok_hand: カテゴリ `{name}` の表示名を {label} に設定しました")

    @Handler("category", CategoryArgument(), "addCommand", CommandEntryArgument())
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandCategory], command: tuple[str, CommandEntry]):
        """
        {command} category (カテゴリ) addCommand (コマンド...)

        カテゴリにコマンドを追加します
        """
        name, category = name
        command_name, command = command

        if command_name in category.commands:
            await ctx.send_warn(":grey_exclamation: 既に設定されています")
            return

        # delete other
        for _category in self.cmdconf.categories.values():
            _category.commands.pop(name, None)

        category.commands[command_name] = command

        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(f":ok_hand: `{command_name}` コマンドを追加しました")

    @Handler("category", CategoryArgument(), "removeCommand", str)
    async def handler(self, ctx: CommandContext, name: tuple[str, CommandCategory], command: str):
        """
        {command} category (カテゴリ) removeCommand (コマンド)

        カテゴリからコマンドを削除し、その他カテゴリに移動します
        """
        name, category = name
        command = command.lower()

        if command not in category.commands:
            await ctx.send_warn(":grey_exclamation: コマンドは設定されていません")
            return

        if name == DEFAULT_CATEGORY:
            await ctx.send_warn(":grey_exclamation: デフォルトカテゴリからは削除できません")
            return

        cmd_entry = category.commands.pop(command)

        if cmd_entry and DEFAULT_CATEGORY in self.cmdconf.categories:
            self.cmdconf.categories[DEFAULT_CATEGORY].commands[command] = cmd_entry

        self.cmdmgr.remap(force_save=True)
        await ctx.send_info(f":ok_hand: `{command}` コマンドをカテゴリから削除しました")
