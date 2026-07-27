"""
NFC Cards Telegram Shop Bot
----------------------------
A menu-driven storefront bot: Browse Products, Cart, Orders, Support Tickets,
Reviews, News Feed, Help, Website + Community links.

Setup:
  1. pip install -r requirements.txt
  2. Set environment variables: BOT_TOKEN, ADMIN_CHAT_ID (your own Telegram
     user ID, so order/support notifications reach you), and optionally
     PROVIDER_TOKEN (Stripe payment provider token from BotFather, for real
     checkout via Telegram Payments).
  3. Run: python bot.py

Data is stored in a local SQLite file (shop.db) - orders, leads, and support
tickets all live there so nothing is lost between restarts.
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
PROVIDER_TOKEN = os.environ.get("PROVIDER_TOKEN", "")  # Stripe, via BotFather
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # your Telegram user ID
WEBSITE_URL = os.environ.get("WEBSITE_URL", "https://example.com")
COMMUNITY_URL = os.environ.get("COMMUNITY_URL", "https://t.me/example")

# Social-proof stats shown on the welcome screen (adjust anytime).
DISPLAY_SALES_COUNT = int(os.environ.get("DISPLAY_SALES_COUNT", "2676"))
AVG_REVIEW = os.environ.get("AVG_REVIEW", "5.0")
REVIEW_COUNT = int(os.environ.get("REVIEW_COUNT", "138"))
AVG_TICKET_RESPONSE = os.environ.get("AVG_TICKET_RESPONSE", "2.1h")

# Product catalog - card type -> label
PRODUCTS = {
    "google": "Google Card",
    "trustpilot": "Trustpilot Card",
    "tripadvisor": "Tripadvisor Card",
    "whatsapp": "WhatsApp Growth Card",
    "telegram": "Telegram Growth Card",
    "social": "Social Media Card (IG/TikTok/FB)",
}

# Product image + customer-facing description, shown when a product is tapped.
# Images live in the /cards folder next to bot.py.
PRODUCT_INFO = {
    "google": {
        "image": "card_google.png",
        "description": (
            "⭐ *Google Card*\n\n"
            "Here's the truth: 9 out of 10 happy customers never leave you a "
            "Google review. Not because they don't love you — because it's too "
            "much friction. They mean to do it later. Later never comes.\n\n"
            "This card kills that friction. One tap, their review page is open, "
            "done in 15 seconds — while they're still smiling about you.\n\n"
            "🔑 *What You Get*\n"
            "📲 Tap & Review — any NFC phone, no app needed\n"
            "🔗 QR code backup for older phones\n"
            "📈 More 5-star reviews = higher local search ranking\n"
            "🛠️ Free setup — linked to your Google Business Profile\n"
            "🚚 Free UK shipping\n\n"
            "Every review you're not collecting is a customer choosing your "
            "competitor instead. Grab yours below."
        ),
    },
    "trustpilot": {
        "image": "card_trustpilot.png",
        "description": (
            "⭐ *Trustpilot Card*\n\n"
            "People don't buy from businesses they don't trust — and Trustpilot "
            "is where they go to decide. A thin review count says \"risky.\" A "
            "wall of verified 5-stars says \"safe bet.\"\n\n"
            "This card turns a good moment with a customer into a public, "
            "verified vote of confidence — before they've even left the building.\n\n"
            "🔑 *What You Get*\n"
            "📲 Tap & Review — no typing, no searching\n"
            "🔗 QR code backup included\n"
            "✅ Verified reviews build instant buyer trust\n"
            "🛠️ Free setup — linked to your Trustpilot page\n"
            "🚚 Free UK shipping\n\n"
            "Trust is the real currency online. Start stacking it below."
        ),
    },
    "tripadvisor": {
        "image": "card_tripadvisor.png",
        "description": (
            "⭐ *Tripadvisor Card*\n\n"
            "In travel and hospitality, ranking is everything — it's the "
            "difference between fully booked and empty tables. And ranking is "
            "driven by one thing: fresh reviews, consistently.\n\n"
            "This card makes leaving one so easy your guests do it before "
            "they've even checked out.\n\n"
            "🔑 *What You Get*\n"
            "📲 Tap & Review — built for hospitality & travel\n"
            "🔗 QR code backup included\n"
            "📈 Climb the Tripadvisor ranking faster\n"
            "🛠️ Free setup — linked to your listing\n"
            "🚚 Free UK shipping\n\n"
            "Every day without this card is a day your ranking stalls while "
            "competitors climb. Get yours below."
        ),
    },
    "whatsapp": {
        "image": "card_whatsapp.png",
        "description": (
            "💬 *WhatsApp Growth Card*\n\n"
            "You don't own your Instagram followers — the algorithm does. You "
            "don't own your email list open rate — the spam folder does. But a "
            "WhatsApp broadcast list? That's a direct line only you control.\n\n"
            "This card turns a single tap into a subscriber, instantly — no "
            "typing a number, no forgetting to save your contact.\n\n"
            "🔑 *What You Get*\n"
            "📲 Tap to join — no manual number-saving\n"
            "🔗 QR code backup included\n"
            "📣 Build a marketing channel you actually own\n"
            "🛠️ Free setup — linked to your number/group\n"
            "🚚 Free UK shipping\n\n"
            "The businesses winning right now are building owned audiences. "
            "Start yours below."
        ),
    },
    "telegram": {
        "image": "card_telegram.png",
        "description": (
            "✈️ *Telegram Growth Card*\n\n"
            "A channel with 50 members and a channel with 5,000 members run "
            "the exact same software. The only difference is how many people "
            "found it easy enough to join.\n\n"
            "This card removes every bit of friction — no searching a username, "
            "no typing, just tap and they're in.\n\n"
            "🔑 *What You Get*\n"
            "📲 Tap to join — instant channel/group access\n"
            "🔗 QR code backup included\n"
            "📣 Grow your audience passively, every single day\n"
            "🛠️ Free setup — linked to your channel/group\n"
            "🚚 Free UK shipping\n\n"
            "Every customer who walks past today without joining is growth "
            "you left on the table. Fix that below."
        ),
    },
    "social": {
        "image": "card_social.png",
        "description": (
            "📸 *Social Media Card*\n\n"
            "You're already getting the foot traffic. The only question is "
            "whether it walks out the door and disappears, or follows you home "
            "on Instagram, TikTok and Facebook.\n\n"
            "One tap on this card and they're following all three — no typing "
            "your handle, no \"I'll find you later\" that never happens.\n\n"
            "🔑 *What You Get*\n"
            "📲 Tap once — links to all 3 platforms\n"
            "🔗 QR code backup included\n"
            "📣 Turn foot traffic into a following that keeps buying\n"
            "🛠️ Free setup — linked to your profiles\n"
            "🚚 Free UK shipping\n\n"
            "Every visitor who leaves without following is a customer you'll "
            "have to win all over again. Stop the leak below."
        ),
    },
}

# Bundle pricing in pence (GBP) - same tiers applied to every card type
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


# --- Conversation states for the order form ---
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
                InlineKeyboardButton(f"🌟 Reviews ({REVIEW_COUNT})", callback_data="reviews"),
                InlineKeyboardButton("📋 My Orders", callback_data="my_orders"),
            ],
            [
                InlineKeyboardButton("📦 Track Order", callback_data="track"),
                InlineKeyboardButton("🤔 Help", callback_data="help"),
            ],
            [InlineKeyboardButton("🛒 View Cart", callback_data="view_cart")],
            [InlineKeyboardButton("🔗 Website", url=WEBSITE_URL)],
            [InlineKeyboardButton("👥 Community Group", url=COMMUNITY_URL)],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "✅ *Shop is Online!*\n\n"
        f"📦 {DISPLAY_SALES_COUNT:,} Sales\n"
        f"⭐ {AVG_REVIEW} Average Review ({REVIEW_COUNT} reviews)\n"
        f"⚡ {AVG_TICKET_RESPONSE} Average Ticket Response\n\n"
        "Welcome — grab NFC review/growth cards for your business. "
        "Bundles start from £17.95.\n\n"
        "Tap your card on any phone to send customers straight to leave a "
        "Google/Trustpilot/Tripadvisor review, or to grow your WhatsApp, "
        "Telegram, Instagram, TikTok or Facebook."
    )
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )


async def select_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point of the order conversation - user picked a card type."""
    query = update.callback_query
    await query.answer()
    product_key = query.data.replace("product_", "")
    context.user_data["product"] = PRODUCTS[product_key]
    context.user_data["product_key"] = product_key

    buttons = []
    row = []
    for i, size in enumerate(BUNDLE_ORDER, start=1):
        total_pence = BUNDLE_PRICES_PENCE[size]
        price = format_gbp(total_pence)
        per_card = format_gbp(round(total_pence / size))
        label = f"{size} — {price} ({per_card}/ea)" if size > 1 else f"{size} — {price}"
        row.append(InlineKeyboardButton(label, callback_data=f"bundle_{size}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="browse")])

    info = PRODUCT_INFO.get(product_key)
    caption = f"{info['description']}\n\nChoose a bundle size:" if info else (
        f"*{context.user_data['product']}*\n\nChoose a bundle size:"
    )

    # A text message can't be edited into a photo message, so remove the old
    # menu message and send a fresh photo+caption message instead.
    try:
        await query.delete_message()
    except Exception:
        pass

    if info and os.path.exists(info["image"]):
        with open(info["image"], "rb") as photo:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=photo,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=caption,
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

    confirm_text = (
        f"🛒 *Selected:* {size} × {context.user_data['product']} = "
        f"{format_gbp(BUNDLE_PRICES_PENCE[size])}\n\n"
        "Let's get your details. What's your business name?"
    )
    # The product message is a photo, so its caption is edited, not its text.
    try:
        await query.edit_message_caption(caption=confirm_text, parse_mode="Markdown")
    except Exception:
        await context.bot.send_message(
            chat_id=query.message.chat_id, text=confirm_text, parse_mode="Markdown"
        )
    return ASK_BUSINESS_NAME


async def safe_edit(query, text, reply_markup=None, parse_mode=None):
    """Edit a message's text, but fall back to delete+send if the current
    message is a photo (edit_message_text fails on photo messages)."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            await query.delete_message()
        except Exception:
            pass
        await query.get_bot().send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all the non-order-flow menu buttons."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "browse":
        buttons = [
            [InlineKeyboardButton(label, callback_data=f"product_{key}")]
            for key, label in PRODUCTS.items()
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="back_main")])
        await safe_edit(
            query,
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
            text = "You don't have any orders yet. Tap Browse Products to get started."
        else:
            text = "*Your Orders:*\n\n" + "\n".join(
                f"#{r['id']} — {r['product']} x{r['bundle_size']} — {r['status']}"
                for r in rows
            )
        await safe_edit(
            query, text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )

    elif data == "track":
        await safe_edit(
            query,
            "Send me your order number (e.g. `12`) and I'll look up its status.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_main")]]
            ),
        )
        context.user_data["awaiting_track"] = True

    elif data == "support":
        await safe_edit(
            query,
            "Describe your issue and I'll pass it straight to the team.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_main")]]
            ),
        )
        context.user_data["awaiting_support"] = True

    elif data == "view_cart":
        await safe_edit(
            query,
            "🛒 *Your Cart*\n\n"
            "This shop checks out one product at a time — pick a card and "
            "bundle size under Browse Products and you'll go straight to "
            "checkout, no cart needed.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )

    elif data == "reviews":
        mock_reviews = (
            f"⭐️⭐️⭐️⭐️⭐️ {AVG_REVIEW} average ({REVIEW_COUNT} reviews)\n\n"
            "🗣️ *Sarah M. — Google Card*\n"
            "\"Went from 12 to 40+ Google reviews in 3 weeks. Customers just tap "
            "and leave before they walk out. Wish I'd got this sooner.\"\n\n"
            "🗣️ *James R. — WhatsApp Growth Card*\n"
            "\"Our WhatsApp broadcast list tripled in a month. Zero effort from "
            "staff — the card does all the work.\"\n\n"
            "🗣️ *Aisha K. — Trustpilot Card*\n"
            "\"Looks premium on the counter and customers actually use it. "
            "Trustpilot score went from 4.3 to 4.8.\"\n\n"
            "New reviews are added as real orders come in."
        )
        await safe_edit(
            query,
            mock_reviews,
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )

    elif data == "news":
        await safe_edit(
            query,
            "📣 No announcements yet — check back soon!",
            reply_markup=main_menu_keyboard(),
        )

    elif data == "help":
        await safe_edit(
            query,
            "*Help*\n\n"
            "• NFC tap works on Android (Chrome) and most modern iPhones.\n"
            "• Every card also comes with a QR code as a backup.\n"
            "• Shipping usually takes 3–5 working days after payment.\n"
            "• Need a human? Use Support Tickets.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )

    elif data == "back_main":
        await safe_edit(
            query, "Main Menu:", reply_markup=main_menu_keyboard()
        )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles free-text replies for track-order and support-ticket flows."""
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
                f"Status: {row['status']}{tracking}",
                reply_markup=main_menu_keyboard(),
            )
        else:
            await update.message.reply_text(
                "Couldn't find that order number.", reply_markup=main_menu_keyboard()
            )
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
            "Got it — a ticket has been opened and the team will reply here.",
            reply_markup=main_menu_keyboard(),
        )
        if ADMIN_CHAT_ID:
            await context.bot.send_message(
                ADMIN_CHAT_ID,
                f"🎫 New support ticket from @{update.effective_user.username}:\n{update.message.text}",
            )
        return


