import discord
from discord.ext import commands
import asyncio
import json
import os
from flask import Flask
from threading import Thread

# ---------------- KEEP ALIVE ----------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_web():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# ---------------- INTENTS ----------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- CONFIG ----------------
TOKEN = os.getenv("TOKEN")

SELLER_ROLE_NAME = "Seller"
SUPPLIER_ROLE_NAME = "Supplier"
TICKET_CATEGORY_NAME = "Tickets"

# ---------------- STORAGE ----------------
active_orders = {}
order_locks = {}

user_wallets = {}
user_balances = {}
cashout_ledger = {}

# ---------------- SAVE FILE ----------------
DATA_FILE = "data.json"

# ---------------- SAVE / LOAD ----------------
def save_data():
    data = {
        "wallets": user_wallets,
        "balances": user_balances,
        "ledger": cashout_ledger
    }

    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_data():
    global user_wallets
    global user_balances
    global cashout_ledger

    if not os.path.exists(DATA_FILE):
        save_data()
        return

    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)

    except:
        data = {
            "wallets": {},
            "balances": {},
            "ledger": {}
        }

        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)

    user_wallets = {
        int(k): v
        for k, v in data.get("wallets", {}).items()
    }

    user_balances = {
        int(k): v
        for k, v in data.get("balances", {}).items()
    }

    cashout_ledger = {
        int(k): v
        for k, v in data.get("ledger", {}).items()
    }

# ---------------- LOCK SYSTEM ----------------
def get_lock(order_id):
    if order_id not in order_locks:
        order_locks[order_id] = asyncio.Lock()
    return order_locks[order_id]

# ---------------- MONEY ----------------
def parse_amount(value):
    value = value.lower()

    if value.endswith("b"):
        return int(float(value[:-1]) * 1_000_000_000)

    if value.endswith("m"):
        return int(float(value[:-1]) * 1_000_000)

    return int(value)

def format_amount(value):
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}b".replace(".0", "")

    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m".replace(".0", "")

    return str(value)

# ---------------- ACCEPT MODAL ----------------
class AcceptModal(discord.ui.Modal, title="Accept Order"):
    sell_amount = discord.ui.TextInput(
        label="How much are you selling?",
        placeholder="Example: 100m",
        required=True,
        max_length=20
    )

    def __init__(self, order_id, supplier):
        super().__init__()
        self.order_id = order_id
        self.supplier = supplier

    async def on_submit(self, interaction: discord.Interaction):
        order = active_orders.get(self.order_id)

        if not order:
            await interaction.response.send_message(
                "❌ Order not found.",
                ephemeral=True
            )
            return

        lock = get_lock(self.order_id)

        async with lock:
            try:
                sell_value = parse_amount(self.sell_amount.value)
            except:
                await interaction.response.send_message(
                    "❌ Invalid amount.",
                    ephemeral=True
                )
                return

            remaining = order["remaining"]

            if sell_value <= 0 or sell_value > remaining:
                await interaction.response.send_message(
                    "❌ Invalid amount.",
                    ephemeral=True
                )
                return

            guild = order["guild"]
            seller = order["seller"]
            supplier = self.supplier

            category = discord.utils.get(
                guild.categories,
                name=TICKET_CATEGORY_NAME
            )

            if not category:
                category = await guild.create_category(TICKET_CATEGORY_NAME)

            ticket_name = supplier.name.lower().replace(" ", "-")

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    read_messages=False
                ),
                seller: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True
                ),
                supplier: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True
                ),
                guild.me: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True
                )
            }

            channel = await guild.create_text_channel(
                name=ticket_name,
                category=category,
                overwrites=overwrites
            )

            order["remaining"] -= sell_value
            order["last_sell_amount"] = sell_value
            order["last_supplier"] = supplier
            order["ticket_channel_id"] = channel.id

            await channel.send(
                f"🎫 **ORDER STARTED**\n\n"
                f"👤 Seller: {seller.mention}\n"
                f"🛒 Supplier: {supplier.mention}\n\n"
                f"💰 Selling: {format_amount(sell_value)}\n"
                f"💵 Rate: {order['rate']} PHP\n\n"
                f"Seller confirms with `!confirm`"
            )

            await interaction.response.send_message(
                "✅ Ticket created.",
                ephemeral=True
            )

# ---------------- ACCEPT BUTTON ----------------
class AcceptView(discord.ui.View):
    def __init__(self, order_id):
        super().__init__(timeout=None)
        self.order_id = order_id

    @discord.ui.button(label="Accept Order", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        order = active_orders.get(self.order_id)

        if not order:
            await interaction.response.send_message(
                "❌ Order not found.",
                ephemeral=True
            )
            return

        guild = order["guild"]
        member = guild.get_member(interaction.user.id)
        role = discord.utils.get(guild.roles, name=SUPPLIER_ROLE_NAME)

        if not member:
            await interaction.response.send_message(
                "❌ You are not in the server.",
                ephemeral=True
            )
            return

        if role not in member.roles:
            await interaction.response.send_message(
                "❌ You are not a supplier.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(
            AcceptModal(self.order_id, member)
        )

# ---------------- NEED COMMAND ----------------
@bot.command()
async def need(ctx, amount: str, rate: str):

    seller_role = discord.utils.get(
        ctx.guild.roles,
        name=SELLER_ROLE_NAME
    )

    supplier_role = discord.utils.get(
        ctx.guild.roles,
        name=SUPPLIER_ROLE_NAME
    )

    if not seller_role or seller_role not in ctx.author.roles:
        await ctx.send("❌ Only Seller role can use `!need`.")
        return

    if not supplier_role:
        await ctx.send("❌ Supplier role not found.")
        return

    try:
        amount_value = parse_amount(amount)
    except:
        await ctx.send("❌ Invalid amount.")
        return

    order_id = str(ctx.message.id)

    active_orders[order_id] = {
        "remaining": amount_value,
        "original_amount": amount,
        "rate": rate,
        "seller": ctx.author,
        "guild": ctx.guild
    }

    view = AcceptView(order_id)

    sent = 0

    for member in supplier_role.members:

        if member.bot:
            continue

        try:
            await member.send(
                f"📢 **NEW ORDER**\n\n"
                f"💰 Amount: {amount}\n"
                f"💵 Rate: {rate} PHP",
                view=view
            )

            sent += 1

        except:
            pass

    await ctx.send(
        f"✅ Sent to {sent} suppliers."
    )

# ---------------- ERROR HANDLER ----------------
@bot.event
async def on_command_error(ctx, error):
    await ctx.send(f"❌ {error}")
    print(error)

# ---------------- READY ----------------
@bot.event
async def on_ready():
    load_data()
    print(f"✅ Logged in as {bot.user}")

# ---------------- START ----------------
keep_alive()

try:
    bot.run(TOKEN)

finally:
    save_data()
