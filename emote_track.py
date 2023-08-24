from typing import Callable, Coroutine, Dict, List, Tuple, overload
import asyncio
import inspect
import os
import re
import time

import _discord as discord
import sql


TOKEN = ""
IS_USER = False
PREFIX = "@@"


class EmoteTracker:
    """
    Used for tracking usage of custom emotes on the server.

    Parameters
    ----------
    - limit:    `int`     - How many of top emotes to display when asking for statistics.
    - days:     `int`     - How many days to use for last {days} days statistics.
    - sql_manager:  `Manager` - SQL sql_manager for communicating with the database.
    - dc_client: `Client` - Discord client object for interacting with discord API.
    """
    def __init__(self, days: int, sql_manager: sql.Manager, dc_client: discord.Client):
        self.days_to_use: int = days #: How many days to use for last {days} days statistics.
        self.sql_manager: sql.Manager = sql_manager #: SQL sql_manager for communicating with the database.
        self.dc_client: discord.Client = dc_client  #: Discord client object for interacting with discord API.
        self.history_cache: Dict[int, List[Tuple[int, int]]] = {}
        """
        History cache dictionary which's keys are user snowflakes.
        For values it contains lists that contain tuples of message_snowflake and reaction_snowflake
        """

    def get_message_emotes(self, message: discord.Message, duplicates: bool = False) -> list:
        """
        Parses all the emotes inside the message but only if emote is inside the server.

        Parameters:
        -----------
        - message:    `discord.Message`  - The message object
        - duplicates: `bool` - Allow duplicated emotes in the returned list.
        """
        proccessed_ids = []
        emotes = []
        guild_emotes_snowflakes = [x.id for x in message.guild.emojis] # Only allow emotes from this server
                                
        for emote in re.findall(r"<:\w*:\d*>", message.content):
            id = int(re.search(r"(?<=:)\d.*(?=>)",emote).group(0))
            if (duplicates or id not in proccessed_ids) and id in guild_emotes_snowflakes: # If duplicates is False (not allowed) shortcircuit evaliaton for in check.
                emotes.append(
                    {
                        "name" : re.search(r"(?<=<:).*(?=:)", emote).group(0),
                        "snowflake" : id
                    }
                )
                proccessed_ids.append(id)

        return emotes

    @overload
    async def proccess(self, reaction: discord.RawReactionActionEvent):
        """
        Logs the reaction with emote into the database.
        
        Attributes
        -----------
        reaction: discord.RawReactionActionEvent - The discord reaction object
        """
        ...

    @overload
    async def proccess(self, message: discord.Message):
        """
        Parses emotes from a message and logs the event into the database.
        If the command !emote_usage <emote> was used, then it doesn't log into the database
        and instead just returns the top 10 emotes if <emote> was not supplied or returns
        the statistics for <emote> if it was supplied.

        Attributes
        -----------
        message: discord.Message - The discord message objects that represents the message an user has sent.
        """
        ...

    async def proccess(self, *,message: discord.Message = None, reaction: discord.RawReactionActionEvent=None):
        if message is not None:
            emotes = self.get_message_emotes(message)
            if emotes:
                self.sql_manager.insert_emote_log(emotes, message.guild)

        elif reaction is not None:
            self.history_cache: Dict[int, List[Tuple[int, int]]]
            """
            History cache dictionary which's keys are user snowflakes.
            For values it contains lists that contain tuples of message_snowflake and reaction_snowflake
            """
            emote_id = reaction.emoji.id
            message = self.dc_client.get_message(reaction.message_id)
            if message is None:
                return

            guild = message.channel.guild
            guild_emoji_ids = [x.id for x in guild.emojis]
            if emote_id not in guild_emoji_ids:
                return

            if reaction.user_id not in self.history_cache:
                self.history_cache[reaction.user_id] = []

            user_history = self.history_cache[reaction.user_id]
            history_tuple = (reaction.message_id, reaction.emoji.id)

            if history_tuple not in user_history: # This emote was not used on the same message (atlest not before reacting to 100 different messages)
                # Only track 100 reactions for each user to avoid memory overflows
                if len(user_history) == 100:
                    user_history.pop(0)

                user_history.append(history_tuple)
                self.sql_manager.insert_emote_log([{"name": reaction.emoji.name, "snowflake" : reaction.emoji.id}], self.dc_client.get_guild(reaction.guild_id))


class CommandProxy:
    def __init__(self, name: str, *args, **kwargs) -> None:
        self.name = name
        self.args = args
        self.kwargs = kwargs


