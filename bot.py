"""
Pokémon news bot: checks Serebii.net's front page for news about the games
you follow and posts new items to a Discord channel through a webhook.
"""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SEREBII_URL = "https://www.serebii.net/"
SEEN_FILE = Path(__file__).with_name("seen.json")
MAX_POSTS_PER_RUN = 5  # stops a flood of messages if a lot is new at once

# Games to follow. Add more by copying a block: a name, a Discord embed
# colour, and a pattern that matches how Serebii writes the game's name.
GAMES = [
    {
        "name": "Pokémon Champions",
        "color": 0xE3350D,
        # "\b" stops it matching "Championships"
        "pattern": re.compile(r"\bpok[eé]mon champions\b", re.IGNORECASE),
    },
    {
        "name": "Pokémon Winds & Waves",
        "color": 0x2A9DF4,
        "pattern": re.compile(r"\bwinds\s*(?:&|and)\s*waves\b", re.IGNORECASE),
    },
]


def fetch_news():
    """Return a list of news items from Serebii's front page."""
    response = requests.get(
        SEREBII_URL,
        headers={"User-Agent": "Mozilla/5.0 (pokemon-news-bot; personal Discord feed)"},
        timeout=30,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")

    items = []
    for post in soup.select("div.post"):
        day_link = post.select_one("h2 a")
        day_url = SEREBII_URL.rstrip("/") + day_link["href"] if day_link and day_link.get("href") else SEREBII_URL
        day_id = day_link.get("id", "") if day_link else ""
        date_el = post.select_one("p.info span.date")
        date_text = date_el.get_text(" ", strip=True) if date_el else ""

        # Each news item starts with <p class="title">Game name</p> and runs
        # until the next title.
        for title_el in post.select("p.title"):
            title = title_el.get_text(" ", strip=True)
            parts, image = [], None
            sibling = title_el.find_next_sibling()
            while sibling is not None and "title" not in (sibling.get("class") or []):
                text = sibling.get_text(" ", strip=True)
                if text:
                    parts.append(text)
                img = sibling.find("img") if image is None else None
                if img and img.get("src"):
                    image = requests.compat.urljoin(SEREBII_URL, img["src"])
                sibling = sibling.find_next_sibling()

            body = "\n\n".join(parts)
            items.append(
                {
                    "title": title,
                    "body": body,
                    "url": day_url,
                    "date": date_text,
                    "image": image,
                    "id": make_id(day_id, title, body),
                }
            )
    return items


def make_id(day_id, title, body):
    # Serebii edits posts during the day, so only the start of the text is
    # used to recognise an item we've already posted.
    key = f"{day_id}|{title}|{body[:120]}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# Headings too general to say which game an item is about. For these the
# news text is checked instead.
GENERIC_TITLES = {"pokémon", "pokemon", "the pokémon company", "nintendo", "nintendo direct", "pokémon presents"}


def match_game(item):
    """Return the game an item is about, or None.

    Items are matched by their heading (e.g. "Pokémon Champions - Regulation M-D"),
    so a Pokémon GO item that only mentions Champions in passing is skipped.
    """
    for game in GAMES:
        if game["pattern"].search(item["title"]):
            return game
    if item["title"].strip().lower() in GENERIC_TITLES:
        for game in GAMES:
            if game["pattern"].search(item["body"]):
                return game
    return None


def post_to_discord(webhook_url, item, game):
    description = item["body"]
    if len(description) > 1500:
        description = description[:1500].rsplit(" ", 1)[0] + "…"
    # Some items only have a generic heading like "Pokémon"; use the game name then.
    title = item["title"] if game["pattern"].search(item["title"]) else game["name"]
    embed = {
        "title": title[:256],
        "url": item["url"],
        "description": description,
        "color": game["color"],
        "author": {"name": game["name"]},
        "footer": {"text": f"Serebii.net • {item['date']}" if item["date"] else "Serebii.net"},
    }
    if item["image"]:
        embed["thumbnail"] = {"url": item["image"]}

    response = requests.post(
        webhook_url,
        json={"username": "Pokémon News", "embeds": [embed]},
        timeout=30,
    )
    if response.status_code == 429:  # rate limited: wait and try once more
        time.sleep(float(response.json().get("retry_after", 2)))
        response = requests.post(webhook_url, json={"username": "Pokémon News", "embeds": [embed]}, timeout=30)
    response.raise_for_status()


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text())
    return []


def save_seen(seen):
    # Keep the list from growing forever.
    SEEN_FILE.write_text(json.dumps(seen[-500:], indent=0) + "\n")


def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        sys.exit("DISCORD_WEBHOOK_URL is not set. Add it as a repository secret.")

    seen = load_seen()
    seen_set = set(seen)

    matches = []
    for item in fetch_news():
        game = match_game(item)
        if game and item["id"] not in seen_set:
            matches.append((item, game))

    # The front page lists newest first; post oldest first so the channel reads in order.
    matches = matches[:MAX_POSTS_PER_RUN][::-1]
    print(f"Found {len(matches)} new item(s).")

    for item, game in matches:
        post_to_discord(webhook_url, item, game)
        print(f"Posted: {item['title']}")
        seen.append(item["id"])
        save_seen(seen)  # save after each post so a failure can't cause repeats
        time.sleep(1)

    save_seen(seen)


if __name__ == "__main__":
    main()
