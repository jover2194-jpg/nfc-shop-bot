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
import asyncio
import sqlite3
import io
import csv
import requests
import qrcode
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_UP

from bip_utils import Bip32Slip10Secp256k1, P2WPKHAddr

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
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
NOWPAYMENTS_API_KEY = os.environ.get("NOWPAYMENTS_API_KEY", "")  # optional legacy fallback
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # your Telegram user ID
# Optional: private Telegram chat/group ID for automatic fulfilment alerts
FULFILMENT_CHAT_ID = os.environ.get("FULFILMENT_CHAT_ID", "")
# Optional: private group/channel that gets a live post every time someone new
# starts the bot - a running, human-readable backup of your subscriber list
# that lives independently of the database (get its ID with /chatid).
SUBSCRIBER_LOG_CHAT_ID = os.environ.get("SUBSCRIBER_LOG_CHAT_ID", "")
WEBSITE_URL = os.environ.get("WEBSITE_URL", "https://example.com")
COMMUNITY_URL = os.environ.get("COMMUNITY_URL", "https://t.me/example")

# Own-wallet crypto (preferred). Put these in Railway Variables — never in the code.
BTC_XPUB = os.environ.get("BTC_XPUB", "")
LTC_XPUB = os.environ.get("LTC_XPUB", "")
# Testnet mode: BTC only (BlockCypher has no Litecoin testnet). Uses the SAME
# BTC_XPUB but derives testnet-format (tb1...) addresses and polls BlockCypher's
# Bitcoin Testnet3 chain instead of mainnet. No real money is ever at risk in
# this mode - free practice coins only, from a public testnet faucet.
TESTNET = os.environ.get("TESTNET", "false").strip().lower() == "true"
# Unpaid crypto orders older than this stop being polled and the customer is
# notified (frees up BlockCypher's free rate limit instead of checking
# abandoned orders forever - default 60 minutes, adjust via Railway Variable).
ORDER_EXPIRY_MINUTES = int(os.environ.get("ORDER_EXPIRY_MINUTES", "60"))
# Optional BlockCypher token (free tier works without it at low volume)
BLOCKCYPHER_TOKEN = os.environ.get("BLOCKCYPHER_TOKEN", "")
# Extra % added to the crypto amount so small price moves don't under-pay
CRYPTO_PRICE_BUFFER_PCT = float(os.environ.get("CRYPTO_PRICE_BUFFER_PCT", "3.0"))

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
# What to ask for so we know what link/number to actually program onto the card.
LINK_PROMPTS = {
    "google": "What's the link to your Google Business review page? (This is what we'll program the card to open — you can find it in Google Business Profile under 'Get more reviews'.)",
    "trustpilot": "What's the link to your Trustpilot review page? (This is what we'll program the card to open.)",
    "tripadvisor": "What's the link to your Tripadvisor listing? (This is what we'll program the card to open.)",
    "whatsapp": "What's the invite link to your WhatsApp group (or your WhatsApp number)? This is what the card will open when tapped — e.g. https://chat.whatsapp.com/xxxxxxxx",
    "telegram": "What's the invite link to your Telegram channel or group? This is what the card will open when tapped — e.g. https://t.me/yourchannel",
    "social": "What are the links to your Instagram, TikTok and/or Facebook profiles? Send them all in one message — the card will let customers pick.",
}

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
            "Here's the problem with growing a WhatsApp group the normal way: "
            "you're either begging people to type in a long invite link, or "
            "you're manually adding numbers one by one — and one wrong move "
            "there gets your number flagged or banned. All that risk, for a "
            "handful of new members.\n\n"
            "This card skips all of it. No links to type, no numbers to add "
            "manually, nothing that touches your account's spam limits. A "
            "customer taps once and they're in your group — done, safely, "
            "every single time.\n\n"
            "🔑 *What You Get*\n"
            "📲 Tap to join — no typing a link, no saving a number\n"
            "🔗 QR code backup included\n"
            "🛡️ Zero risk to your WhatsApp number — no bulk adding, no bans\n"
            "📣 Grow a group you actually own, not a rented algorithm\n"
            "🛠️ Free setup — linked to your group's invite link\n"
            "🚚 Free UK shipping\n\n"
            "Every day without this is another day of chasing members the "
            "hard way — and risking your number doing it. Fix that below."
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


def is_admin(user_id) -> bool:
    return bool(ADMIN_CHAT_ID) and str(user_id) == str(ADMIN_CHAT_ID)


