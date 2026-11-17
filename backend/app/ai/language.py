"""Language support: English, Urdu (script) and Roman Urdu (Urdu written with Latin letters).

Detection is plain rules (no AI): fast, free, testable, and it works even when Gemini is down.
Fixed replies (handoff, STOP/START, safe price) exist in all three languages.
"""
import re
from typing import Literal

Lang = Literal["en", "ur", "roman_ur"]
LANG_NAMES = {"en": "English", "ur": "Urdu (Urdu script)", "roman_ur": "Roman Urdu (Urdu in Latin letters)"}

# Arabic-script letters used by Urdu (Arabic, Arabic Supplement, presentation forms)
_URDU_CHAR = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_LATIN_WORD = re.compile(r"[a-z']+")

# Common Roman Urdu words that are NOT normal English words (so "main", "to", "is" are excluded).
ROMAN_URDU_WORDS = {
    "kya", "kia", "hai", "hain", "hy", "kitne", "kitna", "kitni", "mujhe", "mujhay", "muje", "chahiye", "chahye",
    "chahte", "aap", "ap", "apka", "apki", "apke", "aapka", "aapki", "aapke", "kaise", "kese", "kesay", "kab",
    "kahan", "nahi", "nahin", "nai", "haan", "han", "jee", "shukriya", "salam", "assalam", "walaikum", "karna",
    "karni", "karein", "karen", "karo", "krna", "baat", "saath", "sath", "abhi", "aaj", "wala", "wali", "kaam",
    "paise", "rupay", "rupaye", "hazar", "hazaar", "lakh", "theek", "thik", "acha", "accha", "achha", "bohat",
    "bahut", "zaroor", "zarur", "bata", "batao", "batayein", "hum", "humein", "mera", "meri", "mere", "tum",
    "yeh", "ye", "woh", "wo", "kyun", "kyunke", "lekin", "aur", "bhi", "sirf", "ki", "ka", "ke", "se", "ko",
    # greetings & common phrases, in their many spellings (found missing by a test)
    "asalam", "salaam", "alaikum", "alaikom", "aleikum", "alaykum", "aoa", "slm", "jazakallah", "inshallah",
    "mashallah", "khuda", "hafiz", "allah",
}
# Stop words removed before FAQ search (PostgreSQL has no Urdu dictionary to ignore them).
STOPWORDS = {
    "ur": {"کی", "کا", "کے", "ہے", "ہیں", "کیا", "میں", "سے", "کو", "اور", "آپ", "ہم", "یہ", "وہ", "بھی", "تو",
           "ہو", "پر", "نے", "کر", "مجھے", "ہمیں", "جی"},
    "roman_ur": {"ki", "ka", "ke", "hai", "hain", "kya", "kia", "main", "mein", "se", "ko", "aur", "aap", "ap",
                 "hum", "ye", "yeh", "wo", "woh", "bhi", "to", "ho", "par", "ne", "kar", "mujhe", "jee", "ji"},
}

# "I want a person" in all three languages, ALWAYS active (in addition to the configurable keywords).
BUILTIN_HANDOFF_PHRASES = [
    "insaan se baat", "insan se baat", "kisi se baat", "banda se baat", "bande se baat", "agent se baat",
    "human se baat", "representative se baat", "call karo", "call karein", "call kar do",
    "انسان سے بات", "کسی سے بات", "نمائندے سے بات", "ایجنٹ سے بات", "کال کریں",
]


def detect_language(text: str | None) -> tuple[Lang, bool]:
    """Return (language, confident). Short texts like "STOP", "ok", "?" are not confident."""
    text = text or ""
    letters = sum(ch.isalpha() for ch in text)
    urdu = len(_URDU_CHAR.findall(text))
    if urdu >= 3 and urdu >= 0.3 * max(letters, 1):
        return "ur", True
    words = _LATIN_WORD.findall(text.lower())
    if not words:
        return "en", False
    hits = sum(w in ROMAN_URDU_WORDS for w in words)
    # Two Roman Urdu words, or one in a very short message ("shukriya", "salam")
    if hits >= 2 or (hits == 1 and len(words) <= 2 and words[0] not in {"ki", "ka", "ke", "se", "ko", "ye", "wo"}):
        return "roman_ur", True
    return "en", len(words) >= 3


def search_text(text: str, lang: Lang) -> str:
    """Drop language stop words so FAQ search ranks on the meaningful words."""
    stop = STOPWORDS.get(lang)
    if not stop:
        return text
    words = re.findall(r"\w+", text.lower())
    kept = [w for w in words if w not in stop]
    return " ".join(kept) or text


# ---------------------------------------------------------------- fixed replies
_REPLIES: dict[str, dict[str, str]] = {
    "handoff": {
        "en": "Thanks for your patience! I'm connecting you with a member of the {name} team. "
              "They'll reply here as soon as possible{hours}.",
        "ur": "آپ کے انتظار کا شکریہ! آپ کو {name} کی ٹیم کے ایک رکن سے جوڑا جا رہا ہے۔ "
              "وہ جلد از جلد یہیں جواب دیں گے{hours}۔",
        "roman_ur": "Intezar ka shukriya! Aap ki baat {name} team ke ek member se karwai ja rahi hai. "
                    "Woh jald az jald yahin jawab denge{hours}.",
    },
    "hours": {"en": " (our hours: {h})", "ur": " (اوقاتِ کار: {h})", "roman_ur": " (hamare auqat: {h})"},
    "opt_out": {
        "en": "You have been unsubscribed from {name} marketing messages. You can still message us any time. "
              "Reply START to subscribe again.",
        "ur": "آپ کو {name} کے مارکیٹنگ پیغامات سے ہٹا دیا گیا ہے۔ آپ پھر بھی کسی وقت ہمیں پیغام بھیج سکتے ہیں۔ "
              "دوبارہ شامل ہونے کے لیے START لکھیں۔",
        "roman_ur": "Aap ko {name} ke marketing messages se hata diya gaya hai. Aap phir bhi kabhi bhi hamein "
                    "message kar sakte hain. Dobara shamil hone ke liye START likhein.",
    },
    "opt_in": {
        "en": "Thanks! You're subscribed to updates from {name}. Reply STOP any time to opt out.",
        "ur": "شکریہ! آپ {name} کی اپڈیٹس کے لیے شامل ہو گئے ہیں۔ کسی بھی وقت STOP لکھ کر بند کر سکتے ہیں۔",
        "roman_ur": "Shukriya! Aap {name} ki updates ke liye shamil ho gaye hain. Kabhi bhi STOP likh kar band "
                    "kar sakte hain.",
    },
    "safe_price": {
        "en": "Thanks for asking! Our pricing depends on your exact requirements. "
              "A team member will confirm an accurate quote for you shortly.",
        "ur": "پوچھنے کا شکریہ! ہماری قیمت آپ کی ضروریات پر منحصر ہے۔ ٹیم کا ایک رکن جلد آپ کو درست قیمت بتائے گا۔",
        "roman_ur": "Poochne ka shukriya! Hamari qeemat aap ki zaroorat par depend karti hai. Team ka ek member "
                    "jald aap ko sahi quote bata dega.",
    },
}


def reply(key: str, lang: Lang, **values: str) -> str:
    template = _REPLIES[key].get(lang) or _REPLIES[key]["en"]
    return template.format(**values)


def handoff_reply(lang: Lang, name: str, hours: str | None) -> str:
    h = reply("hours", lang, h=hours) if hours else ""
    return reply("handoff", lang, name=name, hours=h)
