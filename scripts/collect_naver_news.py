"""네이버 뉴스 검색 API로 어제/오늘 뉴스를 모아 브리핑을 생성한다.

AI 요약(Claude API) 없이 무료로 동작하도록, 네이버가 제공하는 기사 요약(description)을
그대로 사용하고 중복 판단/중요도 정렬은 제목 유사도와 보도 매체 수로 근사한다.
"""
import datetime
import difflib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo

NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

KEYWORDS = ["속보", "사회", "경제", "IT", "인공지능", "국제", "스포츠", "문화"]
KST = zoneinfo.ZoneInfo("Asia/Seoul")
TITLE_SIMILARITY_THRESHOLD = 0.6
MAX_ARTICLES = 10
STOPWORDS = {"속보", "단독", "영상", "포토", "종합", "오늘", "어제", "내일", "관련"}

# 정치 관련 기사를 걸러내기 위한 키워드 (제목/요약에 하나라도 포함되면 제외)
POLITICS_KEYWORDS = {
    "정치", "대통령", "국회", "여야", "정당", "총리", "장관", "청와대",
    "국민의힘", "민주당", "조국혁신당", "개혁신당", "정의당",
    "법사위", "국정감사", "탄핵", "총선", "대선", "의원", "여당", "야당",
}


def is_politics(title, description):
    text = f"{title} {description}"
    return any(keyword in text for keyword in POLITICS_KEYWORDS)


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
            title = strip_tags(item.get("title", ""))
            description = strip_tags(item.get("description", ""))
            if is_politics(title, description):
                continue
            press = urllib.parse.urlparse(link).netloc
            articles[link] = {
                "title": title,
                "description": description,
                "link": item.get("link"),
                "press": press,
                "pubDate": pub,
            }
    return today, yesterday, list(articles.values())


def normalize_title(title):
    title = re.sub(r"[\[\(【].*?[\]\)】]", "", title)
    title = re.sub(r"[^0-9A-Za-z가-힣 ]", "", title)
    return re.sub(r"\s+", " ", title).strip()


def cluster_articles(articles):
    clusters = []  # each: {"rep": article, "members": [article,...]}
    for article in sorted(articles, key=lambda a: a["pubDate"], reverse=True):
        norm = normalize_title(article["title"])
        best_cluster = None
        best_ratio = 0.0
        for cluster in clusters:
            ratio = difflib.SequenceMatcher(None, norm, cluster["norm"]).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_cluster = cluster
        if best_cluster is not None and best_ratio >= TITLE_SIMILARITY_THRESHOLD:
            best_cluster["members"].append(article)
            if len(article["description"]) > len(best_cluster["rep"]["description"]):
                best_cluster["rep"] = article
        else:
            clusters.append({"rep": article, "members": [article], "norm": norm})
    clusters.sort(key=lambda c: (len(c["members"]), c["rep"]["pubDate"]), reverse=True)
    return clusters[:MAX_ARTICLES]


def top_keyword(clusters):
    counts = {}
    for cluster in clusters:
        for token in normalize_title(cluster["rep"]["title"]).split():
            if len(token) < 2 or token in STOPWORDS:
                continue
            counts[token] = counts.get(token, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def build_report(today, yesterday, clusters):
    lines = [f"# 네이버 뉴스 브리핑 ({today.isoformat()})", ""]
    lines.append(
        f"_{yesterday.isoformat()}~{today.isoformat()} 기간, 네이버 뉴스 검색 API 기반 자동 수집 "
        "(AI 요약 미사용 — 요약문은 네이버가 제공하는 원문 스니펫이며, 정렬은 보도 매체 수 기준입니다. 정치 기사는 제외됩니다)._"
    )
    lines.append("")

    for i, cluster in enumerate(clusters, 1):
        rep = cluster["rep"]
        member_count = len(cluster["members"])
        lines.append(f"## {i}. {rep['title']}")
        lines.append(f"- 언론사: {rep['press']}" + (f" 외 {member_count - 1}곳" if member_count > 1 else ""))
        lines.append(f"- 링크: {rep['link']}")
        lines.append(f"- 요약: {rep['description'] or '(요약 없음)'}")
        lines.append("")

    keyword = top_keyword(clusters)
    if keyword:
        lines.append(f"**오늘의 한 줄 총평**: 오늘은 총 {len(clusters)}건의 주요 뉴스가 수집됐고, '{keyword}' 관련 소식이 가장 많이 보도됐습니다.")
    else:
        lines.append(f"**오늘의 한 줄 총평**: 오늘은 총 {len(clusters)}건의 주요 뉴스가 수집됐습니다.")

    return "\n".join(lines)


def main():
    today, yesterday, articles = collect_articles()
    if not articles:
        report = f"# 네이버 뉴스 브리핑 ({today.isoformat()})\n\n어제/오늘 기간에 수집된 뉴스가 없습니다.\n"
    else:
        clusters = cluster_articles(articles)
        report = build_report(today, yesterday, clusters)

    os.makedirs("reports", exist_ok=True)
    out_path = os.path.join("reports", f"{today.isoformat()}-naver-news.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report.strip())
        f.write("\n")
    print(f"Wrote {out_path}")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(report.strip())
            f.write("\n")


if __name__ == "__main__":
    main()
