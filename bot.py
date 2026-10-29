import asyncio
import html
import io
import logging
import os
import re
import threading
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# -------------------- Render health server --------------------

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Anime Poster Bot is running!", 200


@web_app.route("/health")
def health():
    return "OK", 200


def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port, use_reloader=False)


# -------------------- Configuration --------------------

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TMDB_TOKEN = os.getenv("TMDB_READ_ACCESS_TOKEN", "").strip()
TMDB_KEY = os.getenv("TMDB_API_KEY", "").strip()

# Set these in Render Environment to point buttons at your real destinations.
WATCH_NOW_URL = os.getenv(
    "WATCH_NOW_URL", "https://t.me/AnimeNetworkTamil"
).strip()
JOIN_TELEGRAM_URL = os.getenv(
    "JOIN_TELEGRAM_URL", "https://t.me/AnimeNetworkTamil"
).strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@AnimeNetworkTamil").strip()

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
LOGO = Path(__file__).parent / "assets" / "channel_logo.jpg"

W, H = 1920, 1080
DEFAULT_QUALITIES = ["480P", "720P", "1080P"]
DEFAULT_QUALITY = "  •  ".join(DEFAULT_QUALITIES)
DEFAULT_AUDIO = "Tamil Audio"
DEFAULT_JOIN = f"Telegram Join → {CHANNEL_USERNAME}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("AnimeNetworkTamil")

http = requests.Session()
http.headers["Accept"] = "application/json"
if TMDB_TOKEN:
    http.headers["Authorization"] = "Bearer " + TMDB_TOKEN


# -------------------- TMDB helpers --------------------

def api(path, params=None):
    params = dict(params or {})
    if not TMDB_TOKEN:
        if not TMDB_KEY:
            raise RuntimeError(
                "Add TMDB_READ_ACCESS_TOKEN or TMDB_API_KEY to Render Environment."
            )
        params["api_key"] = TMDB_KEY

    response = http.get(API + path, params=params, timeout=25)
    if response.status_code == 401:
        raise RuntimeError(
            "TMDB credential rejected. Check your Render Environment variables."
        )
    response.raise_for_status()
    return response.json()


def search(query):
    results = []
    for kind in ("tv", "movie"):
        data = api(
            "/search/" + kind,
            {
                "query": query,
                "include_adult": "false",
                "language": "en-US",
                "page": 1,
            },
        )
        for item in data.get("results", []):
            item["kind"] = kind
            item["title"] = (
                item.get("name")
                or item.get("title")
                or item.get("original_name")
                or item.get("original_title")
                or "Untitled"
            )
            item["date"] = (
                item.get("first_air_date") or item.get("release_date") or ""
            )
            results.append(item)

    results.sort(key=lambda item: item.get("popularity", 0), reverse=True)
    return [item for item in results if item.get("poster_path")][:8]


def details(item):
    data = api(f"/{item['kind']}/{item['id']}", {"language": "en-US"})
    return {
        "title": data.get("name") or data.get("title") or item["title"],
        "overview": (
            data.get("overview")
            or item.get("overview")
            or "Synopsis unavailable."
        ).strip(),
        "year": (
            data.get("first_air_date")
            or data.get("release_date")
            or item.get("date")
            or ""
        )[:4],
        "rating": data.get("vote_average") or item.get("vote_average") or 0,
        "genres": [
            genre["name"]
            for genre in data.get("genres", [])
            if genre.get("name")
        ][:3],
        "poster_path": data.get("poster_path") or item.get("poster_path"),
    }


# -------------------- Image helpers --------------------

def font(size, bold=False):
    paths = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
        (
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"
        ),
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def wrap(draw, text, fnt, maxw):
    lines, current = [], ""
    for word in text.split():
        test = (current + " " + word).strip()
        if draw.textbbox((0, 0), test, font=fnt)[2] <= maxw:
            current = test
        else:
            if current:
                lines.append(current)
            if draw.textbbox((0, 0), word, font=fnt)[2] > maxw:
                current = ""
                for char in word:
                    candidate = current + char
                    if draw.textbbox((0, 0), candidate, font=fnt)[2] <= maxw:
                        current = candidate
                    else:
                        if current:
                            lines.append(current)
                        current = char
            else:
                current = word
    if current:
        lines.append(current)
    return lines


