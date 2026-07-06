from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
FAVORITES_PATH = ROOT / "config" / "favorites.json"
SEEN_PATH = ROOT / "data" / "nogizaka_seen.json"

BLOG_URL = "https://www.nogizaka46.com/s/n46/diary/MEMBER/list"
NEWS_URL = "https://www.nogizaka46.com/s/n46/news/list"
SCHEDULE_URL = "https://www.nogizaka46.com/s/n46/media/list"
YOUTUBE_FEEDS = [
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCUzpZpX2wRYOk3J8QTFGxDg",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCfvohDfHt1v5N8l3BzPRsWQ",
]


@dataclass(frozen=True)
class Item:
    source: str
    title: str
    url: str
    published: str = ""
    member: str = ""
    category: str = ""
    summary: str = ""


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch_html(url: str) -> BeautifulSoup:
    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0 nogizaka-discord-notifier"},
    )
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def classify_news(title: str) -> str:
    text = title.lower()
    if any(word in text for word in ["live", "ライブ", "公演"]):
        return "ライブ"
    if any(word in text for word in ["event", "イベント", "握手", "ミーグリ"]):
        return "イベント"
    if any(word in text for word in ["release", "発売", "リリース", "single", "album"]):
        return "リリース"
    if any(word in text for word in ["tv", "テレビ", "radio", "ラジオ", "配信", "出演"]):
        return "メディア"
    return "その他"


def find_members(text: str, favorites: dict) -> list[str]:
    members = favorites.get("members", [])
    aliases = favorites.get("aliases", {})
    found: set[str] = set()
    for member in members:
        if member and member in text:
            found.add(member)
    for alias, member in aliases.items():
        if alias and alias in text:
            found.add(member)
    return sorted(found)


def fetch_blog_items() -> list[Item]:
    soup = fetch_html(BLOG_URL)
    items: list[Item] = []
    for link in soup.select('a[href*="/diary/detail/"]'):
        url = urljoin(BLOG_URL, link.get("href", ""))
        title = clean_text(link.get_text(" ", strip=True))
        if not title:
            title = "乃木坂46公式ブログ更新"
        container = link.find_parent(["article", "li", "div"]) or link
        text = clean_text(container.get_text(" ", strip=True))
        items.append(Item(source="ブログ", title=title, url=url, summary=text[:500]))
    return unique_items(items)


def fetch_news_items() -> list[Item]:
    soup = fetch_html(NEWS_URL)
    items: list[Item] = []
    for link in soup.select('a[href*="/news/detail/"]'):
        url = urljoin(NEWS_URL, link.get("href", ""))
        title = clean_text(link.get_text(" ", strip=True))
        if not title:
            continue
        category = classify_news(title)
        container = link.find_parent(["article", "li", "div"]) or link
        text = clean_text(container.get_text(" ", strip=True))
        items.append(Item(source="ニュース", title=title, url=url, category=category, summary=text[:500]))
    return unique_items(items)


def fetch_youtube_items() -> list[Item]:
    items: list[Item] = []
    for feed_url in YOUTUBE_FEEDS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:10]:
            items.append(
                Item(
                    source="YouTube",
                    title=clean_text(entry.get("title", "")),
                    url=entry.get("link", ""),
                    published=clean_text(entry.get("published", "")),
                    summary=clean_text(entry.get("summary", ""))[:500],
                )
            )
    return unique_items(items)


def fetch_schedule_items() -> list[Item]:
    # TODO: 公式サイトのHTML構造が変わると取得できないことがあります。
    # その場合は SCHEDULE_URL のリンク条件やセレクタを調整してください。
    soup = fetch_html(SCHEDULE_URL)
    items: list[Item] = []
    for link in soup.select('a[href*="/media/"], a[href*="/schedule/"]'):
        url = urljoin(SCHEDULE_URL, link.get("href", ""))
        title = clean_text(link.get_text(" ", strip=True))
        if not title or url == SCHEDULE_URL:
            continue
        container = link.find_parent(["article", "li", "div"]) or link
        text = clean_text(container.get_text(" ", strip=True))
        items.append(Item(source="スケジュール", title=title, url=url, category=classify_news(title), summary=text[:500]))
    return unique_items(items)


