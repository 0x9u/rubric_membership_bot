import os
import aiohttp
import yaml
import json
import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import logging

API_BASE = "https://api.hellorubric.com/"
MEMBERSHIPS_ENDPOINT = "https://appserver.getqpay.com:9090/AppServerFive/getMembershipsOverview"

DEFAULT_CONFIG = {
    "refresh_cron_hour": 12,
    "guild_id": None,
    "channel_id": None,
    "message_id": None,
    "title" : None,
    "content": None,
}

class _BotBase(discord.Client):

    tree: discord.app_commands.CommandTree

    session_id: str
    email: str | None

    guild_id: int | None
    channel_id: int | None
    message_id: int | None
    content: str | None
    title : str | None

    scheduler: AsyncIOScheduler

    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.session_id = ""
        self.tree = discord.app_commands.CommandTree(self)
        self.scheduler = AsyncIOScheduler()

    def run(self, token: str, session_id: str, email: str | None = None):
        """
        Runs the bot.

        Requires session_id for rubric API, and token (bot token obviously) for discord API.
        Email is not required but rubric will take 12 seconds to query if not provided
        (gg if they allow different emails to be used lol)

        :param token: discord bot token
        :param session_id: rubric session id
        :param email: rubric email
        """
        self.session_id = session_id
        self.email = email
        super().run(token)

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        
        self.scheduler.start()

        config = self._load_config_file()

        config_guild_id = config.get("guild_id", None)

        if not isinstance(config_guild_id, int) and config_guild_id is not None:
            raise ValueError("Guild ID must be an integer")

        self.guild_id = config_guild_id

        config_cron_hour = config.get("refresh_cron_hour", 12)

        if not isinstance(config_cron_hour, int):
            raise ValueError("refresh_cron_hour must be an integer")

        self.cron_hour = config_cron_hour

        synced = await self.tree.sync()
        logging.info(f"Synced {len(synced)} commands")

        config_channel_id = config.get("channel_id", None)

        if not isinstance(config_channel_id, int) and config_guild_id is not None:
            raise ValueError("Channel ID must be an integer")

        self.channel_id = config_channel_id

        config_message_id = config.get("message_id", None)

        if not isinstance(config_message_id, int) and config_guild_id is not None:
            raise ValueError("Message ID must be an integer")

        self.message_id = config_message_id

        config_content = config.get("content", None)

        if not isinstance(config_content, str) and config_guild_id is not None:
            raise ValueError("Message content must be a string")

        self.content = config_content
        
        config_title = config.get("title", None)

        if not isinstance(config_title, str) and config_guild_id is not None:
            raise ValueError("Message title must be a string")

        self.title = config_title

        if self.channel_id is not None:
            self.scheduler.add_job(
                self._update_message, CronTrigger.from_crontab(f"* {self.cron_hour} * * *"))

    @staticmethod
    def _load_config_file():
        if not os.path.exists("config.yaml"):
            _BotBase._save_config_file(DEFAULT_CONFIG)
            return DEFAULT_CONFIG

        with open("config.yaml", "r") as f:
            return yaml.safe_load(f)

    @staticmethod
    def _save_config_file(new_config: dict):
        with open("config.yaml", "w") as f:
            yaml.dump(new_config, f, sort_keys=False, default_flow_style=False)

    async def _create_embed(self):
        return discord.Embed(
            title=self.title,
            description=self.content.replace("%d", await self._fetch_membership_count()),
            color=discord.Color.pink() # ceebs making colors an option
        )

    async def _fetch_membership_count(self) -> str:
        async with aiohttp.ClientSession() as session:
            logging.info("Fetching membership count")
            
            details = {
                "sessionid": self.session_id
            }
            if self.email is not None:
                details["email"] = self.email
            
            req_data = {
                "details": json.dumps(details),
                "endpoint": MEMBERSHIPS_ENDPOINT   
            }
            
            async with session.post(API_BASE, data=req_data,
                                    headers={"Content-Type": "application/x-www-form-urlencoded"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logging.debug(f"fetched membership count: {data=}")
                    active_members: str = data["activemembers"]
                    return active_members
                else:
                    raise Exception(
                        f"Error fetching membership count: {resp.status}")

    async def _update_message(self):
        # shouldnt happen
        assert self.guild_id is not None

        guild = self.get_guild(self.guild_id)
        if guild is None:
            logging.error("Guild not found", stack_info=True)
            return

        channel = guild.get_channel(self.channel_id)
        if channel is None:
            logging.error("Channel not found", stack_info=True)
            return

        message = await channel.fetch_message(self.message_id)
        if message is None:
            logging.error("Message not found", stack_info=True)
            return
        
        logging.info("Updating message")
        
        await message.edit(embed=await self._create_embed())

bot = _BotBase()

@bot.tree.command(description="pings the bot")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"pong: {round(bot.latency * 1000)}ms")

@discord.app_commands.describe(content="(use %d to represent the count, if not provided it will be appended to the end)")
@bot.tree.command(description="setup the bot to display rubric membership")
async def setup(interaction: discord.Interaction, channel: discord.channel.TextChannel, title: str, content: str):
    await interaction.response.defer()

    guild_id = interaction.guild_id
    channel_id = channel.id

    new_config = {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "title": title,
        "content": content
    }

    format_index = content.find("%d")
    if format_index == -1:
        new_config["content"] += " %d"

    bot.guild_id = guild_id
    bot.channel_id = channel_id
    bot.content = content
    bot.title = title

    msg = await channel.send(embed=await bot._create_embed())
    
    if bot.message_id is not None:
        old_msg = await bot.get_channel(bot.channel_id).fetch_message(bot.message_id)
        await old_msg.delete()

    new_config["message_id"] = msg.id
    bot.message_id = msg.id
    bot._save_config_file(new_config)

    bot.scheduler.add_job(bot._update_message,
                          CronTrigger.from_crontab(f"* {bot.cron_hour} * * *"))
    await interaction.followup.send("Setup complete", ephemeral=True)

@bot.tree.command(description="Display membership count")
async def membership_count(interaction: discord.Interaction):
    await interaction.response.defer()
    
    if bot.content is None:
        await interaction.followup.send("Please setup the bot first", ephemeral=True)
        return

    await interaction.followup.send(embed=await bot._create_embed())

if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()

    session_id = os.getenv("SESSION_ID")
    token = os.getenv("DISCORD_TOKEN")
    email = os.getenv("EMAIL")

    if session_id is None:
        raise ValueError("SESSION_ID is not set")
    elif token is None:
        raise ValueError("DISCORD_TOKEN is not set")

    bot.run(token, session_id, email)