def format_order_details(order, lead=None) -> str:
    """Full order block for admin only (includes buyer contact + Telegram username)."""
    lines = [
        f"*Order #{order['id']}* — {order['status']}",
        f"Product: {order['product']} × {order['bundle_size']}",
        f"Amount: {format_gbp(order['amount_pence'] or 0)}",
        f"Shipping: {order['shipping_address'] or 'n/a'}",
    ]
    if order["pay_currency"] and order["pay_amount"]:
        lines.append(f"Paid: {order['pay_amount']} {str(order['pay_currency']).upper()}")
    if order["tracking_number"]:
        lines.append(f"Tracking: {order['tracking_number']}")
    if lead:
        uname = lead["username"] if "username" in lead.keys() and lead["username"] else ""
        lines.extend([
            f"Business: {lead['business_name']}",
            f"Contact: {lead['contact_name']}",
            f"Phone: {lead['phone']}",
            f"Email: {lead['email']}",
            f"Locations: {lead['locations']}",
            f"Card link: {lead['destination_link'] if 'destination_link' in lead.keys() else 'n/a'}",
            f"Telegram: @{uname}" if uname else "Telegram: (no username)",
        ])
    lines.append(f"Created: {order['created_at']}")
    return "\n".join(lines)


def format_supplier_order(order, lead=None) -> str:
    """Supplier-safe order block — shipping + product only. No buyer contact or Telegram."""
    lines = [
        f"*Order #{order['id']}*",
        f"Product: {order['product']} × {order['bundle_size']}",
        f"Shipping address:\n{order['shipping_address'] or 'n/a'}",
    ]
    if lead:
        link = lead["destination_link"] if "destination_link" in lead.keys() else ""
        if link:
            lines.append(f"Program card to: {link}")
        # Business name only (needed for packaging) — no phone/email/Telegram
        if lead["business_name"]:
            lines.append(f"Business name on package: {lead['business_name']}")
    return "\n".join(lines)


async def notify_fulfilment(bot, order, lead=None):
    """Send supplier-safe details only to the optional fulfilment chat."""
    if not FULFILMENT_CHAT_ID:
        return
    text = "📦 *New paid order — ready to fulfil*\n\n" + format_supplier_order(order, lead)
    try:
        await bot.send_message(FULFILMENT_CHAT_ID, text, parse_mode="Markdown")
    except Exception:
        logger.exception("Failed to notify fulfilment chat for order %s", order["id"])


