"""Anime Poster Bot — single fixed Neon Cinematic landscape poster.

Environment variables:
  TELEGRAM_BOT_TOKEN (required)
  TMDB_READ_ACCESS_TOKEN (recommended) or TMDB_API_KEY
  CHANNEL_USERNAME (optional, e.g. @AnimeNetworkTamil)
  DEVELOPER_USERNAME (optional)

Install: pip install -r requirements.txt
Run: python bot.py
"""
import os, io, logging, asyncio, textwrap, threading
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

SEARCH, IMAGE, SEASON, EPISODES = range(4)
POSTER_STYLE = "NEON CINEMATIC"


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
            d.line((x, 0, x-180, h), fill=(15, 65, 48), width=3)
        return out
    ratio = max(w / im.width, h / im.height)
    im = im.resize((int(im.width*ratio), int(im.height*ratio)), Image.Resampling.LANCZOS)
    x = (im.width-w)//2; y = (im.height-h)//2
    return im.crop((x, y, x+w, y+h))


def fit_text(draw, text, max_width, start=70, minimum=24, bold=True):
    for size in range(start, minimum-1, -2):
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
            if line: lines.append(line)
            line = word
    if line: lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textbbox((0, 0), lines[-1] + "…", font=fnt)[2] > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


def make_poster(info: dict, hero: Image.Image, thumbs: list,
               season="1", episodes="1 - 12") -> io.BytesIO:
    """Render the bot's single fixed Neon Cinematic poster."""
    W, H = 1920, 1080
    # Keep the selected anime artwork bright, sharp and colourful.
    # Use only a light overall tint, then a stronger left-side gradient
    # behind the text so the right side of the artwork stays visible.
    base = cover(hero, (W, H)).convert("RGB")
    base = ImageEnhance.Color(base).enhance(1.48)
    base = ImageEnhance.Contrast(base).enhance(1.10)
    base = ImageEnhance.Brightness(base).enhance(1.08)
    base = base.convert("RGBA")

    light_shade = Image.new("RGBA", (W, H), (5, 7, 24, 38))
    base = Image.alpha_composite(base, light_shade)
    grad = Image.new("RGBA", (W, H))
    gd = ImageDraw.Draw(grad)
    for x in range(W):
        # Fade the text-protection gradient before the centre so the
        # main character/artwork on the right remains vivid.
        alpha = int(178 * (1 - min(x / 1180, 1)) ** 1.35)
        gd.line((x, 0, x, H), fill=(4, 7, 24, alpha))
    base = Image.alpha_composite(base, grad)

    d = ImageDraw.Draw(base)
    cyan = (65, 225, 255, 255)
    purple = (168, 95, 255, 255)
    white = (248, 250, 255, 255)
    gold = (255, 211, 95, 255)
    panel = (7, 10, 29, 218)

    d.rounded_rectangle((18, 18, W-18, H-18), radius=26,
                        outline=(95, 105, 255, 230), width=4)
    d.line((42, 190, 1110, 190), fill=cyan, width=4)

    title = (info.get("name") or "ANIME").upper()
    title_font = fit_text(d, title, 1080, 104, 48, True)
    d.text((48, 38), title, font=title_font, fill=white,
           stroke_width=5, stroke_fill=(0, 0, 0, 230))

    media_label = "ANIME SERIES" if info.get("media_type") == "tv" else "ANIME MOVIE"
    year = (info.get("first_air_date") or info.get("release_date") or "")[:4]
    d.text((58, 150), f"{media_label}  |  {year or 'YEAR N/A'}",
           font=font(28, True), fill=cyan)

    season_label = f"SEASON {season}" if str(season) != "0" else "MOVIE"
    rating = info.get("vote_average")
    rating_text = f"{float(rating):.1f}/10" if rating not in (None, "", 0) else "NOT RATED"
    tiles = [
        ("STATUS", info.get("status_label", "AVAILABLE")),
        ("SEASON", season_label),
        ("AUDIO", info.get("audio", "TAMIL")),
        ("QUALITY", info.get("quality", "480P | 720P | 1080P")),
        ("GENRE", info.get("genres", "ANIME")),
        ("RATING", rating_text),
    ]
    tile_x, tile_y, tile_w, tile_h = [30, 342, 654], [220, 322], 292, 84
    for index, (label, value) in enumerate(tiles):
        x, y = tile_x[index % 3], tile_y[index // 3]
        edge = cyan if index % 2 == 0 else purple
        d.rounded_rectangle((x, y, x+tile_w, y+tile_h), radius=14,
                            fill=panel, outline=edge, width=3)
        d.text((x+16, y+10), label, font=font(18, True), fill=edge)
        value_font = fit_text(d, str(value), tile_w-32, 29, 17, True)
        d.text((x+16, y+39), str(value), font=value_font, fill=gold)

    sx, sy, sw, sh = 30, 430, 430, 574
    d.rounded_rectangle((sx, sy, sx+sw, sy+sh), radius=18,
                        fill=panel, outline=purple, width=3)
    d.text((sx+22, sy+20), "SYNOPSIS", font=font(31, True), fill=cyan)
    synopsis_font = font(23, False)
    for i, line in enumerate(wrap_lines(
            d, info.get("overview") or "Synopsis not available.",
            synopsis_font, sw-44, 17)):
        d.text((sx+22, sy+76+i*29), line, font=synopsis_font, fill=white)

    preview_x, preview_y = 490, 690
    preview_w, preview_h, gap = 340, 200, 18
    for i, im in enumerate(thumbs[:4]):
        x = preview_x + i * (preview_w + gap)
        tile = cover(im, (preview_w, preview_h)).convert("RGBA")
        base.alpha_composite(tile, (x, preview_y))
        d = ImageDraw.Draw(base)
        edge = cyan if i % 2 == 0 else purple
        d.rounded_rectangle((x, preview_y, x+preview_w, preview_y+preview_h),
                            radius=14, outline=edge, width=4)

    ex, ey = 490, 920
    d.rounded_rectangle((ex, ey, 1880, ey+78), radius=15,
                        fill=panel, outline=cyan, width=3)
    d.text((ex+28, ey+13), "EPISODES", font=font(38, True), fill=white)
    episode_font = fit_text(d, str(episodes), 700, 42, 24, True)
    d.text((ex+270, ey+14), str(episodes), font=episode_font, fill=gold)
    d.text((1430, ey+20), "ANIMENETWORKTAMIL",
           font=font(25, True), fill=cyan)

    out = io.BytesIO()
    base.convert("RGB").save(out, "JPEG", quality=94, optimize=True)
    out.seek(0)
    return out


def get_images(info):
    kind, mid = info["media_type"], info["id"]
    try:
        data = tmdb(f"/{kind}/{mid}/images", {"include_image_language":"en,null"})
        paths = [x["file_path"] for x in data.get("backdrops", [])[:6]]
        if not paths: paths = [x["file_path"] for x in data.get("posters", [])[:6]]
        return paths
    except Exception as e:
        log.warning("Could not get image list: %s", e)
        return [info.get("backdrop_path") or info.get("poster_path")]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 *Anime Poster Bot*\n\nUse `/p Naruto Shippuden` to create a Neon Cinematic poster.\nChoose a TMDB image or upload your own, then enter the season and episode range.",
        parse_mode=ParseMode.MARKDOWN)