class CommandHandler:
    def __init__(self, func: Callable, cooldown: int) -> None:
        self.func = func
        self.cooldown = cooldown
    

class Bot(discord.Client):
    """
    ~ inherited class ~
    @Info: Used for communicating with discord"""

    def __init__(self, *args, **kwargs):
        args = list(args)
        self.prefix = args.pop(0)
        self.handlers: Dict[str, CommandHandler] = {}
        self.command_uses: Dict[discord.User, Dict[CommandHandler, int]] = {}
        """
        Dictionary for stroing information about last command usage for each user.
        Key is the user and the value is another dictionary who's key is the command handler and value the epox timestamp of last usage.
        """
        super().__init__(*args, **kwargs)

    async def on_ready(self):
        print(f"Logged in: {self.user}")
        await self.change_presence(activity=discord.Game(name=f"{self.prefix}help"))

    async def on_message(self, message: discord.Message):
        if self.user == message.author:
            return
           
        if message.content.startswith(self.prefix):
            command = self.transform_to_command(message)
            author = message.author
            handler = self.handlers[command.name]
            if author not in self.command_uses:
                self.command_uses[author] = {cmd: 0 for cmd in self.handlers.values()}

            user_usage = self.command_uses[author]
            elapsed = time.time() - user_usage[handler]
            if elapsed > handler.cooldown:
                prev = user_usage[handler]
                try:
                    user_usage[handler] = time.time()
                    await self.invoke_handler(message, command)
                except Exception as ex:
                    user_usage[handler] = prev
                    await message.reply(f"Malformed command!\nTraceback:\n```\n{ex}\n```")
        else:
            await emote_tracker.proccess(message=message)
    
    def register_command(self, command: str, cooldown: int=10):
        """
        Decorator that register the function as a command handler

        Parameters
        --------------
        command: str 
            The command that invokes the function
        cooldown: int
            The cooldown in seconds
        """
        def decor_register_command(fnc: Coroutine):
            self.handlers[command] = CommandHandler(fnc, cooldown)
            return fnc
   
        return decor_register_command
    
    async def invoke_handler(self, message: discord.Message, command: CommandProxy):
        if command.name in self.handlers:
            await self.handlers[command.name].func(message, *command.args, **command.kwargs)
        else:
            await message.reply(f"Unknown command ``{command.name}``")
    
    def transform_value(self, value: str):
        if value == "True":
            return True
        if value == "False":
            return False
        if re.search(r"^[0-9]+(?!.)", value, re.MULTILINE) is not None:
            return int(value)
        if re.search(r"^[0-9]+\.[0-9]+(?!.)", value) is not None:
            return float(value)
        if re.search(r"\[.*\]", value) is not None:
            return [self.transform_value(val.strip()) for val in value.lstrip("[").rstrip("]").split(",")]

        return value.strip('"')

    def transform_to_command(self, message: discord.Message) -> CommandProxy:
        content = message.content
        command_name = re.search(f"^{self.prefix}\\w+", content)
        if command_name is None:
            return

        command_name = command_name.group(0).lower()
        content = content.lstrip(command_name).strip()
        command_name = command_name.lstrip(self.prefix)
        kwargs_search: List[str] = re.findall(r'--\w+ \w+|--\w+ ".*?"|--\w+ \[.*\]', content)
        kwargs = {}
        for kwarg in kwargs_search:
            content = content.replace(kwarg, "")
            kwarg = kwarg.lstrip("--").split(' ', 1)
            kwargs[kwarg[0]] = self.transform_value(kwarg[1])
        
        args = [self.transform_value(x[0] if x[0] != '' else x[1]) for x in re.findall(r'(\b(?<!")[.\w]+(?!")\b)|(".+?")', content)]
        
        return CommandProxy(command_name, *args, **kwargs)
    
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await emote_tracker.proccess(reaction=payload)

        


intents = discord.Intents.default()
intents.message_content=True
intents.messages=True
sql_manager = sql.Manager("emotes.db")
dc_client = Bot(PREFIX, intents=intents)
emote_tracker = EmoteTracker(30, sql_manager, dc_client)

async def main():
    sql_manager.start()
    asyncio.create_task(dc_client.start(TOKEN, bot=not IS_USER))