async def mark_order_paid(bot, order_id: int):
    """
    Set order status to Paid, notify customer + admin + fulfilment chat.
    Safe to call more than once (idempotent if already Paid).
    """
    conn = db()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        conn.close()
        return False
    if order["status"] == "Paid":
        conn.close()
        return True

    conn.execute("UPDATE orders SET status=? WHERE id=?", ("Paid", order_id))
    conn.commit()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    lead = None
    if order["lead_id"]:
        lead = conn.execute("SELECT * FROM leads WHERE id=?", (order["lead_id"],)).fetchone()
    conn.close()

    try:
        await bot.send_message(
            order["telegram_user_id"],
            ("🧪 [TESTNET] " if TESTNET else "") +
            f"✅ Payment received for Order #{order['id']} — "
            f"{order['product']} x{order['bundle_size']}. Thank you! "
            "Your cards will ship soon.",
        )
    except Exception:
        logger.exception("Failed to notify customer for order %s", order_id)

    if ADMIN_CHAT_ID:
        try:
            await bot.send_message(
                ADMIN_CHAT_ID,
                ("🧪 [TESTNET — not a real order, not sent to fulfilment] " if TESTNET else "") +
                f"💰 Order #{order['id']} PAID — {order['product']} x{order['bundle_size']} "
                f"({format_gbp(order['amount_pence'])}). Ready to fulfil.\n\n"
                + format_order_details(order, lead),
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception("Failed to notify admin for order %s", order_id)

    if not TESTNET:
        await notify_fulfilment(bot, order, lead)
    return True


# --- Conversation states for the order form ---
(
    ASK_BUNDLE,
    ASK_BUSINESS_NAME,
    ASK_CONTACT_NAME,
    ASK_PHONE,
    ASK_EMAIL,
    ASK_LOCATIONS,
    ASK_LINK,
    ASK_ADDRESS,
    ASK_CRYPTO_CURRENCY,
) = range(9)


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
    conn.execute(
        """CREATE TABLE IF NOT EXISTS subscribers (
            telegram_user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_seen TEXT
        )"""
    )
    # Migration: add crypto payment columns to orders if they don't exist yet
    # (existing databases from before Phase 3 won't have these columns).
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(orders)")}
    for col, col_type in [
        ("payment_id", "TEXT"),
        ("pay_address", "TEXT"),
        ("pay_amount", "TEXT"),
        ("pay_currency", "TEXT"),
    ]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {col_type}")

    # Migration: add destination_link + username to leads.
    lead_cols = {row["name"] for row in conn.execute("PRAGMA table_info(leads)")}
    if "destination_link" not in lead_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN destination_link TEXT")
    if "username" not in lead_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN username TEXT")

    conn.commit()
    conn.close()


def make_qr_png_bytes(data: str) -> bytes:
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def own_wallet_enabled() -> bool:
    """True when at least one xpub is configured for DIY crypto payments."""
    return bool(BTC_XPUB or LTC_XPUB)


def get_crypto_price_gbp(coin_id: str) -> Decimal | None:
    """Live GBP price from CoinGecko. coin_id is 'bitcoin' or 'litecoin'."""
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin_id, "vs_currencies": "gbp"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            price = data.get(coin_id, {}).get("gbp")
            if price is not None:
                return Decimal(str(price))
        logger.error("CoinGecko price failed: %s %s", resp.status_code, resp.text)
    except Exception:
        logger.exception("CoinGecko price request error")
    return None


def derive_receive_address(currency: str, index: int) -> str | None:
    """
    Derive a unique native-SegWit (bech32) receive address from the account xpub.
    Cake Wallet uses BIP84 account xpubs (depth 3). We derive change=0 / address=index.
    currency: 'btc' or 'ltc'
    """
    xpub = BTC_XPUB if currency == "btc" else LTC_XPUB
    if not xpub:
        return None
    if currency == "btc" and TESTNET:
        hrp = "tb"  # Bitcoin Testnet3 bech32 prefix - same key material, test-network format
    else:
        hrp = "bc" if currency == "btc" else "ltc"
    try:
        ctx = Bip32Slip10Secp256k1.FromExtendedKey(xpub)
        # External chain (0) then address index
        child = ctx.ChildKey(0).ChildKey(int(index))
        pub_bytes = child.PublicKey().RawCompressed().ToBytes()
        return P2WPKHAddr.EncodeKey(pub_bytes, hrp=hrp)
    except Exception:
        logger.exception("Failed to derive %s address at index %s", currency, index)
        return None


def create_own_wallet_payment(amount_gbp: float, order_id: int, currency: str):
    """
    Build a DIY payment: live price + buffer + unique address from xpub.
    Returns dict with pay_address, pay_amount (str), pay_currency, or None on failure.
    """
    coin_id = "bitcoin" if currency == "btc" else "litecoin"
    price = get_crypto_price_gbp(coin_id)
    if price is None or price <= 0:
        return None

    # Apply buffer so small price moves don't leave the order underpaid
    buffered = Decimal(str(amount_gbp)) * (Decimal("1") + Decimal(str(CRYPTO_PRICE_BUFFER_PCT)) / Decimal("100"))
    raw_amount = buffered / price
    # Round up to 8 decimal places (satoshi precision)
    pay_amount = raw_amount.quantize(Decimal("0.00000001"), rounding=ROUND_UP)

    address = derive_receive_address(currency, order_id)
    if not address:
        return None

    return {
        "pay_address": address,
        "pay_amount": str(pay_amount),
        "pay_currency": currency,
        "payment_id": f"own_{currency}_{order_id}",  # local id for our DB
    }


def mempool_space_address_received(address: str) -> int | None:
    """
    Testnet-only fallback: BlockCypher's Bitcoin Testnet3 indexer is
    unreliable (confirmed empirically - it can miss real, confirmed-on-chain
    testnet transactions entirely). mempool.space runs a well-maintained
    public testnet API with no key required, so we use it for TESTNET mode
    instead. Returns confirmed + unconfirmed satoshis received, or None on error.
    """
    url = f"https://mempool.space/testnet/api/address/{address}"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            chain = data.get("chain_stats", {})
            mempool = data.get("mempool_stats", {})
            return int(chain.get("funded_txo_sum", 0)) + int(mempool.get("funded_txo_sum", 0))
        logger.warning("mempool.space balance %s: %s %s", address, resp.status_code, resp.text[:200])
    except Exception:
        logger.exception("mempool.space request error for %s", address)
    return None


def blockcypher_address_received(currency: str, address: str) -> int | None:
    """
    Return total received in satoshis (or None on error).
    Uses mempool.space for BTC testnet (BlockCypher's testnet3 indexer is
    unreliable); BlockCypher for everything else. Optional token raises
    BlockCypher rate limits on mainnet.
    """
    if currency == "btc" and TESTNET:
        return mempool_space_address_received(address)
    coin = "btc" if currency == "btc" else "ltc"
    url = f"https://api.blockcypher.com/v1/{coin}/main/addrs/{address}/balance"
    params = {}
    if BLOCKCYPHER_TOKEN:
        params["token"] = BLOCKCYPHER_TOKEN
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            # total_received includes confirmed + we also accept unconfirmed for speed
            return int(data.get("total_received", 0)) + int(data.get("unconfirmed_balance", 0))
        logger.warning("BlockCypher balance %s: %s %s", address, resp.status_code, resp.text[:200])
    except Exception:
        logger.exception("BlockCypher request error for %s", address)
    return None


def crypto_amount_to_satoshis(amount_str: str) -> int:
    """Convert a decimal crypto amount string to integer satoshis."""
    return int((Decimal(amount_str) * Decimal("100000000")).to_integral_value(rounding=ROUND_UP))


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
    user = update.effective_user
    conn = db()
    already_known = conn.execute(
        "SELECT 1 FROM subscribers WHERE telegram_user_id=?", (user.id,)
    ).fetchone()
    if not already_known:
        conn.execute(
            "INSERT INTO subscribers (telegram_user_id, username, first_seen) VALUES (?, ?, ?)",
            (user.id, user.username or "", datetime.utcnow().isoformat()),
        )
        conn.commit()
    conn.close()

    if not already_known and SUBSCRIBER_LOG_CHAT_ID:
        uname = f"@{user.username}" if user.username else "(no username)"
        try:
            await context.bot.send_message(
                SUBSCRIBER_LOG_CHAT_ID,
                f"👤 New subscriber: {uname} — ID {user.id}",
            )
        except Exception:
            logger.exception("Failed to post new subscriber to log chat")

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

    if data == "dismiss_broadcast":
        try:
            await query.delete_message()
        except Exception:
            pass
        return

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

    elif data.startswith("checkpay_"):
        order_id = data.replace("checkpay_", "")
        conn = db()
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        conn.close()
        if not order or not order["pay_address"]:
            await query.answer("Order not found.", show_alert=True)
            return
        if order["status"] == "Paid":
            await query.answer("✅ Payment already confirmed. Thank you!", show_alert=True)
            return
        currency = (order["pay_currency"] or "").lower()
        received = blockcypher_address_received(currency, order["pay_address"])
        if received is None:
            await query.answer("Couldn't reach the blockchain lookup — try again shortly.", show_alert=True)
            return
        needed = crypto_amount_to_satoshis(order["pay_amount"] or "0")
        # Allow a tiny under-pay tolerance (1% of needed or 1000 sats, whichever larger)
        tolerance = max(needed // 100, 1000)
        if received >= needed - tolerance:
            await mark_order_paid(context.bot, int(order_id))
            await query.answer("✅ Payment confirmed! Thank you.", show_alert=True)
        else:
            await query.answer(
                f"Still waiting — received {received / 1e8:.8f} {currency.upper()}, "
                f"need {order['pay_amount']} {currency.upper()}. "
                "I'll notify you automatically once it's paid.",
                show_alert=True,
            )

    elif data.startswith("marksent_"):
        if not is_admin(query.from_user.id):
            await query.answer("Admin only.", show_alert=True)
            return
        order_id = data.replace("marksent_", "")
        conn = db()
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            conn.close()
            await query.answer("Order not found.", show_alert=True)
            return
        conn.execute("UPDATE orders SET status=? WHERE id=?", ("Sent to supplier", order_id))
        conn.commit()
        conn.close()
        await query.answer(f"Order #{order_id} marked Sent to supplier.", show_alert=True)
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(
                            f"🚚 Mark #{order_id} Shipped",
                            callback_data=f"markshipped_{order_id}",
                        )
                    ]]
                )
            )
        except Exception:
            pass
        if ADMIN_CHAT_ID:
            await context.bot.send_message(
                ADMIN_CHAT_ID,
                f"📤 Order #{order_id} marked *Sent to supplier*.",
                parse_mode="Markdown",
            )

    elif data.startswith("markshipped_"):
        if not is_admin(query.from_user.id):
            await query.answer("Admin only.", show_alert=True)
            return
        order_id = data.replace("markshipped_", "")
        conn = db()
        order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            conn.close()
            await query.answer("Order not found.", show_alert=True)
            return
        conn.execute("UPDATE orders SET status=? WHERE id=?", ("Shipped", order_id))
        conn.commit()
        conn.close()
        await query.answer(f"Order #{order_id} marked Shipped.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        try:
            await context.bot.send_message(
                order["telegram_user_id"],
                f"📬 Order #{order_id} has shipped! "
                f"{order['product']} x{order['bundle_size']}. "
                "Use Track Order any time for status.",
            )
        except Exception:
            logger.exception("Failed to notify customer of shipment for order %s", order_id)
        if ADMIN_CHAT_ID:
            await context.bot.send_message(
                ADMIN_CHAT_ID,
                f"🚚 Order #{order_id} marked *Shipped*. Customer notified.",
                parse_mode="Markdown",
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
    product_key = context.user_data.get("product_key", "")
    prompt = LINK_PROMPTS.get(
        product_key,
        "What's the link or page you'd like this card to open when tapped?",
    )
    await update.message.reply_text(prompt)
    return ASK_LINK


async def ask_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["destination_link"] = update.message.text
    await update.message.reply_text("Great — what's the shipping address for the card(s)?")
    return ASK_ADDRESS


async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["address"] = update.message.text
    ud = context.user_data

    conn = db()
    cur = conn.execute(
        """INSERT INTO leads (telegram_user_id, username, business_name, contact_name, phone, email, locations, product, bundle_size, destination_link, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            update.effective_user.id,
            update.effective_user.username or "",
            ud["business_name"],
            ud["contact_name"],
            ud["phone"],
            ud["email"],
            ud["locations"],
            ud["product"],
            ud["bundle_size"],
            ud.get("destination_link", ""),
            datetime.utcnow().isoformat(),
        ),
    )
    lead_id = cur.lastrowid
    conn.commit()
    conn.close()
    context.user_data["lead_id"] = lead_id

    if ADMIN_CHAT_ID:
        uname = update.effective_user.username or "(no username)"
        await context.bot.send_message(
            ADMIN_CHAT_ID,
            f"🟢 New lead #{lead_id}: {ud['business_name']} ({ud['product']} x{ud['bundle_size']}) — "
            f"{ud['contact_name']}, {ud['phone']}, {ud['email']}\n"
            f"🔗 Card link: {ud.get('destination_link', 'not provided')}\n"
            f"Telegram: @{uname}",
        )

    amount_pence = ud["amount_pence"]

    if own_wallet_enabled():
        # DIY crypto: customer picks BTC or LTC, then we generate a unique address from your xpub.
        buttons = []
        row = []
        if BTC_XPUB:
            row.append(InlineKeyboardButton("₿ Bitcoin (BTC)", callback_data="cryptocur_btc"))
        if LTC_XPUB and not TESTNET:
            row.append(InlineKeyboardButton("Ł Litecoin (LTC)", callback_data="cryptocur_ltc"))
        if row:
            buttons.append(row)
        testnet_note = (
            "\n\n🧪 *TESTNET MODE* — practice coins only, this is not real money. "
            "Litecoin is unavailable in this mode.\n"
            if TESTNET else ""
        )
        await update.message.reply_text(
            f"Almost done — {ud['bundle_size']} x {ud['product']} "
            f"({format_gbp(amount_pence)}).\n\nWhich crypto would you like to pay with?" + testnet_note,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )
        return ASK_CRYPTO_CURRENCY

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


async def select_crypto_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pay_currency = query.data.replace("cryptocur_", "")  # "btc" or "ltc"
    ud = context.user_data
    lead_id = ud["lead_id"]
    amount_pence = ud["amount_pence"]
    amount_gbp = amount_pence / 100

    conn = db()
    cur = conn.execute(
        """INSERT INTO orders (lead_id, telegram_user_id, product, bundle_size, shipping_address, amount_pence, status, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            lead_id,
            query.from_user.id,
            ud["product"],
            ud["bundle_size"],
            ud["address"],
            amount_pence,
            "Awaiting crypto payment",
            datetime.utcnow().isoformat(),
        ),
    )
    order_id = cur.lastrowid
    conn.commit()
    conn.close()

    payment = create_own_wallet_payment(amount_gbp, order_id, pay_currency)

    if payment and payment.get("pay_address"):
        pay_address = payment["pay_address"]
        pay_amount = payment["pay_amount"]
        actual_currency = payment["pay_currency"]
        payment_id = payment["payment_id"]

        conn = db()
        conn.execute(
            "UPDATE orders SET payment_id=?, pay_address=?, pay_amount=?, pay_currency=? WHERE id=?",
            (str(payment_id), pay_address, str(pay_amount), actual_currency, order_id),
        )
        conn.commit()
        conn.close()

        # BIP21-style URI so wallet apps pre-fill the amount
        scheme = "bitcoin" if actual_currency == "btc" else "litecoin"
        qr_data = f"{scheme}:{pay_address}?amount={pay_amount}"
        qr_bytes = make_qr_png_bytes(qr_data)
        testnet_banner = (
            "🧪 *TESTNET — practice coins only, not real money* 🧪\n\n" if TESTNET else ""
        )
        caption = (
            testnet_banner +
            f"💰 *Order #{order_id} — Pay with crypto*\n\n"
            f"Send exactly: `{pay_amount} {actual_currency.upper()}`\n"
            f"To address: `{pay_address}`\n\n"
            "Scan the QR code with your wallet app, or copy the address above. "
            "This address is unique to this order — don't reuse it.\n\n"
            "I'll notify you automatically the moment payment is received.\n\n"
            f"⏱ This address stays active for {ORDER_EXPIRY_MINUTES} minutes."
        )
        await query.delete_message()
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=io.BytesIO(qr_bytes),
            caption=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔄 Check Payment Status", callback_data=f"checkpay_{order_id}")]]
            ),
        )
        if ADMIN_CHAT_ID:
            await context.bot.send_message(
                ADMIN_CHAT_ID,
                f"⏳ Order #{order_id} awaiting crypto payment — "
                f"{pay_amount} {actual_currency.upper()} ({format_gbp(amount_pence)})\n"
                f"Address: `{pay_address}`",
                parse_mode="Markdown",
            )
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"Thanks! Your order for {ud['bundle_size']} x {ud['product']} "
            f"({format_gbp(amount_pence)}) is saved. Crypto checkout couldn't be "
            "generated right now — the team will follow up to take payment.",
            reply_markup=main_menu_keyboard(),
        )
    return ConversationHandler.END


