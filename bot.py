import discord
from discord import ChannelType
from discord.ext import commands, menus
import config
from cogs.help import HelpCommand
from utils.jsonfile import JSONList, JSONDict
from utils.context import Context
from collections import Counter
import datetime
from contextlib import suppress
import traceback

extensions = (
    "cogs.settings",
    "cogs.core",
    "cogs.voice",
    "cogs.api",
)


class Bot(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix=lambda b, m: b.prefixes.get(str(m.guild.id), 'dv!'),
            help_command=HelpCommand(),
            case_insensitive=True,
            owner_id=config.owner_id,
            activity=discord.Activity(type=discord.ActivityType.watching, name='dv!')
        )
        self.launched_at = None
        self.client_id = config.client_id

        self.prefixes = JSONDict('data/prefixes.json')  # Mapping[guild_id, prefix]
        self.bad_words = JSONDict('data/bad_words.json')  # Mapping[guild_id, List[str]]
        self.configs = JSONDict('data/configs.json')  # Mapping[channel_id, config]
        self.channels = JSONList('data/channels.json')  # List[channel_id]
        self.blacklist = JSONList('data/blacklist.json')  # List[user_id]
        self.channel_indexes = JSONDict('data/channel_indexes.json')  # List[channel_indexes]

        self.voice_spam_control = commands.CooldownMapping.from_cooldown(3, 5, commands.BucketType.user)
        self.voice_spam_counter = Counter()

        self.text_spam_control = commands.CooldownMapping.from_cooldown(8, 10, commands.BucketType.user)
        self.text_spam_counter = Counter()

        for extension in extensions:
            self.load_extension(extension)

    async def on_ready(self):
        if self.launched_at is None:
            guilds = self.guilds
            for guild in guilds:
                voice_channels = guild.voice_channels
                for ch in voice_channels:
                    await self.on_voice_leave(guild.me, ch)
            self.channel_indexes.clear()
            await self.channel_indexes.save()
            self.launched_at = datetime.datetime.utcnow()
            print('Logged in as', self.user)

    async def on_message(self, message):
        if message.guild is None:
            return
        await self.process_commands(message)

    async def on_message_edit(self, before, after):
        if before.content != after.content:
            await self.on_message(after)

    async def process_commands(self, message):
        ctx = await self.get_context(message, cls=Context)
        if ctx.command is None:
            return
        if ctx.author.id in self.blacklist:
            return
        if not ctx.channel.permissions_for(ctx.guild.me).send_messages:
            return
        bucket = self.text_spam_control.get_bucket(message)
        current = message.created_at.replace(tzinfo=datetime.timezone.utc).timestamp()
        retry_after = bucket.update_rate_limit(current)
        if retry_after:
            self.text_spam_counter[ctx.author.id] += 1
            if self.text_spam_counter[ctx.author.id] >= 5:
                del self.text_spam_counter[ctx.author.id]
                self.blacklist.append(ctx.author.id)
                await self.blacklist.save()
            await ctx.send(f'You are being rate limited. Try again in `{retry_after:.2f}` seconds.')
        else:
            self.text_spam_counter.pop(message.author.id, None)
            await self.invoke(ctx)

    async def on_voice_state_update(self, member, before, after):
        if before.channel != after.channel:
            if before.channel is not None:
                await self.on_voice_leave(member, before.channel)
            if after.channel is not None:
                await self.on_voice_join(member, after.channel)

    async def on_voice_join(self, member, voice_channel):
        if member.id in self.blacklist:
            return
        if not str(voice_channel.id) in self.configs:
            channels = [c for c in voice_channel.guild.channels if c.type == ChannelType.text and
                        c.name == self.get_channel_text_name(voice_channel.name)
                        and c.category_id == voice_channel.category_id]
            if channels:
                text_channel = channels[0]
                overwrite = {voice_channel.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
                for m in voice_channel.members:
                    overwrite[m] = discord.PermissionOverwrite(read_messages=True)
                overwrite[member] = discord.PermissionOverwrite(read_messages=True)
                await text_channel.edit(overwrites=overwrite)
            else:
                settings = self.configs['category-channels']
                if settings and voice_channel.category.name.lower() in settings:
                    name = voice_channel.name
                    existing_channels = [c for c in voice_channel.guild.channels if
                                         c.type == ChannelType.text and c.name == self.get_channel_text_name(name)
                                         and c.category.name.lower() == settings[0]]
                    if voice_channel.category.name.lower() in settings:
                        if existing_channels:
                            await self.overwrite_text_channel_permissions(voice_channel, self.get_channel_text_name(voice_channel.name))
                        else:
                            text_overwrites = {voice_channel.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
                            for m in voice_channel.members:
                                text_overwrites[m] = discord.PermissionOverwrite(read_messages=True)
                            new_text_channel = await member.guild.create_text_channel(
                                overwrites=text_overwrites,
                                name=name,
                                category=voice_channel.category,
                                user_limit=0
                            )
                            self.channels.append(new_text_channel.id)
            return
        perms = member.guild.me.guild_permissions
        if not perms.manage_channels or not perms.move_members:
            return
        fake_message = discord.Object(id=0)
        fake_message.author = member
        bucket = self.voice_spam_control.get_bucket(fake_message)
        retry_after = bucket.update_rate_limit()
        if retry_after:
            self.voice_spam_counter[member.id] += 1
            if self.voice_spam_counter[member.id] >= 5:
                del self.text_spam_counter[member.id]
                self.blacklist.append(member.id)
                await self.blacklist.save()
            with suppress(discord.Forbidden):
                await member.send(f'You are being rate limited. Try again in `{retry_after:.2f}` seconds.')
        else:
            settings = self.configs[str(voice_channel.id)]
            name = settings.get('name', '@user\'s channel')
            limit = settings.get('limit', 0)
            top = settings.get('top', False)
            save_index = False
            try:
                category = member.guild.get_channel(settings['category'])
            except KeyError:
                category = voice_channel.category
            if '@user' in name:
                name = name.replace('@user', member.display_name)
            if '@game' in name:
                for activity in member.activities:
                    if activity.type == discord.ActivityType.playing and activity.name is not None:
                        name = name.replace('@game', activity.name)
                        break
                else:
                    name = name.replace('@game', 'no game')
            if '@position' in name:
                save_index = True
                index = 1
                indexes_sorted = sorted(self.channel_indexes.values())
                for i in indexes_sorted:
                    if index >= i:
                        index += 1
                    else:
                        break
                name = name.replace('@position', str(index))
                name = name.replace(' ', '-')
            if len(name) > 100:
                name = name[:97] + '...'
            words = self.bad_words.get(str(member.guild.id), [])
            for word in words:
                if word in name:
                    name = name.replace(word, '*' * len(word))
            overwrites = {
                member.guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                    manage_channels=True,
                    move_members=True,
                    manage_permissions=True
                ),
                member: discord.PermissionOverwrite(
                    manage_channels=True,
                    move_members=True,
                    manage_permissions=True
                )
            }
            new_channel = await member.guild.create_voice_channel(
                overwrites=overwrites,
                name=name,
                category=category,
                user_limit=limit
            )

            text_overwrites = {voice_channel.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
            for m in voice_channel.members:
                text_overwrites[m] = discord.PermissionOverwrite(read_messages=True)

            new_text_channel = await member.guild.create_text_channel(
                overwrites=text_overwrites,
                name=name,
                category=category,
                user_limit=limit
            )
            if top:
                self.loop.create_task(new_channel.edit(position=0))
            await member.move_to(new_channel)
            self.channels.append(new_channel.id)
            self.channels.append(new_text_channel.id)

            if save_index:
                self.channel_indexes[new_channel.id] = index
                await self.channel_indexes.save()
            await self.channels.save()

    async def on_guild_channel_delete(self, channel):
        if str(channel.id) in self.configs:
            try:
                self.configs.pop(str(channel.id))
            except KeyError:
                return
            await self.configs.save()

    async def on_voice_leave(self, member, voice_channel):
        # [ c for c in channel.guild.channels if c.type == ChannelType.text and c.name == channel.name]
        if 'category-channels' not in self.configs:
            self.configs['category-channels'] = []
        category_settings = self.configs['category-channels']
        if voice_channel.id in self.channels:
            if len(voice_channel.members) == 0:
                await self.clear_empty_voice_channels(member, voice_channel)
                self.channels.remove(voice_channel.id)
                if voice_channel.id in self.channel_indexes:
                    del self.channel_indexes[voice_channel.id]
                await self.channels.save()
                text_channels = self.get_text_channels(voice_channel)
                for text_channel in text_channels:
                    await text_channel.delete()
            else:
                text_channels = self.get_text_channels(voice_channel)
                if text_channels:
                    text_channel = text_channels[0]
                    await self.overwrite_text_channel_permissions(voice_channel, text_channel)
        if voice_channel.category and voice_channel.category.name.lower() in category_settings:
            if len(voice_channel.members) == 0:
                await self.clear_empty_text_channels(member, voice_channel)
            else:
                text_channels = self.get_text_channels(voice_channel)
                if text_channels:
                    text_channel = text_channels[0]
                    await self.overwrite_text_channel_permissions(voice_channel, text_channel)


    def get_text_channels(self, channel):
        text_channel = [c for c in channel.guild.channels if
                        c.type == ChannelType.text and c.name == self.get_channel_text_name(channel.name)
                        and c.category.id == channel.category.id]
        return text_channel

    async def overwrite_text_channel_permissions(self, voice_channel, text_channel: discord.TextChannel):
        overwrite = {voice_channel.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
        for m in voice_channel.members:
            overwrite[m] = discord.PermissionOverwrite(read_messages=True)
        #overwrite[member] = discord.PermissionOverwrite(read_messages=False)
        await text_channel.edit(overwrites=overwrite)

    async def clear_empty_voice_channels(self, member, channel):
        perms = channel.permissions_for(member.guild.me)
        if perms.manage_channels:
            # [await c.delete() for c in channel.guild.channels if
            #  c.type == ChannelType.voice and c.name == channel.name.lower()]
            await channel.delete()

    async def clear_empty_text_channels(self, member, voice_channel):
        perms = voice_channel.permissions_for(member.guild.me)
        if perms.manage_channels:
            for c in self.get_text_channels(voice_channel):
                await c.delete()

    def get_channel_text_name(self, voice_channel_name):
        return voice_channel_name.lower().replace(' ', '-')

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, commands.CommandInvokeError) and not isinstance(error.original, menus.MenuError):
            error = error.original
            traceback.print_exception(error.__class__.__name__, error, error.__traceback__)
            owner = self.get_user(self.owner_id)
            if owner is not None:
                tb = '\n'.join(traceback.format_exception(error.__class__.__name__, error, error.__traceback__))
                with suppress(discord.HTTPException):
                    await owner.send(embed=discord.Embed(
                        description=f'```py\n{tb}```',
                        color=discord.Color.red()
                    ))
        else:
            await ctx.safe_send(msg=str(error), color=discord.Color.red())


if __name__ == "__main__":
    Bot().run(config.token)