def put_lines(draw, lines, xy, fnt, fill, gap=12, stroke=0, stroke_fill="#000"):
    x, y = xy
    for line in lines:
        draw.text(
            (x, y), line, font=fnt, fill=fill,
            stroke_width=stroke, stroke_fill=stroke_fill,
        )
        bounds = draw.textbbox((x, y), line, font=fnt, stroke_width=stroke)
        y += bounds[3] - bounds[1] + gap
    return y


def centered_text(draw, box, text, fnt, fill):
    x1, y1, x2, y2 = box
    bounds = draw.textbbox((0, 0), text, font=fnt)
    text_w = bounds[2] - bounds[0]
    text_h = bounds[3] - bounds[1]
    x = x1 + (x2 - x1 - text_w) // 2
    y = y1 + (y2 - y1 - text_h) // 2 - bounds[1]
    draw.text((x, y), text, font=fnt, fill=fill)


def draw_quality_buttons(draw, x, y, max_width):
    """Large, individually readable 480P / 720P / 1080P badges."""
    labels = DEFAULT_QUALITIES
    gap = 20
    button_h = 70
    button_w = 190
    label_font = font(30, True)

    total_width = len(labels) * button_w + (len(labels) - 1) * gap
    if total_width > max_width:
        button_w = (max_width - gap * (len(labels) - 1)) // len(labels)

    for index, label in enumerate(labels):
        x1 = x + index * (button_w + gap)
        x2 = x1 + button_w
        draw.rounded_rectangle(
            (x1, y, x2, y + button_h),
            radius=16,
            fill="#FFC857",
            outline="#FFFFFF",
            width=3,
        )
        centered_text(
            draw, (x1, y, x2, y + button_h), label, label_font, "#171923"
        )


