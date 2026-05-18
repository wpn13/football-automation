from __future__ import annotations

import datetime as dt
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from textwrap import dedent
from zoneinfo import ZoneInfo

import feedparser
from openai import OpenAI

try:
    from chinese_calendar import is_workday
except Exception:  # pragma: no cover - fallback for dependency/runtime issues
    is_workday = None


TZ = ZoneInfo("Asia/Shanghai")

RSS_SOURCES = {
    "BBC Football": "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "The Guardian Football": "https://www.theguardian.com/football/rss",
    "ESPN Soccer": "https://www.espn.com/espn/rss/soccer/news",
    "Sky Sports Football": "https://www.skysports.com/rss/12040",
}


def china_non_workday(today: dt.date) -> bool:
    if is_workday is not None:
        try:
            return not bool(is_workday(today))
        except Exception:
            pass
    return today.weekday() >= 5


def collect_feed_items(limit_per_source: int = 20) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()

    for source, url in RSS_SOURCES.items():
        feed = feedparser.parse(url)
        for entry in feed.entries[:limit_per_source]:
            link = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not link or not title or link in seen:
                continue

            seen.add(link)
            published = entry.get("published", entry.get("updated", "")).strip()
            summary = entry.get("summary", "").strip()
            items.append(
                {
                    "source": source,
                    "title": title,
                    "published": published,
                    "link": link,
                    "summary": summary[:500],
                }
            )

    return items


def format_items(items: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for idx, item in enumerate(items, start=1):
        lines.append(
            dedent(
                f"""
                [{idx}] {item["title"]}
                Source: {item["source"]}
                Published: {item["published"]}
                Link: {item["link"]}
                Summary: {item["summary"]}
                """
            ).strip()
        )
    return "\n\n".join(lines)


def build_report(today: dt.date, items: list[dict[str, str]]) -> str:
    client = OpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-5.2")

    prompt = f"""
    你是中文足球日报编辑。请根据下面的新闻源材料，整理一份全球足球早报。
    日期：{today.isoformat()}，时区：中国北京时间。

    重点关注过去约24小时内的重要足球信息，并兼顾未来三天的重要看点。
    请按重要性筛选，不要机械罗列新闻。每条尽量说明“为什么重要”。
    避免低可信度流言；如引用新闻或数据，请给出来源名称和链接。

    输出结构：
    1. 今日必看：5-8条最重要摘要。
    2. 昨夜今晨重点比赛：重要比分及影响。
    3. 欧洲赛场：重点覆盖欧冠、英超、西甲；包括争冠、欧战区、保级形势等。
    4. 中国足球：只关注中国男足国家队相关消息，以及重要国脚表现；去掉女足消息；普通联赛和亚冠只有直接影响国家队/国脚时才纳入。
    5. 世界大赛：关注世界杯、欧洲杯、美洲杯；无重要节点则跳过。
    6. 场外与热议：只收录足够有话题性和热度的重磅消息。
    7. 未来三天看点：重点比赛、国家队/国脚节点、欧冠/英超/西甲焦点战。

    新闻源材料：
    {format_items(items)}
    """

    response = client.responses.create(
        model=model,
        input=prompt,
    )
    return response.output_text.strip()


def send_email(subject: str, body: str) -> None:
    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    mail_to = os.environ.get("MAIL_TO", gmail_user)

    msg = EmailMessage()
    msg["From"] = gmail_user
    msg["To"] = mail_to
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(gmail_user, gmail_password)
        server.send_message(msg)


def main() -> int:
    now = dt.datetime.now(TZ)
    today = now.date()
    force_email = os.getenv("FORCE_EMAIL", "false").lower() in {"1", "true", "yes"}

    print(f"Running football report for {today.isoformat()} Asia/Shanghai")
    items = collect_feed_items()
    if not items:
        raise RuntimeError("No RSS feed items were collected.")

    report = build_report(today, items)
    report_path = "football-report.md"
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(report + "\n")

    should_send = force_email or china_non_workday(today)
    print(f"China non-workday: {china_non_workday(today)}; force_email: {force_email}")

    if should_send:
        subject = f"全球足球早报｜{today.isoformat()}"
        send_email(subject, report)
        print(f"Email sent: {subject}")
    else:
        print("Workday detected; email was not sent.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
