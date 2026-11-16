import os, io, logging, asyncio, textwrap
from typing import Optional
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler,
                          ContextTypes, ConversationHandler, filters)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("anime-poster-bot")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TMDB_TOKEN = os.getenv("TMDB_READ_ACCESS_TOKEN", "").strip()
TMDB_KEY = os.getenv("TMDB_API_KEY", "").strip()
CHANNEL = os.getenv("CHANNEL_USERNAME", "@AnimeNetworkTamil").strip()
TMDB_API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"

SEARCH, IMAGE, SEASON, EPISODES = range(4)
TEMPLATES = {
    "neon_info": "Neon Info (Reference)",
    "cinematic": "Cinematic",
    "broadcast": "Broadcast",
    "magazine": "Magazine",
    "minimal": "Minimal",
    "retro": "Retro",
    "purple": "Purple Night",
    "mono": "Monochrome",
    "editorial": "Editorial",
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


def make_poster(info: dict, hero: Image.Image, thumbs: list, template="neon_info", season="1", episodes="1 - 12") -> io.BytesIO:
    W, H = 1920, 1080
    base = cover(hero, (W, H))
    base = ImageEnhance.Color(base).enhance(1.25).filter(ImageFilter.GaussianBlur(1.2))
    # Darken background while retaining the selected anime image.
    shade = Image.new("RGBA", (W, H), (0, 8, 7, 105 if template != "minimal" else 145))
    base = Image.alpha_composite(base.convert("RGBA"), shade)
    # Left-to-right shadow helps information remain readable.
    grad = Image.new("RGBA", (W, H)); gd = ImageDraw.Draw(grad)
    for x in range(W):
        a = int(185 * (1 - x/W) ** 1.35)
        gd.line((x, 0, x, H), fill=(0, 0, 0, a))
    base = Image.alpha_composite(base, grad)
    d = ImageDraw.Draw(base)
    accent = (55, 255, 150, 255) if template in ("neon_info", "broadcast") else (255, 190, 50, 255)
    panel = (0, 0, 0, 205)
    # Header title area
    title = (info.get("name") or "ANIME").upper()
    title_font = fit_text(d, title, 790, 105, 45, True)
    d.text((48, 34), title, font=title_font, fill=(255, 255, 255, 255), stroke_width=5, stroke_fill=(0, 0, 0, 230))
    subtitle = "ANIME SERIES" if info.get("media_type") == "tv" else "ANIME MOVIE"
    d.text((58, 150), subtitle, font=font(28, True), fill=accent, stroke_width=1, stroke_fill=(0,0,0,255))

    # Info tiles, matching the reference layout.
    left, top, gap = 30, 218, 16
    tiles = [
        ("STATUS", info.get("status_label", "AVAILABLE"), "▶"),
        ("SEASON", f"Season {season}", "▰"),
        ("AUDIO", info.get("audio", "TAMIL"), "♫"),
        ("QUALITY", info.get("quality", "480P | 720P | 1080P"), "HD"),
        ("GENRE", info.get("genres", "ANIME"), "✦"),
        ("SOURCE", info.get("source", "ANIMENETWORKTAMIL"), "◉"),
    ]
    widths = [320, 340, 320, 480, 440, 350]
    positions = [(left,top),(left+336,top),(left,top+96),(left+336,top+96),(left,top+192),(left+456,top+192)]
    for (label, value, symbol), (x,y), ww in zip(tiles, positions, widths):
        d.rounded_rectangle((x,y,x+ww,y+82), radius=12, fill=panel, outline=accent, width=3)
        d.text((x+16,y+9), symbol, font=font(34, True), fill=accent)
        d.text((x+66,y+8), label, font=font(19, True), fill=(245,245,245,255))
        vf = fit_text(d, str(value), ww-82, 29, 17, True)
        d.text((x+66,y+36), str(value), font=vf, fill=(255, 211, 55, 255))

    # Synopsis card at lower left.
    sx, sy, sw, sh = 30, 506, 440, 520
    d.rounded_rectangle((sx,sy,sx+sw,sy+sh), radius=14, fill=panel, outline=accent, width=3)
    d.text((sx+22,sy+18), "▣  SYNOPSIS", font=font(31, True), fill=(255, 210, 45, 255))
    synopsis = info.get("overview") or "Synopsis not available."
    sf = font(24, False)
    for i, line in enumerate(wrap_lines(d, synopsis, sf, sw-44, 15)):
        d.text((sx+22, sy+72+i*29), line, font=sf, fill=(245,245,245,255))

    # Four scene previews across the lower middle/right.
    thumb_x, thumb_y, tw, th, tg = 490, 688, 340, 200, 16
    for i in range(4):
        im = thumbs[i] if i < len(thumbs) else None
        tile = cover(im, (tw,th), (12,35,27))
        x = thumb_x + i*(tw+tg)
        base.alpha_composite(tile.convert("RGBA"), (x,thumb_y))
        d = ImageDraw.Draw(base)
        d.rounded_rectangle((x,thumb_y,x+tw,thumb_y+th), radius=12, outline=accent, width=4)

    # Episode strip
    ex, ey = 500, 914
    d.rounded_rectangle((ex,ey,ex+1410,ey+94), radius=14, fill=(0,0,0,205), outline=accent, width=3)
    d.text((ex+40,ey+13), "EPISODE :", font=font(46, True), fill=(255,255,255,255))
    d.text((ex+345,ey+13), str(episodes), font=font(48, True), fill=(255,215,45,255))
    d.text((ex+1040,ey+17), info.get("status_label", "ONGOING").upper(), font=font(38, True), fill=accent)

    # Footer/channel tag
    footer = CHANNEL if CHANNEL else "ANIME NETWORK TAMIL"
    d.text((W-520,H-48), footer, font=font(22,True), fill=(255,255,255,220), stroke_width=2, stroke_fill=(0,0,0,220))
    out = io.BytesIO(); base.convert("RGB").save(out, "JPEG", quality=93, optimize=True); out.seek(0)
    return out


def template_keyboard():
    keys = list(TEMPLATES.items())
    rows = [[InlineKeyboardButton(label, callback_data=f"tpl:{key}")] for key,label in keys]
    return InlineKeyboardMarkup(rows)


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
        "🎨 *Anime Poster Bot*\n\nUse `/p Naruto Shippuden` to create a poster.\nYou can choose a TMDB image or upload your own, select a template, and enter the season and episode range manually.",
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
        await q.edit_message_text("Choose a poster template:", reply_markup=template_keyboard())
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
        await update.message.reply_text("Choose a poster template:", reply_markup=template_keyboard())
        return SEASON
    except Exception as e:
        log.exception("Upload failed")
        await update.message.reply_text(f"Could not read that image: {e}"); return IMAGE


async def template_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    key=q.data.split(":",1)[1]
    context.user_data["template"]=key
    await q.edit_message_text(f"Template: {TEMPLATES.get(key,key)}\n\nEnter the season number (example: 18). Send 0 for a movie or no season.")
    return SEASON


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
        poster_img=make_poster(info,ud["hero"],thumbs,ud.get("template","neon_info"),ud.get("season","1"),episodes)
        name=info.get("name","Anime")
        season=int(ud.get("season","1"))
        season_line=f"Season {season}" if season else "Movie"
        year=(info.get("first_air_date") or info.get("release_date") or "")[:4]
        genres=info.get("genres","Anime")
        overview=info.get("overview") or "Synopsis not available."
        caption=(f"<b>🎌 {name}</b>\n"
                 f"<b>〄 Season :</b> {season_line}\n"
                 f"<b>〄 Episode :</b> {episodes}\n"
                 f"<b>〄 Audio :</b> Tamil\n"
                 f"<b>〄 Quality :</b> 480P | 720P | 1080P\n"
                 f"<b>〄 Status :</b> {info.get('status_label','AVAILABLE')}\n"
                 f"<b>〄 Year :</b> {year or 'N/A'}\n"
                 f"<b>〄 Genre :</b> {genres}\n\n"
                 f"<blockquote expandable><b>Synopsis :</b> {overview}</blockquote>")
        await update.message.reply_photo(photo=poster_img, caption=caption, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("Poster generation failed")
        await update.message.reply_text(f"Poster generation failed: {e}")
    finally:
        for k in ("info","hero","thumb_paths","image_paths","template","season","results","awaiting_upload"):
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
            SEASON:[CallbackQueryHandler(template_chosen,pattern=r"^tpl:"), MessageHandler(filters.TEXT & ~filters.COMMAND,season_entered)],
            EPISODES:[MessageHandler(filters.TEXT & ~filters.COMMAND,episodes_entered)],
        },
        fallbacks=[CommandHandler("cancel",cancel),CommandHandler("start",start)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("help",start))
    app.add_handler(conv)
    log.info("Anime Poster Bot started")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
