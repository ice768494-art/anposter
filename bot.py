"""Anime Poster Bot — single fixed "Clean Cinematic" landscape poster (2560x1440).

Environment variables:
  TELEGRAM_BOT_TOKEN (required)
  TMDB_READ_ACCESS_TOKEN (recommended) or TMDB_API_KEY
  CHANNEL_USERNAME (optional, e.g. @AnimeNetworkTamil)

Optional files next to bot.py:
  fonts/Poppins-Bold.ttf        (title + pills, matches the template)
  fonts/Poppins-SemiBold.ttf    (genre text)
  logo.png                      (square channel logo, shown bottom-right)

Install: pip install -r requirements.txt
Run: python bot.py
"""
import os, io, math, html, logging, asyncio, threading
from typing import Optional
import requests
from flask import Flask, jsonify
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler,
                          ContextTypes, ConversationHandler, filters)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("anime-poster-bot")
web = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@web.get("/")
def health_check():
    return jsonify({"status": "ok", "service": "anime-poster-bot"}), 200


@web.get("/health")
def health():
    return jsonify({"status": "healthy"}), 200


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    threading.Thread(
        target=lambda: web.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False),
        name="render-health-server",
        daemon=True,
    ).start()
    log.info("Health server listening on 0.0.0.0:%s", port)


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TMDB_TOKEN = os.getenv("TMDB_READ_ACCESS_TOKEN", "").strip()
TMDB_KEY = os.getenv("TMDB_API_KEY", "").strip()
CHANNEL = os.getenv("CHANNEL_USERNAME", "@AnimeNetworkTamil").strip()
TMDB_API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
LOGO_PATH = os.path.join(BASE_DIR, "logo.png")

SEARCH, IMAGE, SEASON, EPISODES = range(4)

LANG_NAMES = {
    "ja": "JAPANESE", "ko": "KOREAN", "zh": "CHINESE", "en": "ENGLISH",
    "ta": "TAMIL", "hi": "HINDI", "te": "TELUGU", "ml": "MALAYALAM",
    "fr": "FRENCH", "es": "SPANISH", "de": "GERMAN",
}


def tmdb(path: str, params=None):
    params = dict(params or {})
    headers = {}
    if TMDB_TOKEN:
        headers["Authorization"] = f"Bearer {TMDB_TOKEN}"
    elif TMDB_KEY:
        params["api_key"] = TMDB_KEY
    else:
        raise RuntimeError("Set TMDB_READ_ACCESS_TOKEN or TMDB_API_KEY in environment variables.")
    r = requests.get(TMDB_API + path, params=params, headers=headers, timeout=25)
    r.raise_for_status()
    return r.json()


def fetch_image(path: Optional[str], size="w1280") -> Optional[Image.Image]:
    if not path:
        return None
    try:
        r = requests.get(IMG + size + path, timeout=25)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        log.warning("Image download failed: %s", e)
        return None


def font(size: int, bold=False):
    candidates = [
        os.path.join(BASE_DIR, "fonts", "Poppins-Bold.ttf" if bold else "Poppins-SemiBold.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def cover(im: Optional[Image.Image], size, fallback=(10, 22, 20)):
    w, h = size
    if im is None:
        out = Image.new("RGB", size, fallback)
        d = ImageDraw.Draw(out)
        for x in range(0, w, 90):
            d.line((x, 0, x - 180, h), fill=(15, 65, 48), width=3)
        return out
    ratio = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * ratio), int(im.height * ratio)), Image.Resampling.LANCZOS)
    x = (im.width - w) // 2
    y = (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def fit_text(draw, text, max_width, start=70, minimum=24, bold=True):
    for size in range(start, minimum - 1, -2):
        f = font(size, bold)
        if draw.textbbox((0, 0), text, font=f)[2] <= max_width:
            return f
    return font(minimum, bold)


def wrap_lines(draw, text, fnt, max_width, max_lines=8):
    words = str(text or "").replace("\n", " ").split()
    lines, line = [], ""
    for word in words:
        test = (line + " " + word).strip()
        if draw.textbbox((0, 0), test, font=fnt)[2] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textbbox((0, 0), lines[-1] + "…", font=fnt)[2] > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


# ───────────────────────── Poster helpers ─────────────────────────

def draw_dots(d, x, y, cols, rows, bright_right=True, gap=18, r=5):
    """Fading dot-grid decoration."""
    for row in range(rows):
        for col in range(cols):
            t = col / max(cols - 1, 1)
            if not bright_right:
                t = 1 - t
            a = int(60 + 170 * t)
            cx, cy = x + col * gap, y + row * gap
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, a))


