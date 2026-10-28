# AnimeNetworkTamil Telegram Poster Bot

Non-AI Telegram bot: searches TMDB and builds 16:9 (1920×1080) PNG posters using existing artwork, synopsis, year, rating, genres, the included channel logo, and an AnimeNetworkTamil watermark.

## Setup
1. In Telegram, open @BotFather, send `/newbot`, follow the prompts, and copy the bot token.
2. Create/log in to TMDB and follow https://developer.themoviedb.org/docs/getting-started to get an API Read Access Token or API key.
3. Install Python 3.11+ and extract this ZIP.
4. Copy `.env.example` to `.env`, then add your Telegram token and TMDB credential. Never share or publish `.env`.
5. In a terminal in this folder, run:
   `python -m venv .venv`
6. Activate the environment:
   - Windows: `.venv\\Scripts\\activate`
   - macOS/Linux: `source .venv/bin/activate`
7. Run `pip install -r requirements.txt`
8. Run `python bot.py` and keep the process running.

## Commands
- `/start`
- `/poster Solo Leveling` — choose a matching result to make a poster
- `/search Naruto`
- `/synopsis Naruto`
- `/help`

For 24/7 operation, deploy as a persistent Python worker and add the environment variables in the hosting dashboard. This starter does not include IMDb integration. Review TMDB's current terms and image/artwork rights before distributing posters publicly or commercially. Existing artwork only; no AI image generation.
