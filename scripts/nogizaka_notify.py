from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote_plus, urljoin

import feedparser
import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
FAVORITES_PATH = ROOT / "config" / "favorites.json"
SEEN_PATH = ROOT / "data" / "nogizaka_seen.json"

BLOG_URL = "https://www.nogizaka46.com/s/n46/diary/MEMBER/list"
NEWS_URL = "https://www.nogizaka46.com/s/n46/news/list"
YOUTUBE_FEEDS = [
    {
        "name": "乃木坂46公式YouTubeチャンネル",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCUzpZpX2wRYOk3J8QTFGxDg",
    },
    {
        "name": "乃木坂配信中",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCfvohDfHt1v5N8l3BzPRsWQ",
    },
]
ARTICLE_QUERIES = [
    "鈴木佑捺",
    "鈴木佑捺 乃木坂46",
    "鈴木佑捺 乃木坂46 6期生",
]


@dataclass(frozen=True)
class Item:
    source: str
    title: str
    url: str
    published: str = ""
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


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def fetch_html(url: str) -> BeautifulSoup:
    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0 nogizaka-discord-notifier"},
    )
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def classify_news(title: str) -> str:
    text = title.lower()
    if any(word in text for word in ["live", "ライブ", "公演"]):
        return "ライブ"
    if any(word in text for word in ["event", "イベント", "ミーグリ", "握手"]):
        return "イベント"
    if any(word in text for word in ["release", "発売", "リリース", "single", "album"]):
        return "リリース"
    if any(word in text for word in ["tv", "テレビ", "radio", "ラジオ", "配信", "出演"]):
        return "メディア"
    return "お知らせ"


def favorite_keywords(favorites: dict) -> list[str]:
    keywords: list[str] = []
    keywords.extend(favorites.get("members", []))
    keywords.extend(favorites.get("aliases", {}).keys())
    return [keyword for keyword in keywords if keyword]


def find_favorite_members(text: str, favorites: dict) -> list[str]:
    aliases = favorites.get("aliases", {})
    found: set[str] = set()

    for member in favorites.get("members", []):
        if member and member in text:
            found.add(member)

    for alias, member in aliases.items():
        if alias and alias in text:
            found.add(member)

    return sorted(found)


def unique_items(items: Iterable[Item]) -> list[Item]:
    seen: set[str] = set()
    result: list[Item] = []
    for item in items:
        if not item.url or item.url in seen:
            continue
        seen.add(item.url)
        result.append(item)
    return result


def fetch_blog_items() -> list[Item]:
    soup = fetch_html(BLOG_URL)
    items: list[Item] = []
    for link in soup.select('a[href*="/diary/detail/"]'):
        url = urljoin(BLOG_URL, link.get("href", ""))
        container = link.find_parent(["article", "li", "div"]) or link
        text = clean_text(container.get_text(" ", strip=True))
        title = clean_text(link.get_text(" ", strip=True)) or "乃木坂46公式ブログ更新"
        items.append(Item(source="公式ブログ", title=title, url=url, category="ブログ", summary=text[:700]))
    return unique_items(items)


def fetch_news_items() -> list[Item]:
    soup = fetch_html(NEWS_URL)
    items: list[Item] = []
    for link in soup.select('a[href*="/news/detail/"]'):
        url = urljoin(NEWS_URL, link.get("href", ""))
        container = link.find_parent(["article", "li", "div"]) or link
        text = clean_text(container.get_text(" ", strip=True))
        title = clean_text(link.get_text(" ", strip=True)) or text[:80]
        if not title:
            continue
        items.append(Item(source="公式お知らせ", title=title, url=url, category=classify_news(title), summary=text[:700]))
    return unique_items(items)


def fetch_youtube_items() -> list[Item]:
    items: list[Item] = []
    for feed_config in YOUTUBE_FEEDS:
        feed = feedparser.parse(feed_config["url"])
        for entry in feed.entries[:10]:
            title = clean_text(entry.get("title", ""))
            summary = clean_text(entry.get("summary", ""))
            items.append(
                Item(
                    source=feed_config["name"],
                    title=title,
                    url=entry.get("link", ""),
                    published=clean_text(entry.get("published", "")),
                    category="YouTube",
                    summary=summary[:700],
                )
            )
    return unique_items(items)


