import os
import threading
from flask import Flask

web_app = Flask(__name__)

@web_app.route("/")
def home():
  return "Anime Poster Bot is running!", 200

@web_app.route("/health")
def health():
  return "OK", 200

def run_web_server():
  port = int(os.environ.get("PORT", 10000))
  web_app.run(host="0.0.0.0", port=port, use_reloader=False)

threading.Thread(target=run_web_server, daemon=True).start()


import io, logging, os, re, asyncio
from pathlib import Path
import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TMDB_TOKEN = os.getenv("TMDB_READ_ACCESS_TOKEN", "").strip()
TMDB_KEY = os.getenv("TMDB_API_KEY", "").strip()
API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
LOGO = Path(__file__).parent / "assets" / "channel_logo.jpg"
W, H = 1920, 1080
DEFAULT_QUALITY = "480P  •  720P  •  1080P"
DEFAULT_AUDIO = "Tamil Audio"
DEFAULT_JOIN = "Telegram Join → @AnimeNetworkTamil"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("AnimeNetworkTamil")
http = requests.Session()
http.headers["Accept"] = "application/json"
if TMDB_TOKEN:
    http.headers["Authorization"] = "Bearer " + TMDB_TOKEN


def api(path, params=None):
    params = dict(params or {})
    if not TMDB_TOKEN:
        if not TMDB_KEY:
            raise RuntimeError("Add TMDB_READ_ACCESS_TOKEN or TMDB_API_KEY to Render Environment.")
        params["api_key"] = TMDB_KEY
    r = http.get(API + path, params=params, timeout=25)
    if r.status_code == 401:
        raise RuntimeError("TMDB credential rejected; check your Render Environment variables.")
    r.raise_for_status()
    return r.json()


def search(query):
    out = []
    for kind in ("tv", "movie"):
        data = api("/search/" + kind, {"query": query, "include_adult": "false", "language": "en-US", "page": 1})
        for x in data.get("results", []):
            x["kind"] = kind
            x["title"] = x.get("name") or x.get("title") or x.get("original_name") or x.get("original_title") or "Untitled"
            x["date"] = x.get("first_air_date") or x.get("release_date") or ""
            out.append(x)
    out.sort(key=lambda x: x.get("popularity", 0), reverse=True)
    return [x for x in out if x.get("poster_path")][:8]


def details(item):
    d = api(f"/{item['kind']}/{item['id']}", {"language": "en-US"})
    return {
        "title": d.get("name") or d.get("title") or item["title"],
        "overview": (d.get("overview") or item.get("overview") or "Synopsis unavailable.").strip(),
        "year": (d.get("first_air_date") or d.get("release_date") or item.get("date") or "")[:4],
        "rating": d.get("vote_average") or item.get("vote_average") or 0,
        "genres": [g["name"] for g in d.get("genres", []) if g.get("name")][:3],
        "poster_path": d.get("poster_path") or item.get("poster_path"),
    }


def font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def wrap(draw, text, fnt, maxw):
    lines, cur = [], ""
    for word in text.split():
        test = (cur + " " + word).strip()
        if draw.textbbox((0, 0), test, font=fnt)[2] <= maxw:
            cur = test
        else:
            if cur:
                lines.append(cur)
            # Avoid a single long word extending beyond the right edge.
            if draw.textbbox((0, 0), word, font=fnt)[2] > maxw:
                cur = ""
                for char in word:
                    if draw.textbbox((0, 0), cur + char, font=fnt)[2] <= maxw:
                        cur += char
                    else:
                        if cur:
                            lines.append(cur)
                        cur = char
            else:
                cur = word
    if cur:
        lines.append(cur)
    return lines


def put_lines(draw, lines, xy, fnt, fill, gap=12, stroke=0, stroke_fill="#000"):
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
        b = draw.textbbox((x, y), line, font=fnt, stroke_width=stroke)
        y += b[3] - b[1] + gap
    return y