last_stamp = 0
@dc_client.event
async def on_voice_state_update(member, before , after):
    global last_stamp
    if after.channel is not None and after.channel.id == 640135183382872075 and (before.channel is None or before.channel.id != 640135183382872075) and member != dc_client.user:
        if time.time() - last_stamp > 120 and len(after.channel.members) in {2, 3}:
            last_stamp = time.time()
            voice_client = await after.channel.connect()
            stream = discord.FFmpegPCMAudio("/home/davidhozic/Projects/Discord-Emote-Usage/makechildren.mp3", options="-loglevel fatal")
            voice_client.play(stream)

            while voice_client.is_playing():
                await asyncio.sleep(1)

            await voice_client.disconnect()


# Command handlers
@dc_client.register_command("help")
async def help(message: discord.Message, *args):
    """
    Returns help.

    Parameters
    -----------------
    commands: Sequence
        Sequence of commands to display help for (Leave empty for nothing)
    """
    response = ""
    if not len(args):
        args = dc_client.handlers.keys()
        response = (
f"""\
**__Tracker Bot__**

Usage: {dc_client.prefix}command <arguments>
Positional arguments, seperated by space: arg1 arg2 arg3
Keyword arguments: --name1 value1 --name2 value2

__Available commands:__
"""
)
    for name in args:
        if name in dc_client.handlers:
            response += f"**{name}** (Cooldown: {dc_client.handlers[name].cooldown}):\n```\n"  + "\n".join(inspect.cleandoc(dc_client.handlers[name].func.__doc__).splitlines(keepends=False)) + "```\n"

    await message.reply(response)


@dc_client.register_command("usage")
async def emote_usage(message: discord.Message, emote=None, ascending=False, columns=3, limit=40):
    """
    Returns a list of emotes and their usage.
    
    Parameters
    --------------
    emote: str
        Returns the usage for only this emote (Returns all emotes if not given).
    ascending: bool 
        (True/False) Order by usage in ascending order (those with lower usage first).
    columns: int
        How many emotes to print in single row
    limit: int
        How many emotes to display
    """
    if limit > 40:
        raise ValueError("'limit' parameter has a hard limit of 40!")

    if emote is not None:
        match_ = re.search(r"<:\w+:[0-9]+>", emote)
        if match_ is not None:
            emote = int(re.search(r"(?<=:)[0-9]+(?=>)", match_.group(0)).group(0))

    content = ""
    contents = []
    for name, snowflake, total_count, count30day in sql_manager.statistics(message.guild.id, limit, emote_tracker.days_to_use, emote, ascending):
        contents.append("<:{}:{}> `{:5d}` `{:5d}`"
            .format(
                name,
                snowflake,
                total_count,
                count30day
            )
        )  

    content  = "\n".join("**|**".join(contents[i*columns:(i+1)*columns]) for i in range(len(contents)//columns+1))
    if content:
        content = "Emote, Total count, Last 30 days\n" + content
    else:
        content = "Ni nobenih podatkov!"

    await message.reply(content)

@dc_client.register_command("reboot")
async def reboot(message: discord.Message, time: int):
    """
    The command reboots the bot.
    
    Parameters
    --------------
    time: int
        Time in seconds after which to reboot.
    """
    if message.author.id == 145196308985020416:
        await asyncio.sleep(time)
        os.system("reboot")
    else:
        reply = await message.reply("You are not authorized to perform this action")
        await asyncio.sleep(5)
        await reply.delete()


@dc_client.register_command("clean")
async def clean(message: discord.Message, limit: int):
    """
    The command clears bot's messages
    
    Parameters
    --------------
    limit: int
        How many messages to delete
    """
    if message.author.id == 145196308985020416 or message.author.guild_permissions.administrator:
        if limit > 100:
            await message.reply("Max limit is 100")
        else:
            try:
                async for message in message.channel.history(limit=limit):
                    if message.author.id == dc_client.user.id:
                        await message.delete()
            except Exception as ex:
                print(ex)
    else:
        await message.reply("You are not authorized to perform this action")


@dc_client.register_command("mono")
async def mono(message: discord.Message, content: str=None, message_id: int=None):
    """
    Resends the text in monospace.
    
    Parameters
    -------------------
    content: Optional[str]
        The content to resend.
    message_id: Optional[int]
        The snowflake id of the message to get content from.
    """
    if message_id is not None:
        if content is not None:
            raise ValueError("message_id was provided, cannot use both parameters at once.")
        
        _message = dc_client.get_message(message_id)
        if _message is None:
            raise ValueError("Invalid message_id provided (cannot obtain message from id)")
        
        content = _message.content
    
    content = content.replace('`', '')
    await message.reply(f"```\n{content}\n```")



try:
    loop = asyncio.get_event_loop()
    loop.create_task(main())
    loop.run_forever()
except:
    exit(0)