def star_points(cx, cy, r_out, r_in=None):
    r_in = r_in or r_out * 0.45
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def make_poster(info: dict, hero: Image.Image, thumbs: list,
                season="1", episodes="1 - 12") -> io.BytesIO:
    """Clean artwork-first poster: big title bottom-left, info pills, genre bar,
    channel pill top-right, 'Download Now' + logo bottom-right."""
    W, H = 2560, 1440
    M = 64  # outer margin

    ROSE = (146, 82, 82, 255)
    ROSE_TEXT = (255, 232, 232, 255)
    DARK = (18, 18, 22, 235)
    WHITE = (255, 255, 255, 255)
    GOLD = (255, 196, 40, 255)

    # ── Background artwork ──
    base = cover(hero, (W, H)).convert("RGB")
    base = ImageEnhance.Color(base).enhance(1.2)
    base = ImageEnhance.Contrast(base).enhance(1.08)
    base = base.convert("RGBA")

    # bottom gradient (text legibility)
    mh = int(H * 0.62)
    m = Image.linear_gradient("L").resize((W, mh))
    m = m.point(lambda v: int(238 * (v / 255) ** 1.6))
    layer = Image.new("RGBA", (W, mh), (6, 6, 10, 0))
    layer.putalpha(m)
    base.alpha_composite(layer, (0, H - mh))

    # left gradient
    mw = int(W * 0.55)
    g = Image.linear_gradient("L").rotate(90).transpose(Image.Transpose.FLIP_LEFT_RIGHT).resize((mw, H))
    g = g.point(lambda v: int(130 * (v / 255) ** 1.8))
    layer = Image.new("RGBA", (mw, H), (6, 6, 10, 0))
    layer.putalpha(g)
    base.alpha_composite(layer, (0, 0))

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    # ── Layout anchors (built bottom-up so 1 or 2 title lines both work) ──
    genre_h = 66
    genre_y = H - 117 - genre_h          # genre bar top
    pills_h = 60
    pills_y = genre_y - 47 - pills_h     # pills top
    accent_y = pills_y - 32              # short accent line

    # ── Title (max 2 lines, auto-fit) ──
    title = (info.get("name") or "ANIME").upper()
    max_title_w = 1400
    title_font, lines = font(60, True), [title]
    for size in range(150, 56, -4):
        f = font(size, True)
        ls = wrap_lines(d, title, f, max_title_w, 10)
        if len(ls) <= 2 and max(d.textlength(x, font=f) for x in ls) <= max_title_w:
            title_font, lines = f, ls
            break
    else:
        lines = wrap_lines(d, title, title_font, max_title_w, 2)
    line_h = int(title_font.size * 1.42)
    block_h = line_h * len(lines)
    title_top = accent_y - 34 - block_h

    # soft drop shadow for the title
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for i, line in enumerate(lines):
        sd.text((M + 4, title_top + i * line_h + 6), line, font=title_font, fill=(0, 0, 0, 215))
    shadow = shadow.filter(ImageFilter.GaussianBlur(9))
    base.alpha_composite(shadow)

    for i, line in enumerate(lines):
        d.text((M, title_top + i * line_h), line, font=title_font, fill=WHITE)

    # accent line
    d.rounded_rectangle((M, accent_y, M + 128, accent_y + 6), radius=3, fill=ROSE)

    # ── Info pills ──
    pill_font = font(30, True)

    def pill(x, text, fill, text_fill, star=False):
        tw = d.textlength(text, font=pill_font)
        extra = 46 if star else 0
        w = int(tw + 64 + extra)
        d.rounded_rectangle((x, pills_y, x + w, pills_y + pills_h), radius=14, fill=fill)
        tx = x + 32
        if star:
            d.polygon(star_points(x + 32 + 17, pills_y + pills_h / 2 + 1, 19), fill=GOLD)
            tx += extra
        d.text((tx, pills_y + pills_h / 2 + 1), text, font=pill_font, fill=text_fill, anchor="lm")
        return x + w + 28

    lang = LANG_NAMES.get((info.get("original_language") or "ja").lower(),
                          (info.get("original_language") or "JA").upper())
    rating = info.get("vote_average") or 0
    rating_txt = f"{rating:.1f}" if rating else "N/A"
    try:
        n_seasons = int(info.get("number_of_seasons") or int(season) or 1)
    except Exception:
        n_seasons = 1
    if info.get("media_type") == "movie":
        season_txt = "MOVIE"
    else:
        season_txt = f"{n_seasons} SEASON" + ("S" if n_seasons > 1 else "")

    x = M
    x = pill(x, "OFFICIAL", ROSE, ROSE_TEXT)
    x = pill(x, lang, ROSE, ROSE_TEXT)
    x = pill(x, rating_txt, DARK, WHITE, star=True)
    x = pill(x, season_txt, DARK, WHITE)

    # ── Genre bar ──
    label_font = font(28, True)
    genre_font = font(28, True)
    genres = [g.strip().upper() for g in str(info.get("genres") or "ANIME").split(",") if g.strip()][:4]
    label_w = int(d.textlength("GENRE", font=label_font))
    inner_x = M + 32 + label_w + 32
    gx = inner_x + 32
    positions = []
    for gname in genres:
        positions.append((gx, gname))
        gx += int(d.textlength(gname, font=genre_font)) + 24
    bar_end = gx - 24 + 32
    d.rounded_rectangle((M, genre_y, bar_end, genre_y + genre_h), radius=12, fill=(20, 20, 24, 200))
    d.rounded_rectangle((inner_x, genre_y, bar_end, genre_y + genre_h), radius=12, fill=(255, 255, 255, 24))
    cy_g = genre_y + genre_h / 2 + 1
    d.text((M + 32, cy_g), "GENRE", font=label_font, fill=(176, 104, 104, 255), anchor="lm")
    for gx0, gname in positions:
        d.text((gx0, cy_g), gname, font=genre_font, fill=WHITE, anchor="lm")

    # ── Top-right: dots + channel pill ──
    draw_dots(d, W - M - 162, 34, 10, 2, bright_right=True)
    handle = CHANNEL if CHANNEL.startswith("@") else "@" + CHANNEL
    hf = font(34, True)
    hw = int(d.textlength(handle, font=hf)) + 76
    d.rounded_rectangle((W - M - hw, 125, W - M, 197), radius=36, fill=(255, 255, 255, 255))
    d.text((W - M - hw / 2, 162), handle, font=hf, fill=(20, 20, 26, 255), anchor="mm")

    # ── Bottom-right: Download Now + logo ──
    dia = 200
    lcx, lcy = W - M - dia // 2, H - 60 - dia // 2
    draw_dots(d, W - M - 144, lcy - dia // 2 - 36, 9, 2, bright_right=False)
    d.text((lcx - dia // 2 - 36, lcy), "Download Now", font=font(62, True),
           fill=WHITE, anchor="rm")

    # square channel logo (rounded corners) or fallback arrow icon
    # ring sits behind the logo, offset to the top-left like the template
    rcx, rcy, rr = lcx - 62, lcy - 62, 86
    d.ellipse((rcx - rr, rcy - rr, rcx + rr, rcy + rr), outline=(255, 255, 255, 215), width=4)
    base.alpha_composite(ov)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    if os.path.exists(LOGO_PATH):
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA").resize((dia, dia), Image.Resampling.LANCZOS)
            mask = Image.new("L", (dia, dia), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, dia - 1, dia - 1), radius=18, fill=255)
            bg = Image.new("RGBA", (dia, dia), (255, 255, 255, 255))
            bg.alpha_composite(logo)
            base.paste(bg, (lcx - dia // 2, lcy - dia // 2), mask)
        except Exception as e:
            log.warning("Logo load failed: %s", e)
    else:
        d.ellipse((lcx - dia // 2, lcy - dia // 2, lcx + dia // 2, lcy + dia // 2), fill=ROSE)
        d.polygon([(lcx - 42, lcy - 8), (lcx + 42, lcy - 8), (lcx, lcy + 40)], fill=WHITE)
        d.rectangle((lcx - 12, lcy - 52, lcx + 12, lcy - 8), fill=WHITE)
        base.alpha_composite(ov)

    out = io.BytesIO()
    base.convert("RGB").save(out, "JPEG", quality=93, optimize=True)
    out.seek(0)
    return out


def get_season_overview(info: dict, season_num: int) -> Optional[str]:
    """Synopsis of one specific season from TMDB (None if unavailable)."""
    if info.get("media_type") != "tv" or season_num <= 0:
        return None
    try:
        data = tmdb(f"/tv/{info['id']}/season/{season_num}")
        text = (data.get("overview") or "").strip()
        return text or None
    except Exception as e:
        log.warning("Season %s overview unavailable: %s", season_num, e)
        return None


# ───────────────────────── Bot handlers ─────────────────────────

def get_images(info):
    kind, mid = info["media_type"], info["id"]
    try:
        data = tmdb(f"/{kind}/{mid}/images", {"include_image_language": "en,null"})
        paths = [x["file_path"] for x in data.get("backdrops", [])[:6]]
        if not paths:
            paths = [x["file_path"] for x in data.get("posters", [])[:6]]
        return paths
    except Exception as e:
        log.warning("Could not get image list: %s", e)
        return [info.get("backdrop_path") or info.get("poster_path")]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 *Anime Poster Bot*\n\nUse `/p Naruto Shippuden` to create a poster.\nChoose a TMDB image or upload your own, then enter the season and episode range.",
        parse_mode=ParseMode.MARKDOWN)


async def poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Send an anime name: `/p Naruto Shippuden`", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END
    try:
        data = tmdb("/search/multi", {"query": query, "include_adult": "false"})
        results = [x for x in data.get("results", []) if x.get("media_type") in ("tv", "movie")][:8]
        if not results:
            await update.message.reply_text("No matching title found. Try another spelling.")
            return ConversationHandler.END
        context.user_data["results"] = results
        rows = []
        for i, x in enumerate(results):
            name = x.get("name") or x.get("title") or "Untitled"
            year = (x.get("first_air_date") or x.get("release_date") or "")[:4]
            rows.append([InlineKeyboardButton(f"{name} {('(' + year + ')') if year else ''} · {x['media_type'].upper()}", callback_data=f"pick:{i}")])
        await update.message.reply_text("Choose the correct anime:", reply_markup=InlineKeyboardMarkup(rows))
        return SEARCH
    except Exception as e:
        log.exception("Search failed")
        await update.message.reply_text(f"TMDB search failed: {e}")
        return ConversationHandler.END


async def picked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        idx = int(q.data.split(":")[1])
        result = context.user_data["results"][idx]
        kind = result["media_type"]
        info = tmdb(f"/{kind}/{result['id']}", {"append_to_response": "credits"})
        info["media_type"] = kind
        info["name"] = info.get("name") or info.get("title")
        info["genres"] = ", ".join(g["name"] for g in info.get("genres", [])[:4]) or "ANIME"
        info["status_label"] = "ONGOING" if info.get("status") == "Returning Series" else (info.get("status") or "AVAILABLE").upper()
        context.user_data["info"] = info
        paths = get_images(info)
        context.user_data["image_paths"] = [p for p in paths if p]
        rows = []
        for i, p in enumerate(context.user_data["image_paths"]):
            rows.append([InlineKeyboardButton(f"Use TMDB image {i+1}", callback_data=f"img:{i}")])
        rows.append([InlineKeyboardButton("📤 Upload my own image", callback_data="img:upload")])
        await q.edit_message_text("Choose the main poster/background image, or upload your own photo:", reply_markup=InlineKeyboardMarkup(rows))
        return IMAGE
    except Exception as e:
        log.exception("Title selection failed")
        await q.edit_message_text(f"Could not load title details: {e}")
        return ConversationHandler.END


async def image_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    choice = q.data.split(":", 1)[1]
    if choice == "upload":
        context.user_data["awaiting_upload"] = True
        await q.edit_message_text("Now send the image as a photo or image document. I’ll use it as the main background.")
        return IMAGE
    try:
        path = context.user_data["image_paths"][int(choice)]
        hero = fetch_image(path, "original")  # full-res for the 2560x1440 poster
        if hero is None:
            raise RuntimeError("That TMDB image could not be downloaded.")
        context.user_data["hero"] = hero
        context.user_data["thumb_paths"] = [p for p in context.user_data["image_paths"] if p != path][:4]
        await q.edit_message_text("Poster style selected automatically.\n\nEnter the season number (example: 18). Send 0 for a movie or no season.")
        return SEASON
    except Exception as e:
        await q.edit_message_text(str(e))
        return ConversationHandler.END


async def image_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_upload"):
        await update.message.reply_text("Use the image-selection buttons first, or start again with /p.")
        return IMAGE
    try:
        obj = update.message.photo[-1] if update.message.photo else update.message.document
        if not obj or (update.message.document and not (update.message.document.mime_type or "").startswith("image/")):
            await update.message.reply_text("Please send a valid image.")
            return IMAGE
        f = await context.bot.get_file(obj.file_id)
        b = io.BytesIO()
        await f.download_to_memory(out=b)
        b.seek(0)
        context.user_data["hero"] = Image.open(b).convert("RGB")
        context.user_data["thumb_paths"] = context.user_data.get("image_paths", [])[:4]
        context.user_data["awaiting_upload"] = False
        await update.message.reply_text("Poster style selected automatically.\n\nEnter the season number (example: 18). Send 0 for a movie or no season.")
        return SEASON
    except Exception as e:
        log.exception("Upload failed")
        await update.message.reply_text(f"Could not read that image: {e}")
        return IMAGE


async def season_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    if not value.isdigit() or int(value) > 999:
        await update.message.reply_text("Please enter a number, for example 18. Use 0 for a movie.")
        return SEASON
    context.user_data["season"] = value
    await update.message.reply_text("Enter the episode range (example: 373 - 393). For a movie, send `Movie`.", parse_mode=ParseMode.MARKDOWN)
    return EPISODES


async def episodes_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    episodes = update.message.text.strip()[:40]
    if not episodes:
        await update.message.reply_text("Please enter an episode range or Movie.")
        return EPISODES
    ud = context.user_data
    info = ud["info"]
    await update.message.reply_text("Creating your poster…")
    try:
        thumbs = []  # not used by the new template, kept for compatibility
        poster_img = make_poster(info, ud["hero"], thumbs, ud.get("season", "1"), episodes)
        name = info.get("name", "Anime")
        season = int(ud.get("season", "1"))
        season_line = f"Season {season}" if season else "Movie"
        year = (info.get("first_air_date") or info.get("release_date") or "")[:4]
        genres = info.get("genres", "Anime")
        # Season-specific synopsis (e.g. season 02); falls back to the series synopsis
        overview = get_season_overview(info, season) or info.get("overview") or "Synopsis not available."
        name = html.escape(str(name))
        genres = html.escape(str(genres))
        overview = html.escape(overview)
        caption = (f"<b><b>🎌 {name}</b>\n"
                   f"<b>〄 Season :</b> {season_line}\n"
                   f"<b>〄 Episode :</b> {episodes}\n"
                   f"<b>〄 Audio :</b> Tamil\n"
                   f"<b>〄 Quality :</b> 480P | 720P | 1080P\n"
                   f"<b>〄 Status :</b> {info.get('status_label', 'AVAILABLE')}\n"
                   f"<b>〄 Year :</b> {year or 'N/A'}\n"
                   f"<b>〄 Genre :</b> {genres}</b>\n\n"
                   f"<blockquote expandable><b>Synopsis :</b><b> {overview}</b></blockquote>")
        await update.message.reply_photo(photo=poster_img, caption=caption, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("Poster generation failed")
        await update.message.reply_text(f"Poster generation failed: {e}")
    finally:
        for k in ("info", "hero", "thumb_paths", "image_paths", "season", "results", "awaiting_upload"):
            ud.pop(k, None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled. Start again with /p Anime Name.")
    return ConversationHandler.END


def main():
    if not TOKEN:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN environment variable.")
    app = Application.builder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("p", poster)],
        states={
            SEARCH: [CallbackQueryHandler(picked, pattern=r"^pick:\d+$")],
            IMAGE: [CallbackQueryHandler(image_chosen, pattern=r"^img:"),
                    MessageHandler(filters.PHOTO | filters.Document.IMAGE, image_upload)],
            SEASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, season_entered)],
            EPISODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, episodes_entered)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(conv)
    start_health_server()
    log.info("Anime Poster Bot started")
    # Python 3.14 no longer creates a default event loop automatically.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