async def poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Send an anime name: `/p Naruto Shippuden`", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END
    try:
        data = tmdb("/search/multi", {"query":query, "include_adult":"false"})
        results = [x for x in data.get("results",[]) if x.get("media_type") in ("tv","movie")][:8]
        if not results:
            await update.message.reply_text("No matching title found. Try another spelling.")
            return ConversationHandler.END
        context.user_data["results"] = results
        rows=[]
        for i,x in enumerate(results):
            name=x.get("name") or x.get("title") or "Untitled"
            year=(x.get("first_air_date") or x.get("release_date") or "")[:4]
            rows.append([InlineKeyboardButton(f"{name} {('('+year+')') if year else ''} · {x['media_type'].upper()}", callback_data=f"pick:{i}")])
        await update.message.reply_text("Choose the correct anime:", reply_markup=InlineKeyboardMarkup(rows))
        return SEARCH
    except Exception as e:
        log.exception("Search failed")
        await update.message.reply_text(f"TMDB search failed: {e}")
        return ConversationHandler.END


async def picked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    try:
        idx=int(q.data.split(":")[1]); result=context.user_data["results"][idx]
        kind=result["media_type"]
        info=tmdb(f"/{kind}/{result['id']}", {"append_to_response":"credits"})
        info["media_type"]=kind; info["name"]=info.get("name") or info.get("title")
        info["genres"]=", ".join(g["name"] for g in info.get("genres",[])[:4]) or "ANIME"
        info["status_label"]="ONGOING" if info.get("status")=="Returning Series" else (info.get("status") or "AVAILABLE").upper()
        context.user_data["info"]=info
        paths=get_images(info)
        context.user_data["image_paths"]=[p for p in paths if p]
        rows=[]
        for i,p in enumerate(context.user_data["image_paths"]):
            rows.append([InlineKeyboardButton(f"Use TMDB image {i+1}", callback_data=f"img:{i}")])
        rows.append([InlineKeyboardButton("📤 Upload my own image", callback_data="img:upload")])
        await q.edit_message_text("Choose the main poster/background image, or upload your own photo:", reply_markup=InlineKeyboardMarkup(rows))
        return IMAGE
    except Exception as e:
        log.exception("Title selection failed")
        await q.edit_message_text(f"Could not load title details: {e}")
        return ConversationHandler.END


