import re

EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"
    "\U00002600-\U000027bf"
    "\U00002190-\U000021ff"
    "\U00002300-\U000023ff"
    "\U00002460-\U000024ff"
    "\U00002b00-\U00002bff"
    "\U0001f1e6-\U0001f1ff"
    "\U0000fe00-\U0000fe0f"
    "\U0000200d"
    "\U00002122\U00002139\U000024c2"
    "\U00003297\U00003299"
    "\U0001f3f4"
    "\U000e0000-\U000e007f"
    "]+"
)

MENTION = re.compile(r"(?<!\w)@\w+")
TME_LINK = re.compile(r"(https?://)?t\.me/\S+", re.I)
DANGLING = re.compile(r"[ \t]*\b(via|by|source|cc)\b[ \t:|-]*$", re.I)
PROMO_LINE = re.compile(r"^(subscribe|join|follow|source|via|read more|full story)\b.*$", re.I)
SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?])")
MULTISPACE = re.compile(r"[ \t]{2,}")
MULTILINE = re.compile(r"\n{3,}")

TERMS = {
    "bitcoin": "Bitcoin",
    "btc": "BTC",
    "ethereum": "Ethereum",
    "eth": "ETH",
    "solana": "Solana",
    "sol": "SOL",
    "xrp": "XRP",
    "ripple": "Ripple",
    "binance": "Binance",
    "bnb": "BNB",
    "dogecoin": "Dogecoin",
    "doge": "DOGE",
    "cardano": "Cardano",
    "ada": "ADA",
    "tron": "TRON",
    "litecoin": "Litecoin",
    "chainlink": "Chainlink",
    "polygon": "Polygon",
    "avalanche": "AVAX",
    "tether": "Tether",
    "usdt": "USDT",
    "usdc": "USDC",
    "stablecoin": "Stablecoin",
    "crypto": "Crypto",
    "cryptocurrency": "Crypto",
    "altcoin": "Altcoin",
    "memecoin": "Memecoin",
    "defi": "DeFi",
    "nft": "NFT",
    "etf": "ETF",
    "sec": "SEC",
    "fed": "Fed",
    "blackrock": "BlackRock",
    "coinbase": "Coinbase",
    "michael saylor": "Saylor",
    "microstrategy": "MicroStrategy",
    "trump": "Trump",
    "powell": "Powell",
    "elon musk": "Elon",
    "united states": "USA",
    "u.s.": "USA",
    "us": "USA",
    "united kingdom": "UK",
    "russia": "Russia",
    "ukraine": "Ukraine",
    "china": "China",
    "iran": "Iran",
    "israel": "Israel",
    "pakistan": "Pakistan",
    "india": "India",
    "switzerland": "Switzerland",
    "germany": "Germany",
    "france": "France",
    "japan": "Japan",
    "south korea": "SouthKorea",
    "north korea": "NorthKorea",
    "saudi arabia": "SaudiArabia",
    "turkey": "Turkey",
    "argentina": "Argentina",
    "venezuela": "Venezuela",
    "nigeria": "Nigeria",
    "el salvador": "ElSalvador",
}

_keys = sorted(TERMS, key=len, reverse=True)
_term_re = re.compile(
    r"(?<![#\w])(" + "|".join(re.escape(k) for k in _keys) + r")(?!\w)",
    re.I,
)


def _hashtags(text, limit):
    seen = set()
    used = 0

    def repl(match):
        nonlocal used
        canon = TERMS[match.group(1).lower()]
        low = canon.lower()
        if low in seen or used >= limit:
            return match.group(0)
        seen.add(low)
        used += 1
        return "#" + canon

    return _term_re.sub(repl, text)


def clean(text):
    text = MENTION.sub("", text)
    text = TME_LINK.sub("", text)
    text = EMOJI.sub("", text)
    text = SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = MULTISPACE.sub(" ", text)
    lines = []
    for line in text.splitlines():
        line = DANGLING.sub("", line).strip()
        if not line or PROMO_LINE.match(line):
            if not line:
                lines.append("")
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = MULTILINE.sub("\n\n", text)
    return text.strip()


def process(text, max_hashtags=2):
    text = clean(text)
    if not text:
        return ""
    return _hashtags(text, max_hashtags).strip()