def google_news_feed_url(query: str) -> str:
    encoded = quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"


def fetch_article_items() -> list[Item]:
    items: list[Item] = []
    for query in ARTICLE_QUERIES:
        feed = feedparser.parse(google_news_feed_url(query))
        for entry in feed.entries[:10]:
            title = clean_text(entry.get("title", ""))
            summary = clean_text(entry.get("summary", ""))
            items.append(
                Item(
                    source="各種記事",
                    title=title,
                    url=entry.get("link", ""),
                    published=clean_text(entry.get("published", "")),
                    category="記事",
                    summary=summary[:700],
                )
            )
    return unique_items(items)


def webhook_url() -> str:
    return os.getenv("DISCORD_FAVORITE_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL", "")


def send_discord(item: Item, favorite_members: list[str]) -> None:
    url = webhook_url()
    if not url:
        print(f"Webhook is not configured. Skipped: {item.url}")
        return

    fields = [
        {"name": "種別", "value": item.source, "inline": True},
        {"name": "カテゴリ", "value": item.category or "-", "inline": True},
        {"name": "メンバー", "value": "、".join(favorite_members), "inline": False},
    ]
    if item.published:
        fields.append({"name": "投稿日/日時", "value": item.published, "inline": False})

    payload = {
        "username": "乃木坂46 推しメン通知",
        "embeds": [
            {
                "title": f"鈴木佑捺さん関連: {item.title}"[:256],
                "url": item.url,
                "description": item.summary[:900] if item.summary else item.url,
                "color": 0xF1C40F,
                "fields": fields,
                "footer": {"text": f"Checked at {datetime.now(timezone.utc).isoformat()}"},
            }
        ],
    }
    response = requests.post(url, json=payload, timeout=20)
    response.raise_for_status()


def send_log(message: str) -> None:
    url = os.getenv("DISCORD_LOG_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL", "")
    if not url:
        print(message)
        return
    requests.post(
        url,
        json={"username": "乃木坂46通知ログ", "content": message[:1900]},
        timeout=20,
    ).raise_for_status()


def collect_items() -> list[Item]:
    sources: list[tuple[str, Callable[[], list[Item]]]] = [
        ("公式ブログ", fetch_blog_items),
        ("公式お知らせ", fetch_news_items),
        ("YouTube", fetch_youtube_items),
        ("各種記事", fetch_article_items),
    ]

    items: list[Item] = []
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
    notify_on_first_run = os.getenv("NOTIFY_ON_FIRST_RUN", "false").lower() == "true"
    first_run = not seen_urls

    items = collect_items()
    new_items = [item for item in items if item.url not in seen_urls]

    sent_count = 0
    skipped_count = 0
    for item in new_items:
        text = f"{item.title}\n{item.category}\n{item.summary}"
        favorite_members = find_favorite_members(text, favorites)

        # 鈴木佑捺さんに関係ない新着は通知せず、既読扱いだけにします。
        if not favorite_members:
            seen_urls.add(item.url)
            skipped_count += 1
            continue

        # 初回だけは大量通知を避ける設定にしています。
        if first_run and not notify_on_first_run:
            seen_urls.add(item.url)
            skipped_count += 1
            continue

        send_discord(item, favorite_members)
        seen_urls.add(item.url)
        sent_count += 1

    seen_data["seen_urls"] = sorted(seen_urls)
    seen_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_json(SEEN_PATH, seen_data)

    message = f"乃木坂46自動通知: 送信 {sent_count}件 / 通知なし既読 {skipped_count}件"
    print(message)
    if first_run and not notify_on_first_run:
        send_log(message + "\n初回実行のため、鈴木佑捺さん関連も通知せず既読登録しました。")


if __name__ == "__main__":
    main()
