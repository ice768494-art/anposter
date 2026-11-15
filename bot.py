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

    season_list = [
        s for s in data.get("seasons", [])
        if s.get("season_number", 0) > 0
    ]

    first_season = min(
        season_list,
        key=lambda s: s["season_number"],
        default=None
    )

    season_label = (
        f"Season {first_season['season_number']:02d}"
        if first_season else "Unknown"
    )

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
        "media_type": "TV SERIES" if item.get("kind") == "tv" else "MOVIE",
        "status": data.get("status") or "",
        "season": season_label,
        "episodes": data.get("number_of_episodes"),
        "runtime": (data.get("episode_run_time") or [None])[0]
            if item.get("kind") == "tv" else data.get("runtime"),
        "studio": next((company.get("name", "") for company in data.get("production_companies", []) if company.get("name")), ""),
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


def draw_quality_buttons(draw, x, y, max_width, button_color="#FFC857"):
    """Three bold quality buttons spanning the entire available row."""
    labels = DEFAULT_QUALITIES
    gap = 16
    button_h = 82
    button_w = (max_width - gap * (len(labels) - 1)) // len(labels)
    label_font = font(36, True)

    for index, label in enumerate(labels):
        x1 = x + index * (button_w + gap)
        x2 = x1 + button_w
        if index == len(labels) - 1:
            x2 = x + max_width
        draw.rounded_rectangle(
            (x1, y, x2, y + button_h),
            radius=18,
            fill=button_color,
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


TEMPLATES = {
    "cinematic": {"label": "🎬 Cinematic Dark", "accent": "#FF3158", "gold": "#FFB547", "soft": "#FFCF78", "button": "#FFC857", "overlay": (5, 7, 18, 125)},
    "neon": {"label": "⚡ Neon Blue", "accent": "#00D9FF", "gold": "#7DEBFF", "soft": "#BDF7FF", "button": "#00CFE8", "overlay": (0, 15, 30, 155)},
    "purple": {"label": "💜 Purple Glow", "accent": "#A970FF", "gold": "#D2AEFF", "soft": "#E8D7FF", "button": "#A970FF", "overlay": (20, 5, 35, 155)},
    "minimal": {"label": "✨ Minimal Clean", "accent": "#E6E8EF", "gold": "#D4D8E3", "soft": "#FFFFFF", "button": "#E6E8EF", "overlay": (12, 14, 22, 165)},
    "editorial": {"label": "📰 Manga Editorial", "accent": "#D92727", "gold": "#F0DDB7", "soft": "#FFF4DC", "button": "#F0DDB7", "overlay": (24, 18, 14, 178)},
    "retro": {"label": "📼 Retro VHS", "accent": "#FF7043", "gold": "#FFD166", "soft": "#B9F2E6", "button": "#FFD166", "overlay": (28, 15, 9, 160)},
    "broadcast": {"label": "📺 Broadcast TV", "accent": "#E63946", "gold": "#F1FAEE", "soft": "#DCEBFF", "button": "#A8DADC", "overlay": (7, 20, 38, 170)},
    "mono": {"label": "◼ Monochrome", "accent": "#FFFFFF", "gold": "#D6D6D6", "soft": "#F2F2F2", "button": "#E8E8E8", "overlay": (0, 0, 0, 185)},
    "magazine": {"label": "📚 Anime Magazine", "accent": "#F4C430", "gold": "#FFFFFF", "soft": "#F4F1E8", "button": "#F4C430", "overlay": (17, 25, 34, 165)},
}


def make_poster(info, template="cinematic"):
    style = TEMPLATES.get(template, TEMPLATES["cinematic"])
    accent = style["accent"]
    gold = style["gold"]
    soft = style["soft"]
    button_color = style["button"]
    # Use a user-uploaded image when supplied; otherwise use the selected TMDB poster.
    if info.get("poster_image"):
        poster = Image.open(io.BytesIO(info["poster_image"])).convert("RGB")
    else:
        if not info.get("poster_path"):
            raise RuntimeError("No poster artwork found. Choose or upload an image.")
        response = http.get(IMG + "w780" + info["poster_path"], timeout=30)
        response.raise_for_status()
        poster = Image.open(io.BytesIO(response.content)).convert("RGB")

    bg = ImageOps.fit(
        poster, (W, H), method=Image.Resampling.LANCZOS
    ).filter(ImageFilter.GaussianBlur(28))
    bg = Image.eval(bg, lambda pixel: int(pixel * 0.36)).convert("RGBA")
    bg.alpha_composite(Image.new("RGBA", (W, H), style["overlay"]))
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

    # Distinct, hand-coded graphic-design details; no generated artwork.
    if template == "editorial":
        draw.rectangle((690, 0, 704, H), fill=accent)
        draw.rectangle((735, 25, 1125, 34), fill=gold)
        draw.text((1145, 18), "ANIME / FEATURE", font=font(17, True), fill=soft)
    elif template == "retro":
        for offset in range(0, 7):
            yy = 18 + offset * 13
            draw.line((735, yy, 1120, yy), fill=accent if offset % 2 == 0 else gold, width=3)
        draw.rectangle((1790, 0, W, H), fill=(255, 112, 67, 22))
        draw.text((1580, 24), "VHS • 01", font=font(19, True), fill=gold)
    elif template == "broadcast":
        draw.rectangle((710, 0, W, 18), fill=accent)
        draw.rectangle((735, 20, 965, 56), fill=accent)
        draw.text((750, 26), "NOW SHOWING", font=font(19, True), fill="#FFFFFF")
        draw.text((1660, 25), "ANIME NETWORK / TV", font=font(17, True), fill=soft)
    elif template == "mono":
        draw.rectangle((700, 0, 707, H), fill="#FFFFFF")
        draw.rectangle((735, 25, 920, 31), fill="#FFFFFF")
        draw.text((1660, 24), "A N T  /  COLLECTION", font=font(16, True), fill=soft)
    elif template == "magazine":
        draw.rectangle((700, 0, 710, H), fill=accent)
        draw.text((735, 22), "THE ANIME EDITION", font=font(18, True), fill=gold)
        draw.line((735, 51, 1825, 51), fill=soft, width=2)
        draw.text((1630, 62), "FEATURE STORY", font=font(15, True), fill=gold)

    tx, maxw = 735, 1090
    title = info["title"].upper()
    # Fit the complete title: allow extra lines and reduce font size as needed.
    title_size = 94 if len(title) <= 15 else 82 if len(title) <= 22 else 68
    title_font = font(title_size, True)
    title_lines = wrap(draw, title, title_font, maxw)
    while title_size > 30:
        line_height = max(1, draw.textbbox((0, 0), "Ag", font=title_font)[3])
        if len(title_lines) <= 4 and line_height * len(title_lines) + 10 * (len(title_lines) - 1) <= 285:
            break
        title_size -= 2
        title_font = font(title_size, True)
        title_lines = wrap(draw, title, title_font, maxw)
    y = put_lines(
        draw, title_lines,
        (tx, 54), title_font, "#FFFFFF", 7, 3, "#101322"
    )

    # A small label identifies the selected design.
    template_label = style["label"].upper()
    label_font = font(17, True)
    draw.text((tx, y + 2), template_label, font=label_font, fill=gold)
    y += 28

    # Genres now appear directly below the anime title.
    genres = info.get("genres", [])
    genre_text = "  •  ".join(genres)
    y += 12

    if genre_text:
        genre_font = font(27, True)
        while (
            draw.textbbox((0, 0), genre_text, font=genre_font)[2] > maxw
            and genre_font.size > 18
        ):
            genre_font = font(genre_font.size - 1, True)

        draw.text(
            (tx, y),
            genre_text,
            font=genre_font,
            fill=soft,
        )
        y += 44

    draw.rounded_rectangle(
        (tx, y, tx + 175, y + 9), radius=4, fill=accent
    )
    y += 35
    draw.text((tx, y), "SYNOPSIS", font=font(29, True), fill=gold)
    y += 48

    synopsis = re.sub(
        r"\s+", " ", info.get("overview", "")
    ).strip() or "Synopsis unavailable."
    if len(synopsis) > 620:
        synopsis = synopsis[:620].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    # Keep the synopsis readable but reserve a predictable area for details.
    synopsis_lines = wrap(draw, synopsis, font(25), maxw)[:5]
    y = put_lines(
        draw, synopsis_lines, (tx, y), font(25), "#F0F1FA", 9
    )

    # Build useful metadata instead of leaving a large blank block.
    metadata = []
    if info.get("year"):
        metadata.append(str(info["year"]))
    if info.get("rating"):
        try:
            metadata.append(f"TMDB ★ {float(info['rating']):.1f}/10")
        except (ValueError, TypeError):
            pass
    if info.get("media_type"):
        metadata.append(info["media_type"])
    if info.get("season") and info["season"] not in ("Unknown", "Not specified"):
        metadata.append(str(info["season"]).upper())
    if info.get("seasons"):
        metadata.append(f"{info['seasons']} SEASON" + ("S" if info["seasons"] != 1 else ""))
    if info.get("episodes"):
        metadata.append(f"{info['episodes']} EPISODES")
    if info.get("runtime"):
        metadata.append(f"{info['runtime']} MIN")
    if info.get("status"):
        metadata.append(str(info["status"]).upper())
    if info.get("studio"):
        metadata.append("STUDIO: " + str(info["studio"]))

    meta_y = max(y + 18, 565)
    meta_y = min(meta_y, 580)
    panel_h = 94
    draw.rounded_rectangle(
        (tx, meta_y - 12, tx + maxw, meta_y + panel_h),
        radius=18,
        fill=(18, 22, 36, 205),
        outline=gold,
        width=2,
    )
    draw.text(
        (tx + 22, meta_y), "ANIME DETAILS",
        font=font(19, True), fill=gold
    )
    meta_font = font(20, True)
    meta_text = "  •  ".join(metadata) if metadata else "Details unavailable"
    meta_lines = wrap(draw, meta_text, meta_font, maxw - 44)[:2]
    put_lines(
        draw, meta_lines, (tx + 22, meta_y + 34), meta_font,
        soft, 5
    )

    # Clear section label makes the quality choices feel intentional.
    draw.text(
        (tx, 690), "AVAILABLE QUALITIES",
        font=font(18, True), fill="#D9DCE8"
    )
    draw_quality_buttons(draw, tx, 720, maxw, button_color)
    # Draw the speaker symbol with vector shapes so it renders consistently
    # even on servers whose installed fonts do not contain emoji glyphs.
    speaker_y = 826
    draw.polygon(
        [
            (tx + 4, speaker_y + 17),
            (tx + 22, speaker_y + 17),
            (tx + 43, speaker_y + 2),
            (tx + 43, speaker_y + 54),
            (tx + 22, speaker_y + 39),
            (tx + 4, speaker_y + 39),
        ],
        fill="#35A9FF",
    )
    draw.arc(
        (tx + 35, speaker_y + 5, tx + 70, speaker_y + 51),
        start=-65, end=65, fill="#35A9FF", width=6,
    )
    draw.arc(
        (tx + 40, speaker_y - 3, tx + 84, speaker_y + 59),
        start=-65, end=65, fill="#35A9FF", width=5,
    )
    draw.text(
        (tx + 100, 818),
        DEFAULT_AUDIO,
        font=font(40, True),
        fill="#FFFFFF",
        stroke_width=2,
        stroke_fill="#111222",
    )
    draw_telegram_join(draw, tx, 900, maxw)

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
        "Create 16:9 (1920×1080) anime posters with 9 hand-designed templates, synopsis, year, rating, "
        "genres below the title, large quality buttons, a Telegram logo banner, "
        "channel logo and watermark.\n\n"
        "Commands:\n"
        "/p <anime name>\n"
        "/search <anime name>\n"
        "/synopsis <anime name>\n"
        "/help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Commands:\n"
        "/p <anime name> — create a 16:9 poster\n"
        "/search <anime name> — search titles\n"
        "/synopsis <anime name> — get synopsis and metadata\n"
        "/help — show commands\n\n"
        "Example: /poster Naruto Shippuden"
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text(
            "Usage: /p <anime name>\nExample: /poster Solo Leveling"
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
    """After choosing an anime, ask which poster design to use."""
    query = update.callback_query
    await query.answer()
    index = query.data.split(":", 1)[1]
    item = context.user_data.get("results", {}).get(index)
    if not item:
        await query.edit_message_text("Search expired. Please run /p again.")
        return

    buttons = [
        [InlineKeyboardButton(TEMPLATES[key]["label"], callback_data=f"template:{index}:{key}")]
        for key in TEMPLATES
    ]
    await query.edit_message_text(
        f"🎨 Selected: {item['title']}\n\nChoose a poster template:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def template_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Choose a poster design, then let the user select the artwork."""
    query = update.callback_query
    await query.answer()
    _, index, template = query.data.split(":", 2)
    item = context.user_data.get("results", {}).get(index)
    if not item:
        await query.edit_message_text("Search expired. Please run /p again.")
        return

    context.user_data["poster_pending"] = {
        "item": item,
        "template": template,
        "chat_id": query.message.chat_id,
    }
    try:
        image_data = api(
            f"/{item['kind']}/{item['id']}/images",
            {"include_image_language": "en,null"},
        )
        posters = image_data.get("posters", [])[:6]
    except Exception:
        log.exception("Could not load alternate poster images")
        posters = []

    buttons = []
    for i, poster in enumerate(posters):
        buttons.append([InlineKeyboardButton(
            f"🖼 TMDB Poster {i + 1}",
            callback_data=f"imagepick:{index}:{template}:{i}"
        )])
    buttons.append([InlineKeyboardButton(
        "📤 Upload my own poster image",
        callback_data=f"imageupload:{index}:{template}"
    )])
    await query.edit_message_text(
        f"🎨 Template: {TEMPLATES.get(template, TEMPLATES['cinematic'])['label']}\n"
        f"Anime: {item['title']}\n\nChoose a poster image:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    context.user_data["poster_options"] = posters


async def imagepick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, index, template, poster_index = query.data.split(":", 3)
    item = context.user_data.get("results", {}).get(index)
    posters = context.user_data.get("poster_options", [])
    if not item or int(poster_index) >= len(posters):
        await query.edit_message_text("Image selection expired. Please run /p again.")
        return

    try:
        data = details(item)
        data["poster_path"] = posters[int(poster_index)].get("file_path")
        if not data["poster_path"]:
            raise RuntimeError("That TMDB image is unavailable.")
        context.user_data["poster_pending"] = {
            "item": item, "template": template, "data": data,
            "chat_id": query.message.chat_id,
        }
        context.user_data["waiting_season"] = True
        await query.edit_message_text(
            f"✅ Poster image selected for {data['title']}.\n\n"
            "Send the season number (for example: 2).\n"
            "Send 0 for no season label, or `skip` to keep TMDB's suggestion."
        )
    except Exception as error:
        log.exception("Image selection failed")
        await query.edit_message_text(f"⚠️ Could not select image: {error}")


async def imageupload_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, index, template = query.data.split(":", 2)
    item = context.user_data.get("results", {}).get(index)
    if not item:
        await query.edit_message_text("Search expired. Please run /p again.")
        return
    try:
        data = details(item)
        context.user_data["poster_pending"] = {
            "item": item, "template": template, "data": data,
            "chat_id": query.message.chat_id,
        }
        context.user_data["waiting_poster_photo"] = True
        await query.edit_message_text(
            "📤 Please send the poster image as a photo (not as a document)."
        )
    except Exception as error:
        log.exception("Could not prepare custom image upload")
        await query.edit_message_text(f"⚠️ Could not prepare poster: {error}")


async def poster_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_poster_photo"):
        return
    pending = context.user_data.get("poster_pending")
    if not pending:
        context.user_data.pop("waiting_poster_photo", None)
        await update.effective_message.reply_text("Please start again with /p <anime name>.")
        return
    try:
        photo = update.effective_message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        buffer = io.BytesIO()
        await file.download_to_memory(out=buffer)
        pending["data"]["poster_image"] = buffer.getvalue()
        context.user_data["waiting_poster_photo"] = False
        context.user_data["waiting_season"] = True
        await update.effective_message.reply_text(
            "✅ Your poster image is ready.\n\n"
            "Now send the season number (for example: 2).\n"
            "Send 0 for no season label, or `skip` to keep TMDB's suggestion."
        )
    except Exception as error:
        log.exception("Poster image upload failed")
        await update.effective_message.reply_text(f"⚠️ Could not read that image: {error}")


async def finish_pending_poster(update: Update, context: ContextTypes.DEFAULT_TYPE, season_text: str):
    pending = context.user_data.get("poster_pending")
    if not pending:
        await update.effective_message.reply_text("Your poster session expired. Please run /p again.")
        return

    data = pending["data"]
    value = season_text.strip().lower()
    if value == "skip":
        pass  # Keep the TMDB season suggestion.
    elif value == "0":
        data["season"] = "Not specified"
    elif value.isdigit() and 1 <= int(value) <= 99:
        data["season"] = f"Season {int(value):02d}"
    else:
        await update.effective_message.reply_text(
            "Please send a season number from 0 to 99, or send `skip`."
        )
        return

    context.user_data.pop("waiting_season", None)
    context.user_data.pop("waiting_poster_photo", None)
    context.user_data.pop("poster_pending", None)
    await update.effective_message.reply_text(
        f"🎨 Creating {TEMPLATES.get(pending['template'], TEMPLATES['cinematic'])['label']} poster…"
    )

    try:
        image = make_poster(data, template=pending["template"])

        def get_value(key, default):
            value = data.get(key)
            if isinstance(value, list):
                value = ", ".join(str(x) for x in value)
            return html.escape(str(value).strip()) if value else default

        title = get_value("title", "Unknown Anime")
        season = get_value("season", "Unknown")
        episode = get_value("episode", "TBA")
        audio = get_value("audio", DEFAULT_AUDIO)
        quality = get_value("quality", DEFAULT_QUALITY)
        status = get_value("status", "Unknown")
        year = get_value("year", "TBA")
        genres = get_value("genres", "Not Available")
        source = get_value("source", "Unknown")
        synopsis = re.sub(r"\s+", " ", data.get("overview", "")).strip()
        if not synopsis:
            synopsis = "Synopsis Not Available."

        metadata = (
            f"🎌 <b>Title :</b> {title}\n"
            f"〄 <b>Season :</b> {season}\n"
            f"〄 <b>Episode :</b> {episode}\n"
            f"〄 <b>Audio :</b> {audio}\n"
            f"〄 <b>Quality :</b> {quality}\n"
            f"〄 <b>Status :</b> {status}\n"
            f"〄 <b>Release Year :</b> {year}\n"
            f"〄 <b>Genres :</b> {genres}\n"
            f"〄 <b>Source :</b> {source}\n\n"
            f"➽ <b>Nᴇᴛᴡᴏʀᴋ :-</b> @AnimeNetworkTamil ✓\n\n"
        )
        opening = f"<blockquote expandable>📖 <b>Synopsis of {title}</b>\n"
        closing = "</blockquote>"
        available = max(0, 1024 - len(metadata) - len(opening) - len(closing))
        escaped_synopsis = html.escape(synopsis)
        if len(escaped_synopsis) > available:
            synopsis = synopsis[:max(0, available - 10)].rsplit(" ", 1)[0] + "…"
        caption = metadata + opening + html.escape(synopsis) + closing

        await context.bot.send_photo(
            chat_id=pending["chat_id"], photo=image, caption=caption,
            parse_mode=ParseMode.HTML, reply_markup=poster_keyboard(),
        )
        await update.effective_message.reply_text("✅ Poster Ready Aiduchu")
    except Exception as error:
        log.exception("Poster creation failed")
        await update.effective_message.reply_text(f"⚠️ Couldn't create poster: {error}")


async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("waiting_season"):
        await finish_pending_poster(update, context, update.effective_message.text or "")
        return
    await update.effective_message.reply_text(
        "Use /p followed by an anime name.\n"
        "Example: /p Naruto Shippuden"
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
    app.add_handler(CommandHandler("p", search_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("synopsis", synopsis_cmd))
    app.add_handler(
        CallbackQueryHandler(poster_callback, pattern=r"^poster:\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(template_callback, pattern=r"^template:\d+:[a-z]+$")
    )
    app.add_handler(
        CallbackQueryHandler(imagepick_callback, pattern=r"^imagepick:\d+:[a-z]+:\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(imageupload_callback, pattern=r"^imageupload:\d+:[a-z]+$")
    )
    app.add_handler(MessageHandler(filters.PHOTO, poster_photo_handler))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback)
    )

    log.info("AnimeNetworkTamil Poster Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    main()