def unique_items(items: Iterable[Item]) -> list[Item]:
    seen: set[str] = set()
    result: list[Item] = []
    for item in items:
        if not item.url or item.url in seen:
            continue
        seen.add(item.url)
        result.append(item)
    return result


def webhook_for(item: Item, favorite_members: list[str]) -> str:
    if favorite_members:
        return os.getenv("DISCORD_FAVORITE_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL", "")
    source_key = {
        "ブログ": "DISCORD_BLOG_WEBHOOK_URL",
        "ニュース": "DISCORD_NEWS_WEBHOOK_URL",
        "YouTube": "DISCORD_YOUTUBE_WEBHOOK_URL",
        "スケジュール": "DISCORD_SCHEDULE_WEBHOOK_URL",
    }.get(item.source, "DISCORD_WEBHOOK_URL")
    return os.getenv(source_key) or os.getenv("DISCORD_WEBHOOK_URL", "")


def send_discord(item: Item, favorite_members: list[str]) -> None:
    webhook_url = webhook_for(item, favorite_members)
    if not webhook_url:
        print(f"Webhook is not configured. Skipped: {item.url}")
        return

    title_prefix = "推しメン情報" if favorite_members else "乃木坂46情報"
    color = 0xF1C40F if favorite_members else 0x3498DB
    fields = [
        {"name": "種別", "value": item.source, "inline": True},
        {"name": "カテゴリ", "value": item.category or "-", "inline": True},
    ]
    if favorite_members:
        fields.append({"name": "推しメン", "value": "、".join(favorite_members), "inline": False})
    if item.published:
        fields.append({"name": "投稿日/日時", "value": item.published, "inline": False})

    payload = {
        "username": "乃木坂46通知",
        "embeds": [
            {
                "title": f"{title_prefix}: {item.title}"[:256],
                "url": item.url,
                "description": item.summary[:800] if item.summary else item.url,
                "color": color,
                "fields": fields,
                "footer": {"text": f"Checked at {datetime.now(timezone.utc).isoformat()}"},
            }
        ],
    }
    response = requests.post(webhook_url, json=payload, timeout=20)
    response.raise_for_status()


def send_log(message: str) -> None:
    webhook_url = os.getenv("DISCORD_LOG_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print(message)
        return
    requests.post(
        webhook_url,
        json={"username": "乃木坂46通知ログ", "content": message[:1900]},
        timeout=20,
    ).raise_for_status()


def collect_items() -> list[Item]:
    items: list[Item] = []
    sources = [
        ("ブログ", fetch_blog_items),
        ("ニュース", fetch_news_items),
        ("YouTube", fetch_youtube_items),
        ("スケジュール", fetch_schedule_items),
    ]
    for source_name, fetcher in sources:
        try:
            fetched = fetcher()
            print(f"{source_name}: {len(fetched)} items")
            items.extend(fetched)
        except Exception as exc:
            send_log(f"取得失敗: {source_name}\n{type(exc).__name__}: {exc}")
    return items


def main() -> None:
    favorites = load_json(FAVORITES_PATH, {"members": [], "aliases": {}})
    seen_data = load_json(SEEN_PATH, {"seen_urls": []})
    seen_urls = set(seen_data.get("seen_urls", []))
    first_run = not seen_urls
    notify_on_first_run = os.getenv("NOTIFY_ON_FIRST_RUN", "false").lower() == "true"

    items = collect_items()
    new_items = [item for item in items if item.url not in seen_urls]

    sent_count = 0
    for item in new_items:
        text = f"{item.title}\n{item.member}\n{item.category}\n{item.summary}"
        favorite_members = find_members(text, favorites)
        if first_run and not notify_on_first_run:
            seen_urls.add(item.url)
            continue
        send_discord(item, favorite_members)
        seen_urls.add(item.url)
        sent_count += 1

    seen_data["seen_urls"] = sorted(seen_urls)
    seen_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_json(SEEN_PATH, seen_data)

    if first_run and not notify_on_first_run:
        send_log(f"乃木坂46自動通知: 初回実行のため、{len(new_items)}件を通知済みとして登録しました。次回から新着だけ通知します。")
    else:
        print(f"Sent {sent_count} notifications.")


if __name__ == "__main__":
    main()
