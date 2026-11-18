# --- SLATEWIRE configuration ---
#
# 1) Get a free API key at https://aistudio.google.com/apikey
#    Sign in with a Google account -- no credit card needed. This is
#    Google's free tier for the Gemini API: generous enough (roughly
#    1,500 requests/day on Flash models) that a personal news app like
#    this one will never come close to hitting the limit.
#
#    Note: on the free tier, Google's terms allow using your prompts to
#    help improve their models. Nothing sensitive is sent here (just
#    article titles/snippets), but worth knowing.
#
# 2) Paste it below between the quotes. Do NOT commit this file with a real
#    key in it to a public GitHub repo -- see the note in README.md. Better
#    yet, set it via the SLATEWIRE_GEMINI_API_KEY environment variable
#    instead of editing this file, and leave the placeholder below as-is.

import os

# App version shown in the About popup. Change it here only.
VERSION = "v1.13.0"

GEMINI_API_KEY = os.environ.get("SLATEWIRE_GEMINI_API_KEY", "paste-your-key-here")

# Gemini model used for briefs and digests. "flash-lite-latest" is Google's floating
# free-tier alias: the full "gemini-flash-latest" model is often capacity-limited on the
# free tier (HTTP 503 / no answer before the timeout), while Lite answers a 6-15 line
# brief in a couple of seconds. If you have paid quota and want the larger model, set
# BRIEF_MODEL back to "gemini-flash-latest" -- the fallback below still catches busy days.
BRIEF_MODEL = "gemini-flash-lite-latest"
# Tried automatically when the model above is busy or times out.
BRIEF_MODEL_FALLBACK = "gemini-3.5-flash-lite"

# How many articles to keep per category per fetch.
MAX_PER_CATEGORY = 15

# Local port the desktop window's backend runs on.
PORT = 5055
