"""네이버 뉴스 검색 API로 어제/오늘 뉴스를 모아 Claude API로 정리한 브리핑을 생성한다."""
import datetime
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo

NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

KEYWORDS = ["속보", "사회", "정치", "경제", "IT", "인공지능", "국제"]
KST = zoneinfo.ZoneInfo("Asia/Seoul")


def fetch_news(query, display=30):
    url = "https://openapi.naver.com/v1/search/news.json?" + urllib.parse.urlencode(
        {"query": query, "display": display, "sort": "date"}
    )
    req = urllib.request.Request(
        url,
        headers={
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def strip_tags(text):
    text = re.sub(r"<.*?>", "", text)
    return (
        text.replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def parse_pubdate(value):
    return datetime.datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %z").astimezone(KST)


def collect_articles():
    now_kst = datetime.datetime.now(KST)
    today = now_kst.date()
    yesterday = today - datetime.timedelta(days=1)

    articles = {}
    for keyword in KEYWORDS:
        try:
            data = fetch_news(keyword)
        except urllib.error.HTTPError as e:
            print(f"[warn] '{keyword}' 검색 실패: {e}")
            continue
        for item in data.get("items", []):
            try:
                pub = parse_pubdate(item["pubDate"])
            except ValueError:
                continue
            if pub.date() not in (today, yesterday):
                continue
            link = item.get("originallink") or item.get("link")
            if not link or link in articles:
                continue
            press = urllib.parse.urlparse(link).netloc
            articles[link] = {
                "title": strip_tags(item.get("title", "")),
                "description": strip_tags(item.get("description", "")),
                "link": item.get("link"),
                "originallink": item.get("originallink"),
                "press": press,
                "pubDate": pub.isoformat(),
            }
    return today, yesterday, list(articles.values())


def build_prompt(today, yesterday, articles):
    return f"""다음은 네이버 뉴스 검색 API로 수집한 {yesterday.isoformat()}~{today.isoformat()} 기간 뉴스 원본 목록(JSON)입니다.

아래 규칙에 따라 한국어로 정리해 주세요:
1. 같은 사건을 다루는 중복/유사 기사는 하나로 묶고, 대표 링크 1개만 남기세요.
2. 중요도(사회적 파급력, 시의성)가 높은 순서로 정렬하세요.
3. 최대 10건만 선정하세요.
4. 각 기사마다 다음 항목을 포함하세요: 제목, 언론사, 링크, 세 줄 요약(각 줄은 완결된 문장).
5. 맨 마지막에 "오늘의 한 줄 총평"을 한 문장으로 작성하세요.
6. 출력은 마크다운 형식으로, 다른 설명 없이 결과만 출력하세요.

원본 데이터:
{json.dumps(articles, ensure_ascii=False)}
"""


def summarize(prompt):
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(
            {
                "model": ANTHROPIC_MODEL,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.load(resp)
    return "".join(block["text"] for block in result["content"] if block["type"] == "text")


def main():
    today, yesterday, articles = collect_articles()
    if not articles:
        summary_md = "어제/오늘 기간에 수집된 뉴스가 없습니다."
    else:
        summary_md = summarize(build_prompt(today, yesterday, articles))

    os.makedirs("reports", exist_ok=True)
    out_path = os.path.join("reports", f"{today.isoformat()}-naver-news.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# 네이버 뉴스 브리핑 ({today.isoformat()})\n\n")
        f.write(summary_md.strip())
        f.write("\n")
    print(f"Wrote {out_path}")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(f"# 네이버 뉴스 브리핑 ({today.isoformat()})\n\n")
            f.write(summary_md.strip())
            f.write("\n")


if __name__ == "__main__":
    main()
