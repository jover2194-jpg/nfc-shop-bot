"""
TapForge NFC Cards Telegram Shop Bot
------------------------------------
Menu-driven storefront: Browse Products (with descriptions),
Orders, Support Tickets, Reviews, News, Help,
Website + Community links.

Setup:
  1. pip install -r requirements.txt
  2. Environment variables: BOT_TOKEN, ADMIN_CHAT_ID, (optional) PROVIDER_TOKEN,
     WEBSITE_URL, COMMUNITY_URL
  3. Run: python bot.py

Data stored in /data/shop.db (Railway Volume)
"""

import logging
import os
import sqlite3
from datetime import datetime

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PROVIDER_TOKEN = os.environ.get("PROVIDER_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
WEBSITE_URL = os.environ.get("WEBSITE_URL", "https://example.com")
COMMUNITY_URL = os.environ.get("COMMUNITY_URL", "https://t.me/example")

# Product catalog
PRODUCTS = {
    "google": {
        "name": "Google Review Card",
        "desc": (
            "More 5-star Google reviews = more customers finding you.\n"
            "One tap sends people straight to leave a review. No typing, no friction."
        ),
        "emoji": "🔵",
    },
    "trustpilot": {
        "name": "Trustpilot Card",
        "desc": (
            "Build real trust online.\n"
            "Customers tap once and land on your Trustpilot page ready to review."
        ),
        "emoji": "🟢",
    },
    "tripadvisor": {
        "name": "Tripadvisor Card",
        "desc": (
            "Perfect for restaurants, hotels & experiences.\n"
            "One tap → instant Tripadvisor review. More reviews = higher rankings."
        ),
        "emoji": "🦉",
    },
    "whatsapp": {
        "name": "WhatsApp Growth Card",
        "desc": (
            "Turn every customer into a WhatsApp contact.\n"
            "They tap the card and open a chat with you instantly — perfect for bookings, support or offers."
        ),
        "emoji": "💬",
    },
    "telegram": {
        "name": "Telegram Growth Card",
        "desc": (
            "Grow your Telegram channel or group fast.\n"
            "One tap and they join. Ideal for communities, updates and exclusive deals."
        ),
        "emoji": "✈️",
    },
    "social": {
        "name": "Social Media Card",
        "desc": (
            "One card for Instagram, TikTok & Facebook.\n"
            "Customers tap and choose which platform to follow you on. Maximum reach, zero effort."
        ),
        "emoji": "📱",
    },
}

BUNDLE_PRICES_PENCE = {
    1: 1795,
    5: 5295,
    10: 7895,
    50: 32449,
    100: 39999,
    500: 149999,
}
BUNDLE_ORDER = [1, 5, 10, 50, 100, 500]


def format_gbp(pence: int) -> str:
    return f"£{pence / 100:,.2f}"


(
    ASK_BUNDLE,
    ASK_BUSINESS_NAME,
    ASK_CONTACT_NAME,
    ASK_PHONE,
    ASK_EMAIL,
    ASK_LOCATIONS,
    ASK_ADDRESS,
) = range(7)


def db():
    conn = sqlite3.connect("/data/shop.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER,
            business_name TEXT,
            contact_name TEXT,
            phone TEXT,
            email TEXT,
            locations TEXT,
            product TEXT,
            bundle_size INTEGER,
            source TEXT DEFAULT 'telegram_bot',
            status TEXT DEFAULT 'New',
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            telegram_user_id INTEGER,
            product TEXT,
            bundle_size INTEGER,
            shipping_address TEXT,
            amount_pence INTEGER,
            status TEXT DEFAULT 'Paid',
            tracking_number TEXT,
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER,
            username TEXT,
            message TEXT,
            status TEXT DEFAULT 'Open',
            created_at TEXT
        )"""
    )
    conn.commit()
    conn.close()


def main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛍️ Browse Products", callback_data="browse")],
            [InlineKeyboardButton("🎫 Support Tickets", callback_data="support")],
            [InlineKeyboardButton("📣 News Feed", callback_data="news")],
            [
                InlineKeyboardButton("🌟 Reviews", callback_data="reviews"),
                InlineKeyboardButton("📋 My Orders", callback_data="my_orders"),
            ],
            [
                InlineKeyboardButton("📦 Track Order", callback_data="track"),
                InlineKeyboardButton("🤔 Help", callback_data="help"),
            ],
            [InlineKeyboardButton("🔗 Website", url=WEBSITE_URL)],
            [InlineKeyboardButton("👥 Community Group", url=COMMUNITY_URL)],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db()
    order_count = conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
    conn.close()
    text = (
        "✅ *TapForge Shop is Online!*\n\n"
        f"📦 {order_count} Sales\n\n"
        "Welcome — grab NFC review & growth cards for your business.\n"
        "Bundles start from £17.95.\n\n"
        "One tap on any phone → customers leave a review or join your WhatsApp / Telegram / socials."
    )
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )


async def select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = query.data.replace("product_", "")
    product = PRODUCTS[product_key]
    context.user_data["product"] = product["name"]
    context.user_data["product_key"] = product_key

    text = (
        f"{product['emoji']} *{product['name']}*\n\n"
        f"{product['desc']}\n\n"
        "Choose a bundle size:"
    )

    buttons = []
    row = []
    for size in BUNDLE_ORDER:
        price = format_gbp(BUNDLE_PRICES_PENCE[size])
        row.append(
            InlineKeyboardButton(f"{size} — {price}", callback_data=f"bundle_{size}")
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("⬅️ Back to Products", callback_data="browse")])

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return ASK_BUNDLE


async def select_bundle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    size = int(query.data.replace("bundle_", ""))
    context.user_data["bundle_size"] = size
    context.user_data["amount_pence"] = BUNDLE_PRICES_PENCE[size]

    await query.edit_message_text(
        f"*{context.user_data['product']}* — {size} card(s) — "
        f"{format_gbp(BUNDLE_PRICES_PENCE[size])}\n\n"
        "Let's get your details.\n\nWhat's your business name?",
        parse_mode="Markdown",
    )
    return ASK_BUSINESS_NAME


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "browse":
        buttons = [
            [InlineKeyboardButton(f"{p['emoji']} {p['name']}", callback_data=f"product_{key}")]
            for key, p in PRODUCTS.items()
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_main")])
        await query.edit_message_text(
            "Choose a card type:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data == "my_orders":
        conn = db()
        rows = conn.execute(
            "SELECT * FROM orders WHERE telegram_user_id=? ORDER BY id DESC",
            (query.from_user.id,),
        ).fetchall()
        conn.close()
        if not rows:
            text = "You don't have any orders yet.\nTap *Browse Products* to get started."
        else:
            text = "*Your Orders:*\n\n" + "\n".join(
                f"#{r['id']} — {r['product']} x{r['bundle_size']} — {r['status']}"
                for r in rows
            )
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )

    elif data == "track":
        await query.edit_message_text(
            "Send me your order number (e.g. `12`) and I'll look up its status.",
            parse_mode="Markdown",
        )
        context.user_data["awaiting_track"] = True

    elif data == "support":
        await query.edit_message_text(
            "Describe your issue and I'll pass it straight to the TapForge team."
        )
        context.user_data["awaiting_support"] = True

    elif data == "reviews":
        await query.edit_message_text(
            "⭐️⭐️⭐️⭐️⭐️ 5.0 average\n\n"
            "Reviews from real customers will appear here as orders complete.",
            reply_markup=main_menu_keyboard(),
        )

    elif data == "news":
        await query.edit_message_text(
            "📣 No announcements yet — check back soon!",
            reply_markup=main_menu_keyboard(),
        )

    elif data == "help":
        await query.edit_message_text(
            "*Help*\n\n"
            "• NFC tap works on Android (Chrome) and most modern iPhones.\n"
            "• Every card also comes with a QR code as a backup.\n"
            "• Shipping usually takes 3–5 working days after payment.\n"
            "• Need a human? Use Support Tickets.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )

    elif data == "back_main":
        await query.edit_message_text(
            "Main Menu:", reply_markup=main_menu_keyboard()
        )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_track"):
        context.user_data["awaiting_track"] = False
        order_id = update.message.text.strip().lstrip("#")
        conn = db()
        row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        conn.close()
        if row:
            tracking = f"\nTracking: {row['tracking_number']}" if row["tracking_number"] else ""
            await update.message.reply_text(
                f"Order #{row['id']} — {row['product']} x{row['bundle_size']}\n"
                f"Status: {row['status']}{tracking}"
            )
        else:
            await update.message.reply_text("Couldn't find that order number.")
        return

    if context.user_data.get("awaiting_support"):
        context.user_data["awaiting_support"] = False
        conn = db()
        conn.execute(
            "INSERT INTO support_tickets (telegram_user_id, username, message, created_at) VALUES (?,?,?,?)",
            (
                update.effective_user.id,
                update.effective_user.username or "",
                update.message.text,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        await update.message.reply_text(
            "Got it — a ticket has been opened and the team will reply here."
        )
        if ADMIN_CHAT_ID:
            await context.bot.send_message(
                ADMIN_CHAT_ID,
                f"🎫 New support ticket from @{update.effective_user.username}:\n{update.message.text}",
            )
        return


async def ask_business_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["business_name"] = update.message.text
    await update.message.reply_text("Contact name?")
    return ASK_CONTACT_NAME


async def ask_contact_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["contact_name"] = update.message.text
    await update.message.reply_text("Phone number?")
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text
    await update.message.reply_text("Email address?")
    return ASK_EMAIL


async def ask_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["email"] = update.message.text
    await update.message.reply_text("How many locations does the business have?")
    return ASK_LOCATIONS


async def ask_locations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["locations"] = update.message.text
    await update.message.reply_text("Great — what's the shipping address for the card(s)?")
    return ASK_ADDRESS


async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["address"] = update.message.text
    ud = context.user_data

    conn = db()
    cur = conn.execute(
        """INSERT INTO leads (telegram_user_id, business_name, contact_name, phone, email, locations, product, bundle_size, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            update.effective_user.id,
            ud["business_name"],
            ud["contact_name"],
            ud["phone"],
            ud["email"],
            ud["locations"],
            ud["product"],
            ud["bundle_size"],
            datetime.utcnow().isoformat(),
        ),
    )
    lead_id = cur.lastrowid
    conn.commit()
    conn.close()
    context.user_data["lead_id"] = lead_id

    if ADMIN_CHAT_ID:
        await context.bot.send_message(
            ADMIN_CHAT_ID,
            f"🟢 New lead #{lead_id}: {ud['business_name']} ({ud['product']} x{ud['bundle_size']}) — "
            f"{ud['contact_name']}, {ud['phone']}, {ud['email']}",
        )

    amount_pence = ud["amount_pence"]

    if not PROVIDER_TOKEN:
        conn = db()
        conn.execute(
            """INSERT INTO orders (lead_id, telegram_user_id, product, bundle_size, shipping_address, amount_pence, status, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                lead_id,
                update.effective_user.id,
                ud["product"],
                ud["bundle_size"],
                ud["address"],
                amount_pence,
                "Awaiting payment (manual)",
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        await update.message.reply_text(
            f"Thanks! Your order for {ud['bundle_size']} x {ud['product']} "
            f"({format_gbp(amount_pence)}) is saved.\n\n"
            "Online card payment isn't set up yet — the team will follow up to take payment.",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"Almost done — tap below to pay {format_gbp(amount_pence)}."
    )
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=f"{ud['product']} x{ud['bundle_size']}",
        description=f"{ud['bundle_size']} card(s) — {ud['product']}",
        payload=f"lead_{lead_id}",
        provider_token=PROVIDER_TOKEN,
        currency="GBP",
        prices=[LabeledPrice(f"{ud['product']} x{ud['bundle_size']}", amount_pence)],
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payload = update.message.successful_payment.invoice_payload
    lead_id = payload.replace("lead_", "")
    conn = db()
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    conn.execute(
        """INSERT INTO orders (lead_id, telegram_user_id, product, bundle_size, shipping_address, amount_pence, status, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            lead_id,
            update.effective_user.id,
            lead["product"] if lead else "Unknown",
            lead["bundle_size"] if lead else 1,
            context.user_data.get("address", ""),
            context.user_data.get("amount_pence", 0),
            "Paid",
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(
        "🎉 Payment received! Your cards will be printed and shipped shortly.\n"
        "Use Track Order any time to check status.",
        reply_markup=main_menu_keyboard(),
    )


async def leads_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: show latest 20 leads."""
    if not ADMIN_CHAT_ID or str(update.effective_user.id) != str(ADMIN_CHAT_ID):
        await update.message.reply_text("This command is for admins only.")
        return

    conn = db()
    rows = conn.execute(
        "SELECT * FROM leads ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("No leads yet.")
        return

    text = "*Latest 20 Leads:*\n\n"
    for r in rows:
        text += (
            f"#{r['id']} — {r['business_name']}\n"
            f"{r['product']} x{r['bundle_size']}\n"
            f"{r['contact_name']} | {r['phone']} | {r['email']}\n"
            f"Locations: {r['locations']}\n"
            f"{r['created_at'][:10]}\n\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


def main():
    if not BOT_TOKEN:
        raise SystemExit("Set the BOT_TOKEN environment variable before running.")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(select_product, pattern="^product_")],
        states={
            ASK_BUNDLE: [CallbackQueryHandler(select_bundle, pattern="^bundle_")],
            ASK_BUSINESS_NAME: [MessageHandler(filters.TEXT & \~filters.COMMAND, ask_business_name)],
            ASK_CONTACT_NAME: [MessageHandler(filters.TEXT & \~filters.COMMAND, ask_contact_name)],
            ASK_PHONE: [MessageHandler(filters.TEXT & \~filters.COMMAND, ask_phone)],
            ASK_EMAIL: [MessageHandler(filters.TEXT & \~filters.COMMAND, ask_email)],
            ASK_LOCATIONS: [MessageHandler(filters.TEXT & \~filters.COMMAND, ask_locations)],
            ASK_ADDRESS: [MessageHandler(filters.TEXT & \~filters.COMMAND, ask_address)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("leads", leads_command))
    app.add_handler(order_conv)
    app.add_handler(CallbackQueryHandler(button_router))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, text_router))

    logger.info("TapForge bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
