from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote_plus, urljoin
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
FAVORITES_PATH = ROOT / "config" / "favorites.json"
SEEN_PATH = ROOT / "data" / "nogizaka_seen.json"
JST = ZoneInfo("Asia/Tokyo")

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
ARTICLE_RECENT_DAYS = int(os.getenv("ARTICLE_RECENT_DAYS", "30"))


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


def clean_html(value: str) -> str:
    if not value:
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return clean_text(text)


def now_jst_text() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")


def published_to_jst(value: str) -> str:
    dt = parse_datetime(value)
    if not dt:
        return value or ""
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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
        items.append(Item(source="公式ブログ", title=title, url=url, category="ブログ", summary=text[:500]))
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
        items.append(Item(source="公式お知らせ", title=title, url=url, category=classify_news(title), summary=text[:500]))
    return unique_items(items)


def fetch_youtube_items() -> list[Item]:
    items: list[Item] = []
    for feed_config in YOUTUBE_FEEDS:
        feed = feedparser.parse(feed_config["url"])
        for entry in feed.entries[:10]:
            title = clean_text(entry.get("title", ""))
            summary = clean_html(entry.get("summary", ""))
            items.append(
                Item(
                    source=feed_config["name"],
                    title=title,
                    url=entry.get("link", ""),
                    published=published_to_jst(clean_text(entry.get("published", ""))),
                    category="YouTube",
                    summary=summary[:500],
                )
            )
    return unique_items(items)


def google_news_feed_url(query: str) -> str:
    encoded = quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"


def fetch_article_items() -> list[Item]:
    items: list[Item] = []
    recent_threshold = datetime.now(JST) - timedelta(days=ARTICLE_RECENT_DAYS)
    for query in ARTICLE_QUERIES:
        feed = feedparser.parse(google_news_feed_url(query))
        for entry in feed.entries[:10]:
            published_raw = clean_text(entry.get("published", ""))
            published_dt = parse_datetime(published_raw)
            if published_dt and published_dt.astimezone(JST) < recent_threshold:
                continue

            title = clean_html(entry.get("title", ""))
            summary = clean_html(entry.get("summary", ""))
            items.append(
                Item(
                    source="各種記事",
                    title=title,
                    url=entry.get("link", ""),
                    published=published_to_jst(published_raw),
                    category="記事",
                    summary=summary[:500],
                )
            )
    return unique_items(items)


def webhook_url() -> str:
    return os.getenv("DISCORD_FAVORITE_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL", "")


def item_line(item: Item) -> str:
    date_part = f" / {item.published}" if item.published else ""
    title = item.title
    if len(title) > 120:
        title = title[:117].rstrip() + "..."
    return f"- [{title}]({item.url}){date_part}"


def chunk_item_lines(items: list[Item], max_chars: int = 900) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0

    for item in items:
        line = item_line(item)
        if len(line) > max_chars:
            line = line[: max_chars - 3].rstrip() + "..."

        added_len = len(line) + (1 if current else 0)
        if current and current_len + added_len > max_chars:
            chunks.append(current)
            current = []
            current_len = 0

        current.append(line)
        current_len += added_len

    if current:
        chunks.append(current)
    return chunks


def send_digest(notify_items: list[tuple[Item, list[str]]]) -> None:
    url = webhook_url()
    if not url:
        print("Webhook is not configured. Digest skipped.")
        return

    grouped: dict[str, list[Item]] = {}
    members: set[str] = set()
    for item, favorite_members in notify_items:
        grouped.setdefault(item.source, []).append(item)
        members.update(favorite_members)

    pages: list[dict] = []
    for source, items in grouped.items():
        chunks = chunk_item_lines(items)
        for chunk_index, lines in enumerate(chunks, start=1):
            pages.append(
                {
                    "source": source,
                    "source_count": len(items),
                    "chunk_index": chunk_index,
                    "chunk_count": len(chunks),
                    "lines": lines,
                }
            )

    embeds = []
    total_pages = len(pages)
    for page_index, page in enumerate(pages, start=1):
        page_suffix = f" {page_index}/{total_pages}" if total_pages > 1 else ""
        source_suffix = ""
        if page["chunk_count"] > 1:
            source_suffix = f" {page['chunk_index']}/{page['chunk_count']}"

        embeds.append(
            {
                "title": f"鈴木佑捺さん関連 新着まとめ{page_suffix}",
                "description": f"確認時刻: {now_jst_text()}\nメンバー: {'、'.join(sorted(members))}",
                "color": 0xF1C40F,
                "fields": [
                    {
                        "name": f"{page['source']} ({page['source_count']}件){source_suffix}",
                        "value": "\n".join(page["lines"]),
                        "inline": False,
                    }
                ],
            }
        )

    for start in range(0, len(embeds), 10):
        payload = {
            "username": "乃木坂46 推しメン通知",
            "embeds": embeds[start : start + 10],
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

    notify_items: list[tuple[Item, list[str]]] = []
    skipped_count = 0
    for item in new_items:
        text = f"{item.title}\n{item.category}\n{item.summary}"
        favorite_members = find_favorite_members(text, favorites)

        seen_urls.add(item.url)
        if not favorite_members:
            skipped_count += 1
            continue

        if first_run and not notify_on_first_run:
            skipped_count += 1
            continue

        notify_items.append((item, favorite_members))

    if notify_items:
        send_digest(notify_items)

    seen_data["seen_urls"] = sorted(seen_urls)
    seen_data["updated_at"] = datetime.now(JST).isoformat()
    save_json(SEEN_PATH, seen_data)

    message = f"乃木坂46自動通知: 送信 {len(notify_items)}件 / 通知なし既読 {skipped_count}件 / 確認 {now_jst_text()}"
    print(message)
    if first_run and not notify_on_first_run:
        send_log(message + "\n初回実行のため、鈴木佑捺さん関連も通知せず既読登録しました。")


if __name__ == "__main__":
    main()