def make_poster(info):
    if not info.get("poster_path"):
        raise RuntimeError("No poster artwork found.")
    r = http.get(IMG + "w780" + info["poster_path"], timeout=30)
    r.raise_for_status()
    poster = Image.open(io.BytesIO(r.content)).convert("RGB")

    bg = ImageOps.fit(poster, (W, H), method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(28))
    bg = Image.eval(bg, lambda p: int(p * .36)).convert("RGBA")
    bg.alpha_composite(Image.new("RGBA", (W, H), (5, 7, 18, 125)))
    canvas = bg

    # Original vertical poster art on the left.
    pw, ph = poster.size
    ph2 = 820
    pw2 = int(pw * ph2 / ph)
    if pw2 > 590:
        pw2, ph2 = 590, int(ph * 590 / pw)
    poster = poster.resize((pw2, ph2), Image.Resampling.LANCZOS)
    px, py = 72, (H - ph2) // 2
    shadow = Image.new("RGBA", (pw2 + 50, ph2 + 50), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((20, 20, pw2 + 30, ph2 + 30), radius=18, fill=(0, 0, 0, 180))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)), (px - 24, py - 24))
    canvas.alpha_composite(poster.convert("RGBA"), (px, py))
    d = ImageDraw.Draw(canvas)

    tx, maxw = 735, 1090
    title = info["title"].upper()
    title_font = font(72 if len(title) < 23 else 58, True)
    y = put_lines(d, wrap(d, title, title_font, maxw)[:2], (tx, 95), title_font, "white", 8, 2, "#101322")
    y += 18
    d.rounded_rectangle((tx, y, tx + 175, y + 9), radius=4, fill="#ff3158")
    y += 35
    d.text((tx, y), "SYNOPSIS", font=font(29, True), fill="#ffb547")
    y += 48

    # Larger synopsis: up to 8 lines, with a generous character limit.
    synopsis = re.sub(r"\s+", " ", info.get("overview", "")).strip() or "Synopsis unavailable."
    if len(synopsis) > 620:
        synopsis = synopsis[:620].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    synopsis_font = font(25)
    synopsis_lines = wrap(d, synopsis, synopsis_font, maxw)[:8]
    y = put_lines(d, synopsis_lines, (tx, y), synopsis_font, "#F0F1FA", 10)

    metadata = []
    if info.get("year"):
        metadata.append(info["year"])
    if info.get("rating"):
        try:
            metadata.append(f"TMDB ★ {float(info['rating']):.1f}/10")
        except (ValueError, TypeError):
            pass
    metadata.extend(info.get("genres", []))
    # Keep a fixed lower section for the requested default details.
    meta_y = min(max(y + 8, 635), 715)
    if metadata:
        mf = font(21, True)
        put_lines(d, wrap(d, "  •  ".join(metadata), mf, maxw)[:2], (tx, meta_y), mf, "#ffcf78", 7)

    details_y = 780
    d.text((tx, details_y), DEFAULT_QUALITY, font=font(27, True), fill="white", stroke_width=1, stroke_fill="#111222")
    d.text((tx, details_y + 43), "🔊  " + DEFAULT_AUDIO, font=font(25, True), fill="#ffcf78", stroke_width=1, stroke_fill="#111222")
    d.text((tx, details_y + 87), "✈  " + DEFAULT_JOIN, font=font(24, True), fill="white", stroke_width=1, stroke_fill="#111222")

    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA")
        logo.thumbnail((155, 105), Image.Resampling.LANCZOS)
        logo.putalpha(logo.getchannel("A").point(lambda a: int(a * .95)))
        canvas.alpha_composite(logo, (W - logo.width - 40, H - logo.height - 34))
    d = ImageDraw.Draw(canvas)
    d.text((W - 35, H - 8), "ANIMENETWORKTAMIL", font=font(19, True), fill="white", anchor="rs", stroke_width=2, stroke_fill="#111222")

    # JPEG is sent as a Telegram photo (not a document) and is usually smaller than PNG.
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=94, optimize=True)
    out.seek(0)
    safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "-", info["title"]).strip("-").lower()
    out.name = (safe_title or "anime") + "-AnimeNetworkTamil.jpg"
    return out


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🎌 Welcome to AnimeNetworkTamil Poster Bot!\n\n"
        "Create 16:9 (1920×1080) posters using TMDB artwork, a larger synopsis, year, rating, genres, logo and watermark.\n\n"
        f"Every poster includes:\n• {DEFAULT_QUALITY}\n• {DEFAULT_AUDIO}\n• {DEFAULT_JOIN}\n\n"
        "Use /poster Solo Leveling or /help."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Commands:\n/poster <anime name> — create a 16:9 poster\n/search <anime name> — search titles\n"
        "/synopsis <anime name> — get synopsis and metadata\n/help — commands\n\nExample: /poster Demon Slayer"
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text("Usage: /poster <anime name>\nExample: /poster Solo Leveling")
        return
    msg = await update.effective_message.reply_text("🔎 Searching TMDB…")
    try:
        items = search(query)
        if not items:
            await msg.edit_text("No poster results found. Try another spelling.")
            return
        context.user_data["results"] = {str(i): x for i, x in enumerate(items)}
        buttons = []
        for i, x in enumerate(items):
            label = x["title"] + (f" ({x['date'][:4]})" if x["date"] else "")
            buttons.append([InlineKeyboardButton(label[:58], callback_data=f"poster:{i}")])
        await msg.edit_text("Choose the correct title:", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        log.exception("Search failed")
        await msg.edit_text(f"⚠️ Search failed: {e}")


async def synopsis_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text("Usage: /synopsis <anime name>")
        return
    try:
        items = search(query)
        if not items:
            await update.effective_message.reply_text("No matching title found.")
            return
        d = details(items[0])
        msg = f"📺 {d['title']}\n\n{d['overview'][:900]}"
        if d["year"]:
            msg += f"\n\n📅 Year: {d['year']}"
        if d["rating"]:
            msg += f"\n⭐ TMDB rating: {float(d['rating']):.1f}/10"
        if d["genres"]:
            msg += "\n🎭 Genres: " + ", ".join(d["genres"])
        msg += f"\n\n🎞 {DEFAULT_QUALITY}\n🔊 {DEFAULT_AUDIO}\n✈ {DEFAULT_JOIN}"
        await update.effective_message.reply_text(msg)
    except Exception as e:
        log.exception("Synopsis lookup failed")
        await update.effective_message.reply_text(f"⚠️ Could not retrieve synopsis: {e}")


async def poster_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    item = context.user_data.get("results", {}).get(q.data.split(":", 1)[1])
    if not item:
        await q.edit_message_text("Search expired. Please run /poster again.")
        return
    await q.edit_message_text(f"🎨 Creating 1920×1080 poster for {item['title']}…")
    try:
        d = details(item)
        image = make_poster(d)
        caption = f"🎌 {d['title']}"
        if d["year"]:
            caption += f"\n📅 {d['year']}"
        if d["rating"]:
            caption += f"  ⭐ {float(d['rating']):.1f}/10"
        caption += f"\n <blockquote expandable {synopsis_lines} /n </blockquote>🎞 {DEFAULT_QUALITY}\n🔊 {DEFAULT_AUDIO}\n✈ {DEFAULT_JOIN}\n\nAnimeNetworkTamil"
        # send_photo makes Telegram display it as a normal photo, not a document.
        await context.bot.send_photo(chat_id=q.message.chat_id, photo=image, caption=caption[:1024])
        await q.message.reply_text("✅ Poster ready! Sent as a photo. Artwork and metadata are subject to TMDB terms and applicable rights.")
    except Exception as e:
        log.exception("Poster creation failed")
        await q.message.reply_text(f"⚠️ Couldn't create poster: {e}")


async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Use /poster followed by an anime name. Example: /poster Naruto")


def main():
    if not BOT_TOKEN:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN in Render Environment.")
    if not TMDB_TOKEN and not TMDB_KEY:
        raise SystemExit("Missing TMDB_READ_ACCESS_TOKEN or TMDB_API_KEY in Render Environment.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("poster", search_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("synopsis", synopsis_cmd))
    app.add_handler(CallbackQueryHandler(poster_callback, pattern=r"^poster:\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback))
    log.info("AnimeNetworkTamil Poster Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    # Compatibility workaround for Python versions where no event loop exists yet.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    main()