async def clearpending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: immediately cancel all unpaid crypto orders, stopping them
    from being polled. Useful for clearing out old test orders."""
    if not is_admin(update.effective_user.id):
        return
    conn = db()
    rows = conn.execute("SELECT id FROM orders WHERE status='Awaiting crypto payment'").fetchall()
    conn.execute("UPDATE orders SET status='Cancelled' WHERE status='Awaiting crypto payment'")
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Cleared {len(rows)} pending unpaid order(s).")


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: reply with the numeric ID of whatever chat this is sent in.
    Use this in your fulfilment group (with the bot added as a member) to get
    its ID for FULFILMENT_CHAT_ID - no third-party bot needed."""
    if not is_admin(update.effective_user.id):
        return  # silently ignore non-admins
    chat = update.effective_chat
    await update.message.reply_text(
        f"Chat ID: `{chat.id}`\nType: {chat.type}\nTitle: {chat.title or '(no title - private chat)'}",
        parse_mode="Markdown",
    )


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
        uname = r["username"] if "username" in r.keys() and r["username"] else "(none)"
        chunks.append(
            f"*Lead #{r['id']}* — {r['created_at']}\n"
            f"Business: {r['business_name']}\n"
            f"Contact: {r['contact_name']}\n"
            f"Phone: {r['phone']}\n"
            f"Email: {r['email']}\n"
            f"Locations: {r['locations']}\n"
            f"Product: {r['product']} x{r['bundle_size']}\n"
            f"Card link: {r['destination_link'] if 'destination_link' in r.keys() else 'n/a'}\n"
            f"Telegram: @{uname}\n"
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


async def exportsubscribers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: downloadable CSV of every subscriber (everyone who has
    sent /start) - your full list, in one file you can keep anywhere safe."""
    if not is_admin(update.effective_user.id):
        return
    conn = db()
    rows = conn.execute("SELECT * FROM subscribers ORDER BY first_seen ASC").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("No subscribers yet.")
        return

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Telegram User ID", "Username", "First Seen (UTC)"])
    for r in rows:
        writer.writerow([r["telegram_user_id"], r["username"] or "", r["first_seen"]])

    csv_bytes = buf.getvalue().encode("utf-8")
    filename = f"subscribers_export_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"
    await update.message.reply_document(
        document=io.BytesIO(csv_bytes),
        filename=filename,
        caption=f"👥 {len(rows)} subscriber(s) total.",
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: starts broadcast mode. Send /broadcast (optionally with a
    product key, e.g. /broadcast whatsapp), THEN just send the message you
    want broadcast as your very next message (text or photo) - no need to
    reply to anything. That next message is captured automatically by
    broadcast_capture() below."""
    if not is_admin(update.effective_user.id):
        return

    product_key = None
    if context.args:
        key = context.args[0].strip().lower()
        if key not in PRODUCTS:
            await update.message.reply_text(
                f"Unknown product key '{key}'. Valid keys: " + ", ".join(PRODUCTS.keys())
            )
            return
        product_key = key

    context.user_data["awaiting_broadcast"] = True
    context.user_data["broadcast_product_key"] = product_key
    await update.message.reply_text(
        "📣 Okay — now just send me the message to broadcast (text, or a "
        "photo with a caption). Send it as a normal message, right here, "
        "right now. Or send /cancel to back out."
    )


async def broadcast_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs BEFORE all other handlers. If the admin is in broadcast mode
    (just sent /broadcast), this grabs their very next message and sends it
    to every subscriber - no reply gesture required. Anyone else, or the
    admin when not in broadcast mode, passes straight through untouched."""
    if not update.effective_user or not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("awaiting_broadcast"):
        return

    context.user_data["awaiting_broadcast"] = False
    product_key = context.user_data.pop("broadcast_product_key", None)

    if update.message and update.message.text and update.message.text.strip() == "/cancel":
        await update.message.reply_text("Broadcast cancelled.")
        raise ApplicationHandlerStop

    buttons = []
    if product_key:
        buttons.append([InlineKeyboardButton("🛍️ Shop This Product", callback_data=f"product_{product_key}")])
    buttons.append([InlineKeyboardButton("✖️ Dismiss", callback_data="dismiss_broadcast")])
    reply_markup = InlineKeyboardMarkup(buttons)

    conn = db()
    subs = conn.execute("SELECT telegram_user_id FROM subscribers").fetchall()
    conn.close()

    sent, failed = 0, 0
    status_msg = await update.message.reply_text(f"Broadcasting to {len(subs)} subscriber(s)...")
    for row in subs:
        uid = row["telegram_user_id"]
        try:
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                reply_markup=reply_markup,
            )
            sent += 1
        except Exception:
            failed += 1  # e.g. user blocked the bot - just skip them
        await asyncio.sleep(0.05)  # stay comfortably under Telegram's rate limits

    await status_msg.edit_text(f"✅ Broadcast done — sent to {sent}, failed for {failed}.")
    raise ApplicationHandlerStop


async def exportorders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: send a downloadable CSV of paid/in-fulfilment orders -
    supplier-safe columns only (no customer contact details)."""
    if not is_admin(update.effective_user.id):
        return
    conn = db()
    rows = conn.execute(
        """SELECT * FROM orders
           WHERE status IN ('Paid', 'Sent to supplier')
           ORDER BY id ASC"""
    ).fetchall()
    if not rows:
        conn.close()
        await update.message.reply_text("No paid orders to export right now.")
        return

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Order #", "Status", "Product", "Qty", "Shipping Address", "Program Card To", "Business Name"])
    for order in rows:
        lead = None
        if order["lead_id"]:
            lead = conn.execute("SELECT * FROM leads WHERE id=?", (order["lead_id"],)).fetchone()
        link = lead["destination_link"] if lead and "destination_link" in lead.keys() else ""
        business = lead["business_name"] if lead else ""
        writer.writerow([
            order["id"], order["status"], order["product"], order["bundle_size"],
            order["shipping_address"] or "", link or "", business or "",
        ])
    conn.close()

    csv_bytes = buf.getvalue().encode("utf-8")
    filename = f"orders_export_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"
    await update.message.reply_document(
        document=io.BytesIO(csv_bytes),
        filename=filename,
        caption=f"📦 {len(rows)} order(s) — supplier-safe export (no customer contact info).",
    )


async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: list paid / in-fulfilment orders with mark-as-sent buttons."""
    if not is_admin(update.effective_user.id):
        return  # silently ignore non-admins

    conn = db()
    # Show orders that still need action (Paid or Sent to supplier)
    rows = conn.execute(
        """SELECT * FROM orders
           WHERE status IN ('Paid', 'Sent to supplier')
           ORDER BY id DESC LIMIT 30"""
    ).fetchall()

    if not rows:
        conn.close()
        await update.message.reply_text(
            "No paid orders waiting for fulfilment right now.\n"
            "(Orders move here once crypto/card payment is confirmed.)"
        )
        return

    for order in rows:
        lead = None
        if order["lead_id"]:
            lead = conn.execute(
                "SELECT * FROM leads WHERE id=?", (order["lead_id"],)
            ).fetchone()
        text = format_order_details(order, lead)

        buttons = []
        if order["status"] == "Paid":
            buttons.append([
                InlineKeyboardButton(
                    f"📤 Mark #{order['id']} Sent to supplier",
                    callback_data=f"marksent_{order['id']}",
                )
            ])
        elif order["status"] == "Sent to supplier":
            buttons.append([
                InlineKeyboardButton(
                    f"🚚 Mark #{order['id']} Shipped",
                    callback_data=f"markshipped_{order['id']}",
                )
            ])

        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
    conn.close()


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


async def check_pending_payments_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs on a timer. Checks every order still awaiting crypto payment via
    BlockCypher. When enough crypto has arrived, marks Paid and notifies everyone.
    Orders older than ORDER_EXPIRY_MINUTES are marked Expired, the customer is
    notified, and it stops being polled - this is what keeps BlockCypher's free
    rate limit from being exhausted by abandoned/test orders piling up."""
    if not own_wallet_enabled():
        return
    conn = db()
    cutoff = (datetime.utcnow() - timedelta(minutes=ORDER_EXPIRY_MINUTES)).isoformat()
    expired = conn.execute(
        "SELECT * FROM orders WHERE status='Awaiting crypto payment' AND created_at < ?",
        (cutoff,),
    ).fetchall()
    if expired:
        conn.execute(
            "UPDATE orders SET status='Expired' WHERE status='Awaiting crypto payment' AND created_at < ?",
            (cutoff,),
        )
        conn.commit()
        logger.info("Expired %d stale unpaid crypto order(s)", len(expired))
    pending = conn.execute(
        "SELECT * FROM orders WHERE status='Awaiting crypto payment' AND pay_address IS NOT NULL"
    ).fetchall()
    conn.close()

    for order in expired:
        try:
            await context.bot.send_message(
                order["telegram_user_id"],
                f"⌛ The payment window for Order #{order['id']} has closed "
                f"(no payment received within {ORDER_EXPIRY_MINUTES} minutes). "
                "No worries - send /start any time to place a new order.",
            )
        except Exception:
            logger.exception("Failed to notify customer of expiry for order %s", order["id"])

    for order in pending:
        currency = (order["pay_currency"] or "").lower()
        if not currency or not order["pay_address"]:
            continue
        received = blockcypher_address_received(currency, order["pay_address"])
        if received is None:
            continue
        needed = crypto_amount_to_satoshis(order["pay_amount"] or "0")
        tolerance = max(needed // 100, 1000)
        if received < needed - tolerance:
            continue
        await mark_order_paid(context.bot, order["id"])


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payload = update.message.successful_payment.invoice_payload
    lead_id = payload.replace("lead_", "")
    conn = db()
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    cur = conn.execute(
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
    order_id = cur.lastrowid
    conn.commit()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    conn.close()

    await update.message.reply_text(
        "🎉 Payment received! Your cards will be printed and shipped shortly. "
        "Use Track Order any time to check status.",
        reply_markup=main_menu_keyboard(),
    )
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                ADMIN_CHAT_ID,
                f"💰 Order #{order_id} PAID (card) — ready to fulfil.\n\n"
                + format_order_details(order, lead),
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception("Failed to notify admin of card payment %s", order_id)
    await notify_fulfilment(context.bot, order, lead)


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
            ASK_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_link)],
            ASK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_address)],
            ASK_CRYPTO_CURRENCY: [CallbackQueryHandler(select_crypto_currency, pattern="^cryptocur_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("leads", leads_command))
    app.add_handler(CommandHandler("chatid", chatid_command))
    app.add_handler(CommandHandler("clearpending", clearpending_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("exportorders", exportorders_command))
    app.add_handler(MessageHandler(filters.ALL, broadcast_capture), group=-1)
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("exportsubscribers", exportsubscribers_command))
    if own_wallet_enabled():
        app.job_queue.run_repeating(check_pending_payments_job, interval=60, first=20)
    app.add_handler(order_conv)
    app.add_handler(CallbackQueryHandler(button_router))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