async def image_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    choice=q.data.split(":",1)[1]
    if choice == "upload":
        context.user_data["awaiting_upload"]=True
        await q.edit_message_text("Now send the image as a photo or image document. I’ll use it as the main background.")
        return IMAGE
    try:
        path=context.user_data["image_paths"][int(choice)]
        hero=fetch_image(path)
        if hero is None: raise RuntimeError("That TMDB image could not be downloaded.")
        context.user_data["hero"]=hero
        context.user_data["thumb_paths"]=[p for p in context.user_data["image_paths"] if p != path][:4]
        await q.edit_message_text("Neon Cinematic style selected automatically.\n\nEnter the season number (example: 18). Send 0 for a movie or no season.")
        return SEASON
    except Exception as e:
        await q.edit_message_text(str(e)); return ConversationHandler.END


async def image_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_upload"):
        await update.message.reply_text("Use the image-selection buttons first, or start again with /p.")
        return IMAGE
    try:
        obj=update.message.photo[-1] if update.message.photo else update.message.document
        if not obj or (update.message.document and not (update.message.document.mime_type or "").startswith("image/")):
            await update.message.reply_text("Please send a valid image."); return IMAGE
        f=await context.bot.get_file(obj.file_id)
        b=io.BytesIO(); await f.download_to_memory(out=b); b.seek(0)
        context.user_data["hero"]=Image.open(b).convert("RGB")
        context.user_data["thumb_paths"]=context.user_data.get("image_paths",[])[:4]
        context.user_data["awaiting_upload"]=False
        await update.message.reply_text("Neon Cinematic style selected automatically.\n\nEnter the season number (example: 18). Send 0 for a movie or no season.")
        return SEASON
    except Exception as e:
        log.exception("Upload failed")
        await update.message.reply_text(f"Could not read that image: {e}"); return IMAGE


async def season_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value=update.message.text.strip()
    if not value.isdigit() or int(value)>999:
        await update.message.reply_text("Please enter a number, for example 18. Use 0 for a movie.")
        return SEASON
    context.user_data["season"]=value
    await update.message.reply_text("Enter the episode range (example: 373 - 393). For a movie, send `Movie`.", parse_mode=ParseMode.MARKDOWN)
    return EPISODES


async def episodes_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    episodes=update.message.text.strip()[:40]
    if not episodes:
        await update.message.reply_text("Please enter an episode range or Movie."); return EPISODES
    ud=context.user_data; info=ud["info"]
    await update.message.reply_text("Creating your poster…")
    try:
        thumbs=[]
        for p in ud.get("thumb_paths",[])[:4]:
            im=fetch_image(p,"w500")
            if im: thumbs.append(im)
        poster_img=make_poster(info, ud["hero"], thumbs, ud.get("season", "1"), episodes)
        name=info.get("name","Anime")
        season=int(ud.get("season","1"))
        season_line=f"Season {season}" if season else "Movie"
        year=(info.get("first_air_date") or info.get("release_date") or "")[:4]
        genres=info.get("genres","Anime")
        overview=info.get("overview") or "Synopsis not available."
        caption=(f"<b><b>🎌 {name}</b>\n"
                 f"<b>〄 Season :</b> {season_line}\n"
                 f"<b>〄 Episode :</b> {episodes}\n"
                 f"<b>〄 Audio :</b> Tamil\n"
                 f"<b>〄 Quality :</b> 480P | 720P | 1080P\n"
                 f"<b>〄 Status :</b> {info.get('status_label','AVAILABLE')}\n"
                 f"<b>〄 Year :</b> {year or 'N/A'}\n"
                 f"<b>〄 Genre :</b> {genres}</b>\n\n"
                 f"<blockquote expandable><b>Synopsis :</b> {overview}</blockquote>")
        await update.message.reply_photo(photo=poster_img, caption=caption, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("Poster generation failed")
        await update.message.reply_text(f"Poster generation failed: {e}")
    finally:
        for k in ("info","hero","thumb_paths","image_paths","season","results","awaiting_upload"):
            ud.pop(k,None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled. Start again with /p Anime Name.")
    return ConversationHandler.END


def main():
    if not TOKEN:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN environment variable.")
    app=Application.builder().token(TOKEN).build()
    conv=ConversationHandler(
        entry_points=[CommandHandler("p",poster)],
        states={
            SEARCH:[CallbackQueryHandler(picked,pattern=r"^pick:\d+$")],
            IMAGE:[CallbackQueryHandler(image_chosen,pattern=r"^img:"), MessageHandler(filters.PHOTO | filters.Document.IMAGE,image_upload)],
            SEASON:[MessageHandler(filters.TEXT & ~filters.COMMAND,season_entered)],
            EPISODES:[MessageHandler(filters.TEXT & ~filters.COMMAND,episodes_entered)],
        },
        fallbacks=[CommandHandler("cancel",cancel),CommandHandler("start",start)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("help",start))
    app.add_handler(conv)
    start_health_server()
    log.info("Anime Poster Bot started")
    # Python 3.14 no longer creates a default event loop automatically.
    # python-telegram-bot 21.x run_polling() expects one to exist.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