# --- Order form conversation (business details) ---

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
        # No payment provider configured yet - confirm the order manually instead
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
            f"({format_gbp(amount_pence)}) is saved. Online card payment isn't set up yet "
            "on this bot — the team will follow up to take payment.",
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


async def leads_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: show the most recent 20 leads."""
    if not ADMIN_CHAT_ID or str(update.effective_user.id) != str(ADMIN_CHAT_ID):
        return  # silently ignore for anyone who isn't the admin
    conn = db()
    rows = conn.execute(
        "SELECT * FROM leads ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("No leads saved yet.")
        return
    chunks = []
    for r in rows:
        chunks.append(
            f"*Lead #{r['id']}* — {r['created_at']}\n"
            f"Business: {r['business_name']}\n"
            f"Contact: {r['contact_name']}\n"
            f"Phone: {r['phone']}\n"
            f"Email: {r['email']}\n"
            f"Locations: {r['locations']}\n"
            f"Product: {r['product']} x{r['bundle_size']}\n"
            f"Status: {r['status']}"
        )
    batch = []
    for i, c in enumerate(chunks, 1):
        batch.append(c)
        if i % 5 == 0 or i == len(chunks):
            await update.message.reply_text(
                "\n\n---\n\n".join(batch), parse_mode="Markdown"
            )
            batch = []


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
        "🎉 Payment received! Your cards will be printed and shipped shortly. "
        "Use Track Order any time to check status.",
        reply_markup=main_menu_keyboard(),
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("Set the BOT_TOKEN environment variable before running.")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(select_product, pattern="^product_")],
        states={
            ASK_BUNDLE: [CallbackQueryHandler(select_bundle, pattern="^bundle_")],
            ASK_BUSINESS_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_business_name)],
            ASK_CONTACT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_contact_name)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_email)],
            ASK_LOCATIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_locations)],
            ASK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_address)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("leads", leads_command))
    app.add_handler(order_conv)
    app.add_handler(CallbackQueryHandler(button_router))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