def draw_telegram_join(draw, x, y, width):
    """Draw a large blue Telegram-style banner with a paper-plane mark."""
    height = 82
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=19,
        fill="#168DE2",
        outline="#FFFFFF",
        width=3,
    )

    # White circular badge and blue paper-plane symbol.
    cx, cy, radius = x + 51, y + height // 2, 29
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill="#FFFFFF",
    )
    draw.polygon(
        [
            (cx - 17, cy - 10),
            (cx + 19, cy - 19),
            (cx + 8, cy + 19),
            (cx, cy + 5),
            (cx - 9, cy + 12),
            (cx - 7, cy - 1),
        ],
        fill="#168DE2",
    )

    label = f"JOIN TELEGRAM  {CHANNEL_USERNAME}"
    label_font = font(30, True)
    available_width = width - 105
    while draw.textbbox((0, 0), label, font=label_font)[2] > available_width:
        if label_font.size <= 18:
            break
        label_font = font(label_font.size - 1, True)

    bounds = draw.textbbox((0, 0), label, font=label_font)
    text_h = bounds[3] - bounds[1]
    draw.text(
        (x + 96, y + (height - text_h) // 2 - bounds[1]),
        label,
        font=label_font,
        fill="#FFFFFF",
    )


def make_poster(info):
    if not info.get("poster_path"):
        raise RuntimeError("No poster artwork found.")

    response = http.get(IMG + "w780" + info["poster_path"], timeout=30)
    response.raise_for_status()
    poster = Image.open(io.BytesIO(response.content)).convert("RGB")

    bg = ImageOps.fit(
        poster, (W, H), method=Image.Resampling.LANCZOS
    ).filter(ImageFilter.GaussianBlur(28))
    bg = Image.eval(bg, lambda pixel: int(pixel * 0.36)).convert("RGBA")
    bg.alpha_composite(Image.new("RGBA", (W, H), (5, 7, 18, 125)))
    canvas = bg

    # Original vertical poster artwork on the left.
    poster_w, poster_h = poster.size
    display_h = 820
    display_w = int(poster_w * display_h / poster_h)
    if display_w > 590:
        display_w, display_h = 590, int(poster_h * 590 / poster_w)
    poster = poster.resize((display_w, display_h), Image.Resampling.LANCZOS)
    poster_x, poster_y = 72, (H - display_h) // 2

    shadow = Image.new(
        "RGBA", (display_w + 50, display_h + 50), (0, 0, 0, 0)
    )
    ImageDraw.Draw(shadow).rounded_rectangle(
        (20, 20, display_w + 30, display_h + 30),
        radius=18,
        fill=(0, 0, 0, 180),
    )
    canvas.alpha_composite(
        shadow.filter(ImageFilter.GaussianBlur(18)),
        (poster_x - 24, poster_y - 24),
    )
    canvas.alpha_composite(poster.convert("RGBA"), (poster_x, poster_y))
    draw = ImageDraw.Draw(canvas)

    tx, maxw = 735, 1090
    title = info["title"].upper()
    title_font = font(72 if len(title) < 23 else 58, True)
    y = put_lines(
        draw, wrap(draw, title, title_font, maxw)[:2],
        (tx, 95), title_font, "#FFFFFF", 8, 2, "#101322"
    )
    y += 18
    draw.rounded_rectangle(
        (tx, y, tx + 175, y + 9), radius=4, fill="#FF3158"
    )
    y += 35
    draw.text((tx, y), "SYNOPSIS", font=font(29, True), fill="#FFB547")
    y += 48

    synopsis = re.sub(
        r"\s+", " ", info.get("overview", "")
    ).strip() or "Synopsis unavailable."
    if len(synopsis) > 620:
        synopsis = synopsis[:620].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    synopsis_lines = wrap(draw, synopsis, font(25), maxw)[:8]
    y = put_lines(
        draw, synopsis_lines, (tx, y), font(25), "#F0F1FA", 10
    )

    metadata = []
    if info.get("year"):
        metadata.append(info["year"])
    if info.get("rating"):
        try:
            metadata.append(f"TMDB ★ {float(info['rating']):.1f}/10")
        except (ValueError, TypeError):
            pass
    metadata.extend(info.get("genres", []))

    meta_y = min(max(y + 8, 620), 680)
    if metadata:
        meta_font = font(21, True)
        put_lines(
            draw,
            wrap(draw, "  •  ".join(metadata), meta_font, maxw)[:2],
            (tx, meta_y),
            meta_font,
            "#FFCF78",
            7,
        )

    # Larger quality buttons, Tamil audio and Telegram banner.
    draw_quality_buttons(draw, tx, 755, maxw)
    draw.text(
        (tx, 845),
        "🔊  " + DEFAULT_AUDIO,
        font=font(27, True),
        fill="#FFFFFF",
        stroke_width=1,
        stroke_fill="#111222",
    )
    # Leave room on the far right for the existing channel logo.
    draw_telegram_join(draw, tx, 910, 930)

    # Existing channel logo and watermark.
    if LOGO.exists():
        try:
            logo = Image.open(LOGO).convert("RGBA")
            logo.thumbnail((155, 105), Image.Resampling.LANCZOS)
            logo.putalpha(
                logo.getchannel("A").point(lambda alpha: int(alpha * 0.95))
            )
            canvas.alpha_composite(
                logo, (W - logo.width - 40, H - logo.height - 34)
            )
        except Exception:
            log.exception("Could not load channel logo")

    draw = ImageDraw.Draw(canvas)
    draw.text(
        (W - 35, H - 8),
        "ANIMENETWORKTAMIL",
        font=font(19, True),
        fill="#FFFFFF",
        anchor="rs",
        stroke_width=2,
        stroke_fill="#111222",
    )

    output = io.BytesIO()
    canvas.convert("RGB").save(
        output, format="JPEG", quality=94, optimize=True
    )
    output.seek(0)
    safe_title = re.sub(
        r"[^a-zA-Z0-9_-]+", "-", info["title"]
    ).strip("-").lower()
    output.name = (safe_title or "anime") + "-AnimeNetworkTamil.jpg"
    return output


def poster_keyboard():
    """Real clickable Telegram buttons shown below the poster photo."""
    rows = []
    if WATCH_NOW_URL:
        rows.append([
            InlineKeyboardButton("▶️ Watch Now", url=WATCH_NOW_URL)
        ])
    if JOIN_TELEGRAM_URL:
        rows.append([
            InlineKeyboardButton("✈️ Join Telegram", url=JOIN_TELEGRAM_URL)
        ])
    return InlineKeyboardMarkup(rows) if rows else None


# -------------------- Telegram commands --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🎌 Welcome to AnimeNetworkTamil Poster Bot!\n\n"
        "Create 16:9 (1920×1080) anime posters with synopsis, year, rating, "
        "genres, large quality buttons, a Telegram logo banner, channel logo "
        "and watermark.\n\n"
        "Commands:\n"
        "/poster <anime name>\n"
        "/search <anime name>\n"
        "/synopsis <anime name>\n"
        "/help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Commands:\n"
        "/poster <anime name> — create a 16:9 poster\n"
        "/search <anime name> — search titles\n"
        "/synopsis <anime name> — get synopsis and metadata\n"
        "/help — show commands\n\n"
        "Example: /poster Naruto Shippuden"
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text(
            "Usage: /poster <anime name>\nExample: /poster Solo Leveling"
        )
        return

    message = await update.effective_message.reply_text("🔎 Searching TMDB…")
    try:
        items = search(query)
        if not items:
            await message.edit_text(
                "No poster results found. Try another spelling."
            )
            return

        context.user_data["results"] = {
            str(index): item for index, item in enumerate(items)
        }
        buttons = []
        for index, item in enumerate(items):
            label = item["title"] + (
                f" ({item['date'][:4]})" if item["date"] else ""
            )
            buttons.append([
                InlineKeyboardButton(
                    label[:58], callback_data=f"poster:{index}"
                )
            ])
        await message.edit_text(
            "Choose the correct title:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as error:
        log.exception("Search failed")
        await message.edit_text(f"⚠️ Search failed: {error}")


async def synopsis_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text(
            "Usage: /synopsis <anime name>"
        )
        return

    try:
        items = search(query)
        if not items:
            await update.effective_message.reply_text(
                "No matching title found."
            )
            return

        data = details(items[0])
        message = f"📺 {data['title']}\n\n{data['overview'][:900]}"
        if data["year"]:
            message += f"\n\n📅 Year: {data['year']}"
        if data["rating"]:
            message += f"\n⭐ TMDB rating: {float(data['rating']):.1f}/10"
        if data["genres"]:
            message += "\n🎭 Genres: " + ", ".join(data["genres"])
        message += (
            f"\n\n🎞 {DEFAULT_QUALITY}\n"
            f"🔊 {DEFAULT_AUDIO}\n"
            f"✈️ {DEFAULT_JOIN}"
        )
        await update.effective_message.reply_text(message)
    except Exception as error:
        log.exception("Synopsis lookup failed")
        await update.effective_message.reply_text(
            f"⚠️ Could not retrieve synopsis: {error}"
        )


async def poster_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    index = query.data.split(":", 1)[1]
    item = context.user_data.get("results", {}).get(index)
    if not item:
        await query.edit_message_text(
            "Search expired. Please run /poster again."
        )
        return

    await query.edit_message_text(
        f"🎨 Creating 1920×1080 poster for {item['title']}…"
    )
    try:
        data = details(item)
        image = make_poster(data)

        # HTML is escaped so titles/synopsis cannot break the Telegram caption.
        title = html.escape(data["title"])
        caption = f"🎌 <b>{title}</b>"
        if data["year"]:
            caption += f"\n📅 {html.escape(data['year'])}"
        if data["rating"]:
            caption += f"  ⭐ {float(data['rating']):.1f}/10"

        quote = (
            f"🎞 {html.escape(DEFAULT_QUALITY)}\n"
            f"🔊 {html.escape(DEFAULT_AUDIO)}\n"
            f"✈️ {html.escape(DEFAULT_JOIN)}\n\n"
            "AnimeNetworkTamil"
        )
        caption += f"\n\n<blockquote expandable>{quote}</blockquote>"

        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=image,
            caption=caption[:1024],
            parse_mode=ParseMode.HTML,
            reply_markup=poster_keyboard(),
        )
        await query.message.reply_text(
            "✅ Poster ready! The image has large quality badges and a "
            "Telegram-style join banner. The Watch Now and Join Telegram "
            "buttons below the image are clickable."
        )
    except Exception as error:
        log.exception("Poster creation failed")
        await query.message.reply_text(f"⚠️ Couldn't create poster: {error}")


async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Use /poster followed by an anime name.\n"
        "Example: /poster Naruto Shippuden"
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN in Render Environment.")
    if not TMDB_TOKEN and not TMDB_KEY:
        raise SystemExit(
            "Missing TMDB_READ_ACCESS_TOKEN or TMDB_API_KEY in Render Environment."
        )

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("poster", search_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("synopsis", synopsis_cmd))
    app.add_handler(
        CallbackQueryHandler(poster_callback, pattern=r"^poster:\d+$")
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback)
    )

    log.info("AnimeNetworkTamil Poster Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    # Run the HTTP health server for Render Web Service deployments.
    threading.Thread(target=run_web_server, daemon=True).start()
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    main()
