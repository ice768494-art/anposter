"""Anime Poster Bot — 4 landscape poster templates (2560x1440) with live button previews.

Flow:  /p <name> -> pick title -> browse TMDB images (◀ ▶ preview) -> browse templates
       (live preview of YOUR image) -> season -> episodes -> poster.

Environment variables:
  TELEGRAM_BOT_TOKEN (required)
  TMDB_READ_ACCESS_TOKEN (recommended) or TMDB_API_KEY
  CHANNEL_USERNAME (optional, e.g. @AnimeNetworkTamil)

Optional files next to bot.py:
  fonts/Poppins-Bold.ttf
  fonts/Poppins-SemiBold.ttf
  logo.png                      (square channel logo)

Install: pip install -r requirements.txt
Run: python bot.py
"""
import os, io, re, math, html, logging, asyncio, threading
from typing import Optional
import requests
from flask import Flask, jsonify
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode, ChatAction
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

SEARCH, IMAGE, TEMPLATE, SEASON, EPISODES, SYNOPSIS = range(6)

LANG_NAMES = {
    "ja": "JAPANESE", "ko": "KOREAN", "zh": "CHINESE", "en": "ENGLISH",
    "ta": "TAMIL", "hi": "HINDI", "te": "TELUGU", "ml": "MALAYALAM",
    "fr": "FRENCH", "es": "SPANISH", "de": "GERMAN",
}

W, H, M = 2560, 1440, 64
ROSE = (146, 82, 82, 255)
ROSE_TEXT = (255, 232, 232, 255)
DARK = (18, 18, 22, 235)
WHITE = (255, 255, 255, 255)
GOLD = (255, 196, 40, 255)


# ───────────────────────── TMDB / image helpers ─────────────────────────

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


def download_bytes(url: str) -> bytes:
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.content


def fetch_image(path: Optional[str], size="w1280") -> Optional[Image.Image]:
    if not path:
        return None
    try:
        return Image.open(io.BytesIO(download_bytes(IMG + size + path))).convert("RGB")
    except Exception as e:
        log.warning("Image download failed: %s", e)
        return None


def ensure_poster(info: dict) -> Optional[Image.Image]:
    """Portrait TMDB poster (cached on the info dict) — used by the Split Card template."""
    if "_poster_img" not in info:
        info["_poster_img"] = fetch_image(info.get("poster_path"), "w780")
    return info["_poster_img"]


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


# ───────────────────────── Shared poster helpers ─────────────────────────

def draw_dots(d, x, y, cols, rows, bright_right=True, gap=18, r=5):
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


def channel_handle():
    return CHANNEL if CHANNEL.startswith("@") else "@" + CHANNEL


def info_bits(info, season):
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
    genres = [g.strip().upper() for g in str(info.get("genres") or "ANIME").split(",") if g.strip()][:4]
    return lang, rating_txt, season_txt, genres


def prep_art(hero, color=1.2, contrast=1.08):
    base = cover(hero, (W, H)).convert("RGB")
    base = ImageEnhance.Color(base).enhance(color)
    base = ImageEnhance.Contrast(base).enhance(contrast)
    return base.convert("RGBA")


def v_gradient(base, y, h, a0, a1, color=(6, 6, 10)):
    """Vertical dark gradient overlay: alpha a0 at the top edge -> a1 at the bottom edge."""
    g = Image.linear_gradient("L").resize((base.width, h))
    g = g.point(lambda v: int(a0 + (a1 - a0) * v / 255))
    layer = Image.new("RGBA", (base.width, h), color + (0,))
    layer.putalpha(g)
    base.alpha_composite(layer, (0, y))


def fit_title(draw, title, max_w, max_lines=2, start=150, minimum=56):
    for size in range(start, minimum - 1, -4):
        f = font(size, True)
        ls = wrap_lines(draw, title, f, max_w, 10)
        if len(ls) <= max_lines and max(draw.textlength(x, font=f) for x in ls) <= max_w:
            return f, ls
    f = font(minimum, True)
    return f, wrap_lines(draw, title, f, max_w, max_lines)


def draw_title(base, lines, fnt, x, top, line_h, align="l"):
    anc = "la" if align == "l" else "ma"
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    for i, line in enumerate(lines):
        sd.text((x + 4, top + i * line_h + 6), line, font=fnt, fill=(0, 0, 0, 215), anchor=anc)
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(9)))
    d = ImageDraw.Draw(base)
    for i, line in enumerate(lines):
        d.text((x, top + i * line_h), line, font=fnt, fill=WHITE, anchor=anc)


def pill_width(d, text, f, star=False):
    return int(d.textlength(text, font=f) + 64 + (46 if star else 0))


def pills_total(d, items, size=30, gap=28):
    f = font(size, True)
    return sum(pill_width(d, t, f, s) for t, _, _, s in items) + gap * (len(items) - 1)


def draw_pills(d, x, y, items, h=60, size=30, gap=28):
    """items = [(text, fill, text_fill, has_star)]. Returns x after the last pill."""
    f = font(size, True)
    for text, fill, tfill, star in items:
        w = pill_width(d, text, f, star)
        d.rounded_rectangle((x, y, x + w, y + h), radius=14, fill=fill)
        tx = x + 32
        if star:
            d.polygon(star_points(x + 32 + 17, y + h / 2 + 1, 19), fill=GOLD)
            tx += 46
        d.text((tx, y + h / 2 + 1), text, font=f, fill=tfill, anchor="lm")
        x += w + gap
    return x


def paste_logo(base, cx, cy, dia):
    """Square channel logo with rounded corners, or a fallback download-arrow badge."""
    if os.path.exists(LOGO_PATH):
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA").resize((dia, dia), Image.Resampling.LANCZOS)
            mask = Image.new("L", (dia, dia), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, dia - 1, dia - 1), radius=max(8, dia // 11), fill=255)
            bg = Image.new("RGBA", (dia, dia), (255, 255, 255, 255))
            bg.alpha_composite(logo)
            base.paste(bg, (cx - dia // 2, cy - dia // 2), mask)
            return
        except Exception as e:
            log.warning("Logo load failed: %s", e)
    ov = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    k = dia / 200
    d.ellipse((cx - dia // 2, cy - dia // 2, cx + dia // 2, cy + dia // 2), fill=ROSE)
    d.polygon([(cx - 42 * k, cy - 8 * k), (cx + 42 * k, cy - 8 * k), (cx, cy + 40 * k)], fill=WHITE)
    d.rectangle((cx - 12 * k, cy - 52 * k, cx + 12 * k, cy - 8 * k), fill=WHITE)
    base.alpha_composite(ov)


def finish(base, preview=False):
    img = base.convert("RGB")
    if preview:
        img = img.resize((1280, 720), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82 if preview else 93, optimize=True)
    out.seek(0)
    return out


# ───────────────────────── Template 1: Clean Cinematic ─────────────────────────

def tpl_clean(info, hero, season, episodes):
    base = prep_art(hero)
    v_gradient(base, H - int(H * 0.62), int(H * 0.62), 0, 238)  # bottom fade

    mw = int(W * 0.55)  # left fade
    g = Image.linear_gradient("L").rotate(90).transpose(Image.Transpose.FLIP_LEFT_RIGHT).resize((mw, H))
    g = g.point(lambda v: int(130 * (v / 255) ** 1.8))
    layer = Image.new("RGBA", (mw, H), (6, 6, 10, 0))
    layer.putalpha(g)
    base.alpha_composite(layer, (0, 0))

    genre_h, pills_h = 66, 60
    genre_y = H - 117 - genre_h
    pills_y = genre_y - 47 - pills_h
    accent_y = pills_y - 32

    d0 = ImageDraw.Draw(base)
    tf, lines = fit_title(d0, (info.get("name") or "ANIME").upper(), 1400)
    line_h = int(tf.size * 1.42)
    title_top = accent_y - 34 - line_h * len(lines)
    draw_title(base, lines, tf, M, title_top, line_h)

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle((M, accent_y, M + 128, accent_y + 6), radius=3, fill=ROSE)

    lang, rating_txt, season_txt, genres = info_bits(info, season)
    draw_pills(d, M, pills_y, [("OFFICIAL", ROSE, ROSE_TEXT, False), (lang, ROSE, ROSE_TEXT, False),
                               (rating_txt, DARK, WHITE, True), (season_txt, DARK, WHITE, False)])

    # genre bar
    lab_f = font(28, True)
    label_w = int(d.textlength("GENRE", font=lab_f))
    inner_x = M + 32 + label_w + 32
    gx, positions = inner_x + 32, []
    for gname in genres:
        positions.append((gx, gname))
        gx += int(d.textlength(gname, font=lab_f)) + 24
    bar_end = gx - 24 + 32
    d.rounded_rectangle((M, genre_y, bar_end, genre_y + genre_h), radius=12, fill=(20, 20, 24, 200))
    d.rounded_rectangle((inner_x, genre_y, bar_end, genre_y + genre_h), radius=12, fill=(255, 255, 255, 24))
    cy_g = genre_y + genre_h / 2 + 1
    d.text((M + 32, cy_g), "GENRE", font=lab_f, fill=(176, 104, 104, 255), anchor="lm")
    for gx0, gname in positions:
        d.text((gx0, cy_g), gname, font=lab_f, fill=WHITE, anchor="lm")

    # top-right: dots + channel pill
    draw_dots(d, W - M - 162, 34, 10, 2, True)
    hf = font(34, True)
    handle = channel_handle()
    hw = int(d.textlength(handle, font=hf)) + 76
    d.rounded_rectangle((W - M - hw, 125, W - M, 197), radius=36, fill=WHITE)
    d.text((W - M - hw / 2, 162), handle, font=hf, fill=(20, 20, 26, 255), anchor="mm")

    # bottom-right: Download Now + logo
    dia = 200
    lcx, lcy = W - M - dia // 2, H - 60 - dia // 2
    draw_dots(d, W - M - 144, lcy - dia // 2 - 36, 9, 2, False)
    d.text((lcx - dia // 2 - 36, lcy), "Download Now", font=font(62, True), fill=WHITE, anchor="rm")
    rcx, rcy, rr = lcx - 62, lcy - 62, 86
    d.ellipse((rcx - rr, rcy - rr, rcx + rr, rcy + rr), outline=(255, 255, 255, 215), width=4)
    base.alpha_composite(ov)
    paste_logo(base, lcx, lcy, dia)
    return base


# ───────────────────────── Template 2: Split Card ─────────────────────────

def tpl_split(info, hero, season, episodes):
    lang, rating_txt, season_txt, genres = info_bits(info, season)
    art = cover(hero, (W, H)).resize((W // 4, H // 4), Image.Resampling.LANCZOS)
    art = art.filter(ImageFilter.GaussianBlur(12)).resize((W, H), Image.Resampling.BICUBIC)
    art = ImageEnhance.Brightness(ImageEnhance.Color(art).enhance(1.3)).enhance(0.5)
    base = art.convert("RGBA")
    v_gradient(base, 0, H, 40, 120)

    CW, CH = 780, 1170
    cx0, cy0 = W - 150 - CW, (H - CH) // 2
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((cx0 + 10, cy0 + 24, cx0 + CW + 10, cy0 + CH + 24), radius=44, fill=(0, 0, 0, 200))
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(30)))
    card = cover(ensure_poster(info) or hero, (CW, CH)).convert("RGBA")
    mask = Image.new("L", (CW, CH), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CW - 1, CH - 1), radius=40, fill=255)
    base.paste(card, (cx0, cy0), mask)

    LM = 110
    d0 = ImageDraw.Draw(base)
    tf, lines = fit_title(d0, (info.get("name") or "ANIME").upper(), 1380, 2, 130, 56)
    line_h = int(tf.size * 1.3)
    pills_y, accent_y = 935, 895
    title_top = accent_y - 34 - line_h * len(lines)
    draw_title(base, lines, tf, LM, title_top, line_h)

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle((cx0, cy0, cx0 + CW, cy0 + CH), radius=40, outline=(255, 255, 255, 235), width=6)

    hf = font(34, True)
    handle = channel_handle()
    hw = int(d.textlength(handle, font=hf)) + 76
    d.rounded_rectangle((LM, 90, LM + hw, 162), radius=36, fill=WHITE)
    d.text((LM + hw / 2, 126), handle, font=hf, fill=(20, 20, 26, 255), anchor="mm")
    draw_dots(d, LM + hw + 50, 108, 10, 2, True)

    d.rounded_rectangle((LM, accent_y, LM + 128, accent_y + 6), radius=3, fill=ROSE)
    draw_pills(d, LM, pills_y, [("OFFICIAL", ROSE, ROSE_TEXT, False), (lang, ROSE, ROSE_TEXT, False),
                                (rating_txt, DARK, WHITE, True), (season_txt, DARK, WHITE, False)])
    d.text((LM, 1075), "  •  ".join(genres), font=font(34, True), fill=(235, 190, 190, 255), anchor="lm")
    if info.get("media_type") == "movie":
        ep_line = "FULL MOVIE"
    elif str(season) not in ("0", ""):
        ep_line = f"SEASON {season}  •  EPISODES {episodes}"
    else:
        ep_line = f"EPISODES {episodes}"
    d.text((LM, 1135), ep_line, font=font(36, True), fill=WHITE, anchor="lm")

    # download button
    cy_row, dia = 1265, 130
    bx = LM + dia + 34
    bf = font(38, True)
    bw = int(d.textlength("Download Now", font=bf)) + 64 + 90
    d.rounded_rectangle((bx, cy_row - 44, bx + bw, cy_row + 44), radius=44, fill=ROSE)
    acx = bx + 24 + 28
    d.ellipse((acx - 28, cy_row - 28, acx + 28, cy_row + 28), fill=WHITE)
    d.polygon([(acx - 13, cy_row - 6), (acx + 13, cy_row - 6), (acx, cy_row + 12)], fill=ROSE)
    d.text((acx + 28 + 24, cy_row + 1), "Download Now", font=bf, fill=WHITE, anchor="lm")

    base.alpha_composite(ov)
    paste_logo(base, LM + dia // 2, cy_row, dia)
    return base


# ───────────────────────── Template 3: Center Stage (letterbox) ─────────────────────────

def tpl_center(info, hero, season, episodes):
    lang, rating_txt, season_txt, genres = info_bits(info, season)
    TOP, BOT = 130, 300
    base = prep_art(hero)
    v_gradient(base, TOP, 260, 150, 0)
    v_gradient(base, 480, H - BOT - 480, 0, 235)

    d0 = ImageDraw.Draw(base)
    tf, lines = fit_title(d0, (info.get("name") or "ANIME").upper(), 2100, 2, 170, 70)
    line_h = int(tf.size * 1.3)
    draw_title(base, lines, tf, W // 2, (H - BOT - 50) - line_h * len(lines), line_h, "m")

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle((0, 0, W, TOP), fill=(8, 8, 10, 255))
    d.rectangle((0, H - BOT, W, H), fill=(8, 8, 10, 255))
    d.rectangle((0, TOP - 4, W, TOP), fill=ROSE)
    d.rectangle((0, H - BOT, W, H - BOT + 4), fill=ROSE)

    d.text((M + 96 + 28, TOP // 2), channel_handle(), font=font(38, True), fill=WHITE, anchor="lm")
    bf = font(34, True)
    tw = int(d.textlength("DOWNLOAD NOW", font=bf))
    d.rounded_rectangle((W - M - tw - 70, 28, W - M, 102), radius=37, fill=ROSE)
    d.text((W - M - tw / 2 - 35, 66), "DOWNLOAD NOW", font=bf, fill=WHITE, anchor="mm")

    items = [("OFFICIAL", ROSE, ROSE_TEXT, False), (lang, ROSE, ROSE_TEXT, False),
             (rating_txt, (40, 40, 48, 255), WHITE, True), (season_txt, (40, 40, 48, 255), WHITE, False)]
    if info.get("media_type") == "tv" and episodes:
        items.append((f"EP {episodes}", (40, 40, 48, 255), WHITE, False))
    x0 = (W - pills_total(d, items)) // 2
    draw_pills(d, x0, H - BOT + 40, items)
    d.text((W // 2, H - BOT + 190), "  •  ".join(genres), font=font(36, True),
           fill=(225, 175, 175, 255), anchor="mm")

    base.alpha_composite(ov)
    paste_logo(base, M + 48, TOP // 2, 96)
    return base


# ───────────────────────── Template 4: Glass Panel ─────────────────────────

def tpl_glass(info, hero, season, episodes):
    lang, rating_txt, season_txt, genres = info_bits(info, season)
    base = prep_art(hero)
    v_gradient(base, H - 700, 700, 0, 150)

    px, py, px2, py2 = M, H - 64 - 376, W - M, H - 64
    pw, ph = px2 - px, py2 - py
    region = base.crop((px, py, px2, py2)).filter(ImageFilter.GaussianBlur(28))
    region = ImageEnhance.Brightness(region).enhance(0.45)
    region.alpha_composite(Image.new("RGBA", (pw, ph), (255, 255, 255, 22)))
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw - 1, ph - 1), radius=40, fill=255)
    base.paste(region, (px, py), mask)

    d0 = ImageDraw.Draw(base)
    tf, lines = fit_title(d0, (info.get("name") or "ANIME").upper(), 1250, 2, 104, 52)
    line_h = int(tf.size * 1.2)
    total = line_h * len(lines) + 30 + 28
    t_top = py + (ph - total) // 2
    draw_title(base, lines, tf, px + 56, t_top, line_h)

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle((px, py, px2, py2), radius=40, outline=(255, 255, 255, 70), width=3)
    d.text((px + 56, t_top + line_h * len(lines) + 30 + 14), "  •  ".join(genres),
           font=font(28, True), fill=(230, 190, 190, 255), anchor="lm")

    # stats columns
    if info.get("media_type") == "movie":
        stats = [("TYPE", "MOVIE"), ("AUDIO", "TAMIL"), ("RATING", rating_txt)]
    else:
        stats = [("SEASON", str(season) if str(season) not in ("0", "") else "1"),
                 ("EPISODES", episodes or "-"), ("RATING", rating_txt)]
    sx, colw = 1490, 250
    for i, (label, val) in enumerate(stats):
        x0 = sx + i * colw
        if i:
            d.line((x0, py + 100, x0, py + 210), fill=(255, 255, 255, 60), width=3)
        tx = x0 + (24 if i else 0)
        d.text((tx, py + 110), label, font=font(24, True), fill=(190, 160, 160, 255), anchor="lm")
        if label == "RATING":
            d.polygon(star_points(tx + 17, py + 170, 19), fill=GOLD)
            tx += 46
        vf = fit_text(d, val, colw - 50, 46, 24)
        d.text((tx, py + 170), val, font=vf, fill=WHITE, anchor="lm")
    d.rounded_rectangle((sx, py + 246, sx + colw * 3 - 10, py + 310), radius=32, fill=ROSE)
    d.text((sx + (colw * 3 - 10) / 2, py + 279), "DOWNLOAD NOW", font=font(32, True), fill=WHITE, anchor="mm")

    # top row
    draw_pills(d, M, 64, [("OFFICIAL", ROSE, ROSE_TEXT, False), (lang, ROSE, ROSE_TEXT, False)])
    hf = font(34, True)
    handle = channel_handle()
    hw = int(d.textlength(handle, font=hf)) + 76
    d.rounded_rectangle((W - M - hw, 56, W - M, 128), radius=36, fill=WHITE)
    d.text((W - M - hw / 2, 92), handle, font=hf, fill=(20, 20, 26, 255), anchor="mm")

    base.alpha_composite(ov)
    paste_logo(base, px2 - 56 - 80, py + ph // 2, 160)
    return base


# ───────────────────────── Template 5: Diagonal Slash ─────────────────────────

def tpl_slash(info, hero, season, episodes):
    lang, rating_txt, season_txt, genres = info_bits(info, season)
    base = prep_art(hero)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.polygon([(0, 0), (1250, 0), (900, H), (0, H)], fill=(10, 10, 14, 238))
    d.line([(1250, 0), (900, H)], fill=ROSE, width=10)
    d.line([(1290, 0), (940, H)], fill=(255, 255, 255, 70), width=3)
    base.alpha_composite(ov)

    LM = 100
    d0 = ImageDraw.Draw(base)
    tf, lines = fit_title(d0, (info.get("name") or "ANIME").upper(), 880, 3, 120, 50)
    line_h = int(tf.size * 1.3)
    draw_title(base, lines, tf, LM, 640 - line_h * len(lines), line_h)

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    hf = font(32, True)
    handle = channel_handle()
    hw = int(d.textlength(handle, font=hf)) + 70
    d.rounded_rectangle((LM, 70, LM + hw, 142), radius=36, fill=WHITE)
    d.text((LM + hw / 2, 106), handle, font=hf, fill=(20, 20, 26, 255), anchor="mm")
    d.rounded_rectangle((LM, 672, LM + 128, 678), radius=3, fill=ROSE)
    draw_pills(d, LM, 712, [("OFFICIAL", ROSE, ROSE_TEXT, False), (lang, ROSE, ROSE_TEXT, False),
                            (rating_txt, DARK, WHITE, True), (season_txt, DARK, WHITE, False)],
               h=52, size=26, gap=20)
    d.text((LM, 830), "  •  ".join(genres), font=font(30, True), fill=(235, 190, 190, 255), anchor="lm")
    if info.get("media_type") == "movie":
        ep_line = "FULL MOVIE"
    elif str(season) not in ("0", ""):
        ep_line = f"SEASON {season}  •  EP {episodes}"
    else:
        ep_line = f"EP {episodes}"
    d.text((LM, 895), ep_line, font=font(40, True), fill=WHITE, anchor="lm")

    cy, dia = H - 130, 120
    bf = font(44, True)
    bx = LM + dia + 30
    bw = int(d.textlength("Download Now", font=bf)) + 80
    d.rounded_rectangle((bx, cy - 42, bx + bw, cy + 42), radius=42, fill=ROSE)
    d.text((bx + bw / 2, cy + 1), "Download Now", font=bf, fill=WHITE, anchor="mm")
    draw_dots(d, W - M - 162, 60, 10, 2, True)
    base.alpha_composite(ov)
    paste_logo(base, LM + dia // 2, cy, dia)
    return base


# ───────────────────────── Template 6: Neon Frame ─────────────────────────

def tpl_neon(info, hero, season, episodes):
    lang, rating_txt, season_txt, genres = info_bits(info, season)
    NEON, CYAN = (255, 80, 130, 255), (70, 220, 255, 255)
    DK = (10, 10, 16, 225)
    base = prep_art(hero, 1.3, 1.1)
    v_gradient(base, 0, H, 40, 110)
    v_gradient(base, H - 800, 800, 0, 205)

    def frame(dr):
        dr.rounded_rectangle((36, 36, W - 36, H - 36), radius=34, outline=NEON, width=12)
        L, ins = 140, 70
        for cx, cy, sx, sy in [(ins, ins, 1, 1), (W - ins, ins, -1, 1), (ins, H - ins, 1, -1), (W - ins, H - ins, -1, -1)]:
            dr.line([(cx + sx * L, cy), (cx, cy), (cx, cy + sy * L)], fill=CYAN, width=8, joint="curve")

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    frame(ImageDraw.Draw(glow))
    glow = glow.filter(ImageFilter.GaussianBlur(16))
    base.alpha_composite(glow)
    base.alpha_composite(glow)
    frame(ImageDraw.Draw(base))

    d0 = ImageDraw.Draw(base)
    tf, lines = fit_title(d0, (info.get("name") or "ANIME").upper(), 2000, 2, 150, 64)
    line_h = int(tf.size * 1.3)
    draw_title(base, lines, tf, W // 2, (H - 250) - line_h * len(lines), line_h, "m")

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    items = [("OFFICIAL", NEON, WHITE, False), (lang, NEON, WHITE, False), (rating_txt, DK, WHITE, True),
             (season_txt, DK, WHITE, False)]
    if info.get("media_type") == "tv" and episodes:
        items.append((f"EP {episodes}", DK, WHITE, False))
    draw_pills(d, (W - pills_total(d, items)) // 2, H - 215, items)
    d.text((W // 2, H - 105), "  •  ".join(genres), font=font(32, True), fill=(160, 235, 255, 255), anchor="mm")

    hf = font(34, True)
    handle = channel_handle()
    hw = int(d.textlength(handle, font=hf)) + 80
    d.rounded_rectangle((W // 2 - hw // 2, 78, W // 2 + hw // 2, 150), radius=36, fill=DK, outline=NEON, width=4)
    d.text((W // 2, 114), handle, font=hf, fill=WHITE, anchor="mm")
    df = font(32, True)
    dw = int(d.textlength("DOWNLOAD NOW", font=df)) + 70
    d.rounded_rectangle((W - 130 - dw, 78, W - 130, 150), radius=36, fill=DK, outline=CYAN, width=4)
    d.text((W - 130 - dw / 2, 114), "DOWNLOAD NOW", font=df, fill=CYAN, anchor="mm")
    base.alpha_composite(ov)
    paste_logo(base, 190, 114, 110)
    return base


# ───────────────────────── Template 7: Minimal Bar ─────────────────────────

def tpl_bar(info, hero, season, episodes):
    lang, rating_txt, season_txt, genres = info_bits(info, season)
    BAR = 200
    base = prep_art(hero)
    v_gradient(base, 0, 300, 110, 0)
    v_gradient(base, H - BAR - 300, 300, 0, 90)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle((0, H - BAR, W, H), fill=(246, 246, 248, 255))
    d.rectangle((0, H - BAR, W, H - BAR + 8), fill=ROSE)

    DK_TXT, GREY = (20, 20, 26, 255), (120, 120, 130, 255)
    tx = M + 130 + 40
    tf, lines = fit_title(d, (info.get("name") or "ANIME").upper(), 1150, 1, 84, 36)
    d.text((tx, H - 118), lines[0], font=tf, fill=DK_TXT, anchor="lm")
    d.text((tx, H - 48), "  •  ".join(genres), font=font(28, True), fill=ROSE, anchor="lm")

    if info.get("media_type") == "movie":
        stats = [("TYPE", "MOVIE"), ("AUDIO", "TAMIL"), ("RATING", rating_txt)]
    else:
        stats = [("SEASON", str(season) if str(season) not in ("0", "") else "1"),
                 ("EPISODES", episodes or "-"), ("RATING", rating_txt)]
    sx, colw = 1450, 230
    for i, (label, val) in enumerate(stats):
        x0 = sx + i * colw
        if i:
            d.line((x0 - 14, H - 150, x0 - 14, H - 50), fill=(200, 200, 205, 255), width=3)
        tx2 = x0
        d.text((tx2, H - 135), label, font=font(24, True), fill=GREY, anchor="lm")
        if label == "RATING":
            d.polygon(star_points(tx2 + 17, H - 80, 19), fill=GOLD)
            tx2 += 46
        d.text((tx2, H - 80), val, font=fit_text(d, val, colw - 50, 42, 22), fill=DK_TXT, anchor="lm")
    d.rounded_rectangle((W - M - 326, H - 140, W - M, H - 60), radius=40, fill=ROSE)
    d.text((W - M - 163, H - 99), "DOWNLOAD NOW", font=font(30, True), fill=WHITE, anchor="mm")

    draw_pills(d, M, 64, [("OFFICIAL", ROSE, ROSE_TEXT, False), (lang, ROSE, ROSE_TEXT, False)])
    hf = font(34, True)
    handle = channel_handle()
    hw = int(d.textlength(handle, font=hf)) + 76
    d.rounded_rectangle((W - M - hw, 56, W - M, 128), radius=36, fill=WHITE)
    d.text((W - M - hw / 2, 92), handle, font=hf, fill=DK_TXT, anchor="mm")
    base.alpha_composite(ov)
    paste_logo(base, M + 65, H - BAR // 2 + 4, 130)
    return base


# ───────────────────────── Template 8: Big Episode ─────────────────────────

def tpl_episode(info, hero, season, episodes):
    lang, rating_txt, season_txt, genres = info_bits(info, season)
    base = prep_art(hero, 1.1, 1.1)
    v_gradient(base, 0, H, 110, 190)
    v_gradient(base, H - 700, 700, 0, 160)
    if info.get("media_type") == "movie":
        big = "FILM"
    else:
        m = re.search(r"\d+", episodes or "")
        big = m.group(0) if m else str(season)

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    bf = font(820 if len(big) <= 3 else 560, True)
    d.text((W - 60, H // 2 - 10), big, font=bf, fill=(255, 255, 255, 18),
           stroke_width=5, stroke_fill=(255, 255, 255, 90), anchor="rm")
    base.alpha_composite(ov)

    genre_y, pills_y = H - 110, H - 210
    accent_y = pills_y - 32
    d0 = ImageDraw.Draw(base)
    tf, lines = fit_title(d0, (info.get("name") or "ANIME").upper(), 1400, 2, 130, 56)
    line_h = int(tf.size * 1.35)
    draw_title(base, lines, tf, M, accent_y - 34 - line_h * len(lines), line_h)

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle((M, accent_y, M + 128, accent_y + 6), radius=3, fill=ROSE)
    draw_pills(d, M, pills_y, [("OFFICIAL", ROSE, ROSE_TEXT, False), (lang, ROSE, ROSE_TEXT, False),
                               (rating_txt, DARK, WHITE, True), (season_txt, DARK, WHITE, False)])
    d.text((M, genre_y), "  •  ".join(genres), font=font(32, True), fill=(235, 190, 190, 255), anchor="lm")

    hf = font(34, True)
    handle = channel_handle()
    hw = int(d.textlength(handle, font=hf)) + 76
    d.rounded_rectangle((M, 64, M + hw, 136), radius=36, fill=WHITE)
    d.text((M + hw / 2, 100), handle, font=hf, fill=(20, 20, 26, 255), anchor="mm")
    ep_txt = "FULL MOVIE" if info.get("media_type") == "movie" else f"EPISODES  {episodes}"
    ef = font(40, True)
    ew = int(d.textlength(ep_txt, font=ef)) + 80
    d.rounded_rectangle((W - M - ew, 64, W - M, 136), radius=36, fill=ROSE)
    d.text((W - M - ew / 2, 100), ep_txt, font=ef, fill=WHITE, anchor="mm")

    dia = 150
    lcx, lcy = W - M - dia // 2, H - 64 - dia // 2
    d.text((lcx - dia // 2 - 30, lcy), "Download Now", font=font(54, True), fill=WHITE, anchor="rm")
    base.alpha_composite(ov)
    paste_logo(base, lcx, lcy, dia)
    return base


# ───────────────────────── Template registry ─────────────────────────
# To add a template: write tpl_xxx(info, hero, season, episodes) -> RGBA image, then add it here.

TEMPLATES = {
    "clean": ("Clean Cinematic", tpl_clean, "Artwork first, big title bottom-left, info pills + genre bar."),
    "split": ("Split Card", tpl_split, "Blurred backdrop with a portrait poster card on the right."),
    "center": ("Center Stage", tpl_center, "Letterbox cinema bars with a centered title."),
    "glass": ("Glass Panel", tpl_glass, "Frosted glass info panel with season / episode / rating stats."),
    "slash": ("Diagonal Slash", tpl_slash, "Dark diagonal info panel on the left, artwork open on the right."),
    "neon": ("Neon Frame", tpl_neon, "Glowing neon border with corner brackets and a centered title."),
    "bar": ("Minimal Bar", tpl_bar, "Clean white info bar under full artwork."),
    "episode": ("Big Episode", tpl_episode, "Giant outlined episode number behind the title."),
}


def make_poster(info: dict, hero: Image.Image, thumbs: list, season="1", episodes="1 - 12",
                template="clean", preview=False) -> io.BytesIO:
    fn = TEMPLATES.get(template, TEMPLATES["clean"])[1]
    return finish(fn(info, hero, season, episodes), preview)


def get_season_overview(info: dict, season_num: int) -> Optional[str]:
    if info.get("media_type") != "tv" or season_num <= 0:
        return None
    try:
        data = tmdb(f"/tv/{info['id']}/season/{season_num}", {"language": "en-US"})
        text = (data.get("overview") or "").strip()
        if text:
            return text
    except Exception as e:
        log.warning("Season %s details failed: %s", season_num, e)
    for sea in info.get("seasons", []) or []:
        if sea.get("season_number") == season_num:
            text = (sea.get("overview") or "").strip()
            if text:
                return text
    log.info("TMDB has no synopsis for %s season %s", info.get("name"), season_num)
    return None


# ───────────────────────── Gallery helpers ─────────────────────────

async def show_photo(context, chat_id, q, media, caption, kb):
    """Send a new photo message (q=None) or edit the current one in place (q=callback query).
    `media` is a URL or bytes; if Telegram can't fetch the URL we download it ourselves."""
    async def attempt(m):
        if q:
            await q.edit_message_media(InputMediaPhoto(m, caption=caption, parse_mode=ParseMode.HTML),
                                       reply_markup=kb)
        else:
            await context.bot.send_photo(chat_id, m, caption=caption, parse_mode=ParseMode.HTML,
                                         reply_markup=kb)
    try:
        await attempt(media)
    except Exception as e:
        if isinstance(media, str):
            log.info("URL media failed (%s); retrying with bytes", e)
            await attempt(await asyncio.to_thread(download_bytes, media))
        else:
            raise


def get_images(info):
    kind, mid = info["media_type"], info["id"]
    try:
        data = tmdb(f"/{kind}/{mid}/images", {"include_image_language": "en,null"})
        paths = [x["file_path"] for x in data.get("backdrops", [])[:8]]
        if not paths:
            paths = [x["file_path"] for x in data.get("posters", [])[:8]]
        return paths
    except Exception as e:
        log.warning("Could not get image list: %s", e)
        return [info.get("backdrop_path") or info.get("poster_path")]


def img_kb(idx, n):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀ Prev", callback_data="img:prev"),
         InlineKeyboardButton(f"{idx + 1}/{n}", callback_data="img:noop"),
         InlineKeyboardButton("Next ▶", callback_data="img:next")],
        [InlineKeyboardButton("✅ Use this image", callback_data="img:sel")],
        [InlineKeyboardButton("📤 Upload my own image", callback_data="img:upload")],
    ])


async def show_image(context, chat_id, idx, q=None):
    ud = context.user_data
    paths = ud["image_paths"]
    idx %= len(paths)
    ud["img_idx"] = idx
    name = html.escape(str(ud["info"].get("name", "")))
    caption = (f"🖼 <b>{name}</b>\nImage {idx + 1} of {len(paths)}\n\n"
               "Browse with ◀ ▶ then press <b>Use this image</b>.")
    await show_photo(context, chat_id, q, IMG + "w780" + paths[idx], caption, img_kb(idx, len(paths)))


def tpl_kb(cur):
    keys = list(TEMPLATES)
    rows = [[InlineKeyboardButton("◀ Prev", callback_data="tpl:prev"),
             InlineKeyboardButton(f"{keys.index(cur) + 1}/{len(keys)}", callback_data="tpl:noop"),
             InlineKeyboardButton("Next ▶", callback_data="tpl:next")]]
    btns = [InlineKeyboardButton(("▶ " if k == cur else "") + TEMPLATES[k][0], callback_data=f"tpl:go:{k}")
            for k in keys]
    rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton("✔ Use this template", callback_data="tpl:sel")])
    rows.append([InlineKeyboardButton("🖼 Change image", callback_data="tpl:back")])
    return InlineKeyboardMarkup(rows)


async def render_tpl_preview(ud, key) -> bytes:
    cache = ud.setdefault("tpl_cache", {})
    if key not in cache:
        buf = await asyncio.to_thread(make_poster, ud["info"], ud["hero"], [], "1", "1 - 12", key, True)
        cache[key] = buf.getvalue()
    return cache[key]


async def show_template(context, chat_id, key, q=None):
    ud = context.user_data
    ud["tpl_key"] = key
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    data = await render_tpl_preview(ud, key)
    name, desc = TEMPLATES[key][0], TEMPLATES[key][2]
    n = list(TEMPLATES).index(key) + 1
    caption = (f"🎨 <b>{html.escape(name)}</b> ({n}/{len(TEMPLATES)})\n{html.escape(desc)}\n\n"
               "Preview uses your chosen image. Tap a style, then <b>Use this template</b>.")
    await show_photo(context, chat_id, q, data, caption, tpl_kb(key))


# ───────────────────────── Bot handlers ─────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 *Anime Poster Bot*\n\nUse `/p Naruto Shippuden` to create a poster.\n"
        "Browse TMDB images with previews, pick one of 4 templates (with live preview), "
        "then enter the season and episode range.",
        parse_mode=ParseMode.MARKDOWN)


async def poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Send an anime name: `/p Naruto Shippuden`", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END
    try:
        data = await asyncio.to_thread(tmdb, "/search/multi", {"query": query, "include_adult": "false"})
        results = [x for x in data.get("results", []) if x.get("media_type") in ("tv", "movie")][:8]
        if not results:
            await update.message.reply_text("No matching title found. Try another spelling.")
            return ConversationHandler.END
        context.user_data["results"] = results
        rows = []
        for i, x in enumerate(results):
            name = x.get("name") or x.get("title") or "Untitled"
            year = (x.get("first_air_date") or x.get("release_date") or "")[:4]
            rows.append([InlineKeyboardButton(f"{name} {('(' + year + ')') if year else ''} · {x['media_type'].upper()}",
                                              callback_data=f"pick:{i}")])
        await update.message.reply_text("Choose the correct anime:", reply_markup=InlineKeyboardMarkup(rows))
        return SEARCH
    except Exception as e:
        log.exception("Search failed")
        await update.message.reply_text(f"TMDB search failed: {e}")
        return ConversationHandler.END


async def picked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ud = context.user_data
    chat_id = q.message.chat_id
    try:
        idx = int(q.data.split(":")[1])
        result = ud["results"][idx]
        kind = result["media_type"]
        info = await asyncio.to_thread(tmdb, f"/{kind}/{result['id']}", {"append_to_response": "credits"})
        info["media_type"] = kind
        info["name"] = info.get("name") or info.get("title")
        info["genres"] = ", ".join(g["name"] for g in info.get("genres", [])[:4]) or "ANIME"
        info["status_label"] = "ONGOING" if info.get("status") == "Returning Series" else (info.get("status") or "AVAILABLE").upper()
        ud["info"] = info
        paths = await asyncio.to_thread(get_images, info)
        ud["image_paths"] = [p for p in paths if p]
        ud["tpl_cache"] = {}
        try:
            await q.message.delete()
        except Exception:
            pass
        if not ud["image_paths"]:
            ud["awaiting_upload"] = True
            await context.bot.send_message(chat_id, "No TMDB images found. Send your own image as a photo or image file.")
            return IMAGE
        await show_image(context, chat_id, 0)
        return IMAGE
    except Exception as e:
        log.exception("Title selection failed")
        await context.bot.send_message(chat_id, f"Could not load title details: {e}")
        return ConversationHandler.END


async def image_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ud = context.user_data
    action = q.data.split(":")[1]
    chat_id = q.message.chat_id
    if "info" not in ud:
        await q.answer("Session expired — start again with /p", show_alert=True)
        return ConversationHandler.END
    paths = ud.get("image_paths", [])
    try:
        if action == "noop":
            await q.answer()
            return IMAGE
        if action in ("prev", "next"):
            await q.answer()
            if len(paths) > 1:
                step = 1 if action == "next" else -1
                await show_image(context, chat_id, ud.get("img_idx", 0) + step, q)
            return IMAGE
        if action == "upload":
            await q.answer()
            ud["awaiting_upload"] = True
            await q.edit_message_caption("📤 Send your image now (as a photo or image file). It becomes the main background.")
            return IMAGE
        if action == "sel":
            await q.answer("Downloading full-res image…")
            path = paths[ud.get("img_idx", 0)]
            hero = await asyncio.to_thread(fetch_image, path, "original")
            if hero is None:
                await context.bot.send_message(chat_id, "Could not download that image, try another one.")
                return IMAGE
            ud["hero"] = hero
            ud["tpl_cache"] = {}
            try:
                await q.message.delete()
            except Exception:
                pass
            await show_template(context, chat_id, ud.get("tpl_key", "clean"))
            return TEMPLATE
    except Exception as e:
        log.exception("Image step failed")
        await context.bot.send_message(chat_id, f"Something went wrong: {e}")
        return ConversationHandler.END
    return IMAGE


async def image_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    if not ud.get("awaiting_upload"):
        await update.message.reply_text("Use the image buttons first, or start again with /p.")
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
        ud["hero"] = Image.open(b).convert("RGB")
        ud["tpl_cache"] = {}
        ud["awaiting_upload"] = False
        await show_template(context, update.message.chat_id, ud.get("tpl_key", "clean"))
        return TEMPLATE
    except Exception as e:
        log.exception("Upload failed")
        await update.message.reply_text(f"Could not read that image: {e}")
        return IMAGE


async def template_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ud = context.user_data
    chat_id = q.message.chat_id
    parts = q.data.split(":")
    action = parts[1]
    if "hero" not in ud or "info" not in ud:
        await q.answer("Session expired — start again with /p", show_alert=True)
        return ConversationHandler.END
    keys = list(TEMPLATES)
    cur = ud.get("tpl_key", "clean")
    try:
        if action == "noop":
            await q.answer()
            return TEMPLATE
        if action == "back":
            await q.answer()
            try:
                await q.message.delete()
            except Exception:
                pass
            if ud.get("image_paths"):
                await show_image(context, chat_id, ud.get("img_idx", 0))
            else:
                ud["awaiting_upload"] = True
                await context.bot.send_message(chat_id, "Send a different image as a photo or image file.")
            return IMAGE
        if action == "sel":
            await q.answer("Template selected ✅")
            ud["template"] = cur
            await q.edit_message_caption(f"✅ Template: <b>{html.escape(TEMPLATES[cur][0])}</b>",
                                         parse_mode=ParseMode.HTML)
            await context.bot.send_message(
                chat_id, "Enter the season number (example: 18). Send 0 for a movie or no season.")
            return SEASON
        if action in ("prev", "next"):
            key = keys[(keys.index(cur) + (1 if action == "next" else -1)) % len(keys)]
        elif action == "go" and len(parts) > 2 and parts[2] in TEMPLATES:
            key = parts[2]
        else:
            await q.answer()
            return TEMPLATE
        if key == cur:
            await q.answer()
            return TEMPLATE
        await q.answer("Rendering preview…")
        await show_template(context, chat_id, key, q)
        return TEMPLATE
    except Exception as e:
        log.exception("Template step failed")
        await context.bot.send_message(chat_id, f"Something went wrong: {e}")
        return ConversationHandler.END


async def season_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    if not value.isdigit() or int(value) > 999:
        await update.message.reply_text("Please enter a number, for example 18. Use 0 for a movie.")
        return SEASON
    context.user_data["season"] = value
    await update.message.reply_text("Enter the episode range (example: 373 - 393). For a movie, send `Movie`.",
                                    parse_mode=ParseMode.MARKDOWN)
    return EPISODES


async def send_poster(update: Update, context: ContextTypes.DEFAULT_TYPE, overview: str):
    ud = context.user_data
    info = ud["info"]
    episodes = ud.get("episodes", "")
    await update.message.reply_text("Creating your poster…")
    try:
        poster_img = await asyncio.to_thread(make_poster, info, ud["hero"], [], ud.get("season", "1"),
                                             episodes, ud.get("template", "clean"), False)
        name = html.escape(str(info.get("name", "Anime")))
        season = int(ud.get("season", "1"))
        season_line = f"Season {season}" if season else "Movie"
        year = (info.get("first_air_date") or info.get("release_date") or "")[:4]
        genres = html.escape(str(info.get("genres", "Anime")))
        overview = (overview or "Synopsis not available.").strip()
        if len(overview) > 600:  # Telegram captions are limited to 1024 chars
            overview = overview[:600].rsplit(" ", 1)[0] + "…"
        overview = html.escape(overview)
        caption = (f"<b><b>🎌 {name}</b>\n"
                   f"<b>〄 Season :</b> {season_line}\n"
                   f"<b>〄 Episode :</b> {html.escape(episodes)}\n"
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
        for k in ("info", "hero", "image_paths", "img_idx", "season", "episodes", "results",
                  "awaiting_upload", "template", "tpl_key", "tpl_cache"):
            ud.pop(k, None)
    return ConversationHandler.END


async def episodes_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    episodes = update.message.text.strip()[:40]
    if not episodes:
        await update.message.reply_text("Please enter an episode range or Movie.")
        return EPISODES
    ud = context.user_data
    info = ud["info"]
    ud["episodes"] = episodes
    season = int(ud.get("season", "1"))
    series_overview = info.get("overview") or "Synopsis not available."

    if info.get("media_type") == "tv" and season > 0:
        season_overview = await asyncio.to_thread(get_season_overview, info, season)
        if season_overview:
            return await send_poster(update, context, season_overview)
        await update.message.reply_text(
            f"TMDB has no synopsis for Season {season}.\n\n"
            "Send the Season synopsis text now, or send `skip` to use the series synopsis.",
            parse_mode=ParseMode.MARKDOWN)
        return SYNOPSIS
    return await send_poster(update, context, series_overview)


async def synopsis_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Send the synopsis text, or `skip`.", parse_mode=ParseMode.MARKDOWN)
        return SYNOPSIS
    if text.lower() == "skip":
        text = context.user_data["info"].get("overview") or "Synopsis not available."
    return await send_poster(update, context, text)


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
            IMAGE: [CallbackQueryHandler(image_cb, pattern=r"^img:(prev|next|sel|upload|noop)$"),
                    MessageHandler(filters.PHOTO | filters.Document.IMAGE, image_upload)],
            TEMPLATE: [CallbackQueryHandler(template_cb, pattern=r"^tpl:")],
            SEASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, season_entered)],
            EPISODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, episodes_entered)],
            SYNOPSIS: [MessageHandler(filters.TEXT & ~filters.COMMAND, synopsis_entered)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(conv)
    start_health_server()
    log.info("Anime Poster Bot started")
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
