"""
Daily Newsletter Generator
- RSS 수집: 한국경제, 매일경제, Reuters, Bloomberg, WWD, BoF
- Claude AI로 요약 + 중요도 분류 (Critical/High/Medium/Low)
- 한/영 토글 지원 HTML 생성
- GitHub Pages 배포용
"""

import feedparser
import anthropic
import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# ─── 설정 ──────────────────────────────────────────────────────────────────────

SOURCES = [
    {"name": "한국경제",   "name_en": "Korea Economic Daily", "url": "https://www.hankyung.com/feed/economy",                "lang": "ko"},
    {"name": "매일경제",   "name_en": "Maeil Business News",  "url": "https://www.mk.co.kr/rss/30000001/",                   "lang": "ko"},
    {"name": "Reuters",    "name_en": "Reuters",              "url": "https://feeds.reuters.com/reuters/businessNews.rss",    "lang": "en"},
    {"name": "Bloomberg",  "name_en": "Bloomberg",            "url": "https://feeds.bloomberg.com/markets/news.rss",          "lang": "en"},
    {"name": "WWD",        "name_en": "WWD",                  "url": "https://wwd.com/feed/",                                 "lang": "en"},
    {"name": "BoF",        "name_en": "Business of Fashion",  "url": "https://www.businessoffashion.com/feed/news/",          "lang": "en"},
]

MAX_ARTICLES_PER_SOURCE = 5   # 소스당 최대 기사 수
OUTPUT_FILE = Path(__file__).parent / "output" / "index.html"

# ─── URL 리졸버 ────────────────────────────────────────────────────────────────

def resolve_url(url: str, timeout: int = 5) -> str:
    """
    RSS 리다이렉트 URL → 실제 기사 URL로 변환합니다.

    처리 순서:
    1. feedburner / Google News 등 쿼리 파라미터에 원본 URL이 포함된 경우 추출
    2. HTTP HEAD 요청으로 리다이렉트 따라가기
    3. 실패 시 원본 URL 그대로 반환
    """
    if not url:
        return url

    # 1) 쿼리 파라미터에 원본 URL이 있는 패턴 처리
    #    예: https://news.google.com/rss/articles/...?url=https://...
    #        https://feedproxy.google.com/~r/.../~3/https://...
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    for key in ("url", "u", "redirect", "target"):
        if key in qs:
            candidate = qs[key][0]
            if candidate.startswith("http"):
                return candidate

    # feedburner ~3/ 패턴 (percent-encoded URL도 처리)
    decoded = urllib.parse.unquote(url)
    m = re.search(r"~3/(https?://[^\s\"'>]+)", decoded)
    if m:
        return m.group(1)

    # 2) HTTP HEAD 요청으로 리다이렉트 추적 (최대 5회)
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        final = resp.geturl()
        # 트래킹 파라미터 제거 (utm_*, ref 등)
        fp = urllib.parse.urlparse(final)
        clean_qs = {k: v for k, v in urllib.parse.parse_qs(fp.query).items()
                    if not k.lower().startswith(("utm_", "ref", "src", "campaign"))}
        clean = fp._replace(query=urllib.parse.urlencode(clean_qs, doseq=True)).geturl()
        return clean
    except Exception:
        return url  # 실패 시 원본 반환


# ─── RSS 수집 ──────────────────────────────────────────────────────────────────

def fetch_headlines(source: dict) -> list[dict]:
    """RSS에서 헤드라인을 가져옵니다."""
    try:
        feed = feedparser.parse(source["url"])
        articles = []
        for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
            raw_link = entry.get("link", "")
            # RSS entry에 원본 링크 필드가 따로 있는 경우 우선 사용
            # (feedburner는 feedburner_origlink, Reuters는 id 필드에 실제 URL)
            real_link = (
                entry.get("feedburner_origlink")
                or entry.get("original-link")
                or entry.get("id", "")  # Reuters: id == 실제 기사 URL
                or raw_link
            )
            # id가 URL 형태가 아니면 link 사용
            if real_link and not real_link.startswith("http"):
                real_link = raw_link
            # 여전히 리다이렉트가 의심되면 HEAD 요청으로 해소
            if real_link and any(x in real_link for x in
                                 ("feedproxy", "feedburner", "news.google", "rss.", "/redir")):
                real_link = resolve_url(real_link)

            articles.append({
                "source": source["name"],
                "source_en": source["name_en"],
                "lang": source["lang"],
                "title": entry.get("title", ""),
                "link": real_link or raw_link,
                "summary": entry.get("summary", entry.get("description", ""))[:500],
                "published": entry.get("published", ""),
            })
        print(f"  ✓ {source['name']}: {len(articles)}개 수집")
        return articles
    except Exception as e:
        print(f"  ✗ {source['name']} 오류: {e}")
        return []


# ─── AI 요약 ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 패션/의류 업계 및 글로벌 비즈니스 뉴스 전문 편집자입니다.
주어진 기사 목록을 분석하여 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.

각 기사에 대해:
1. importance: "Critical" | "High" | "Medium" | "Low"
   - Critical: 업계 전반에 즉각적 영향 (관세, 대형 M&A, 공급망 위기 등)
   - High: 주요 브랜드/바이어 동향, 시장 변화
   - Medium: 트렌드, 전략 발표
   - Low: 일반 정보성 기사
2. summary_ko: 한국어 2~3문장 요약
3. summary_en: 영어 2~3문장 요약
4. tags: 관련 키워드 최대 3개 (한국어)

응답 형식:
{"articles": [{"id": 0, "importance": "High", "summary_ko": "...", "summary_en": "...", "tags": ["관세", "공급망"]}]}"""


def summarize_articles(articles: list[dict]) -> list[dict]:
    """Claude API로 기사를 요약하고 중요도를 분류합니다."""
    if not articles:
        return []

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    # 기사 목록 텍스트 구성
    articles_text = ""
    for i, a in enumerate(articles):
        articles_text += f"\n[{i}] 출처: {a['source']} | 제목: {a['title']}\n내용: {a['summary']}\n"

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": articles_text}]
        )
        raw = message.content[0].text.strip()
        # 마크다운 코드블록 제거 (```json ... ``` 형태로 올 때)
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()
        print(f"  AI 응답 미리보기: {raw[:100]}")
        result = json.loads(raw)

        # AI 결과를 원본 기사에 병합
        enriched = []
        for item in result.get("articles", []):
            idx = item["id"]
            if idx < len(articles):
                merged = {**articles[idx], **item}
                enriched.append(merged)
        print(f"  ✓ AI 요약 완료: {len(enriched)}개")
        return enriched
    except Exception as e:
        print(f"  ✗ AI 요약 오류: {e}")
        # 폴백: 중요도 없이 원본 반환
        for a in articles:
            a["importance"] = "Medium"
            a["summary_ko"] = a["summary"]
            a["summary_en"] = a["summary"]
            a["tags"] = []
        return articles


# ─── HTML 생성 ─────────────────────────────────────────────────────────────────

IMPORTANCE_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
IMPORTANCE_COLOR = {
    "Critical": ("#dc2626", "#fef2f2"),
    "High":     ("#d97706", "#fffbeb"),
    "Medium":   ("#2563eb", "#eff6ff"),
    "Low":      ("#6b7280", "#f9fafb"),
}

def build_html(articles: list[dict], date_range: str) -> str:
    today = datetime.now().strftime("%B %d, %Y")
    sorted_articles = sorted(articles, key=lambda x: IMPORTANCE_ORDER.get(x.get("importance", "Low"), 3))

    # 기사 카드 HTML 생성
    cards_html = ""
    for a in sorted_articles:
        imp = a.get("importance", "Medium")
        color, bg = IMPORTANCE_COLOR.get(imp, ("#6b7280", "#f9fafb"))
        tags_html = "".join(f'<span class="tag">{t}</span>' for t in a.get("tags", []))
        cards_html += f"""
        <div class="card" data-importance="{imp}">
          <div class="card-header" style="border-left: 4px solid {color}; background: {bg};">
            <div class="card-meta">
              <span class="source-badge">{a['source']}</span>
              <span class="importance-badge" style="color:{color}; border-color:{color};">{imp}</span>
            </div>
            <h3 class="card-title">
              <a href="{a.get('link','#')}" target="_blank" rel="noopener">{a['title']}</a>
            </h3>
            <div class="tags">{tags_html}</div>
          </div>
          <div class="card-body">
            <p class="summary ko">{a.get('summary_ko', '')}</p>
            <p class="summary en hidden">{a.get('summary_en', '')}</p>
          </div>
        </div>"""

    # 중요도별 카운트
    counts = {}
    for a in articles:
        imp = a.get("importance", "Medium")
        counts[imp] = counts.get(imp, 0) + 1

    filter_buttons = '<button class="filter-btn active" data-filter="all">All</button>'
    for imp in ["Critical", "High", "Medium", "Low"]:
        cnt = counts.get(imp, 0)
        if cnt:
            color, _ = IMPORTANCE_COLOR[imp]
            filter_buttons += f'<button class="filter-btn" data-filter="{imp}" style="--imp-color:{color}">{imp} <span class="cnt">{cnt}</span></button>'

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daily Newsletter | {today}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f8fafc; color: #1e293b; }}

  /* Header */
  .hero {{ background: #0f172a; color: white; padding: 48px 24px; text-align: right; }}
  .hero h1 {{ font-size: clamp(2rem, 5vw, 3.5rem); font-weight: 800; letter-spacing: -0.02em; }}
  .hero .date {{ margin-top: 8px; opacity: 0.6; font-size: 0.9rem; letter-spacing: 0.1em; text-transform: uppercase; }}

  /* Controls */
  .controls {{ max-width: 900px; margin: 32px auto 0; padding: 0 24px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
  .lang-toggle {{ display: flex; background: white; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }}
  .lang-toggle button {{ padding: 8px 20px; border: none; background: transparent; cursor: pointer; font-size: 0.85rem; font-weight: 600; color: #64748b; transition: all .2s; }}
  .lang-toggle button.active {{ background: #0f172a; color: white; }}
  .filter-wrap {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  .filter-btn {{ padding: 7px 16px; border: 1.5px solid #e2e8f0; background: white; border-radius: 20px; cursor: pointer; font-size: 0.82rem; font-weight: 600; color: #64748b; transition: all .2s; }}
  .filter-btn.active {{ border-color: var(--imp-color, #0f172a); background: var(--imp-color, #0f172a); color: white; }}
  .filter-btn[data-filter="all"].active {{ background: #0f172a; border-color: #0f172a; color: white; }}
  .cnt {{ opacity: 0.8; }}

  /* Cards */
  .articles {{ max-width: 900px; margin: 24px auto 48px; padding: 0 24px; display: flex; flex-direction: column; gap: 16px; }}
  .card {{ background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.06); transition: box-shadow .2s; }}
  .card:hover {{ box-shadow: 0 4px 12px rgba(0,0,0,.1); }}
  .card.hidden {{ display: none; }}
  .card-header {{ padding: 16px 20px 12px; }}
  .card-meta {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
  .source-badge {{ font-size: 0.75rem; font-weight: 700; background: #0f172a; color: white; padding: 2px 10px; border-radius: 20px; letter-spacing: 0.05em; }}
  .importance-badge {{ font-size: 0.72rem; font-weight: 700; border: 1.5px solid; padding: 1px 9px; border-radius: 20px; text-transform: uppercase; letter-spacing: 0.05em; }}
  .card-title {{ font-size: 0.95rem; font-weight: 700; line-height: 1.4; }}
  .card-title a {{ color: inherit; text-decoration: none; }}
  .card-title a:hover {{ text-decoration: underline; }}
  .tags {{ margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }}
  .tag {{ font-size: 0.72rem; background: #f1f5f9; color: #475569; padding: 2px 10px; border-radius: 20px; }}
  .card-body {{ padding: 12px 20px 16px; border-top: 1px solid #f1f5f9; }}
  .summary {{ font-size: 0.88rem; line-height: 1.65; color: #475569; }}
  .hidden {{ display: none; }}

  /* Footer */
  footer {{ text-align: center; padding: 24px; font-size: 0.78rem; color: #94a3b8; }}
</style>
</head>
<body>

<div class="hero">
  <h1>Daily Newsletter</h1>
  <div class="date">{date_range}</div>
</div>

<div class="controls">
  <div class="lang-toggle">
    <button class="active" onclick="setLang('ko')">한국어</button>
    <button onclick="setLang('en')">English</button>
  </div>
  <div class="filter-wrap">
    {filter_buttons}
  </div>
</div>

<div class="articles" id="articles">
  {cards_html}
</div>

<footer>Generated {today} · Powered by Claude AI</footer>

<script>
function setLang(lang) {{
  document.querySelectorAll('.lang-toggle button').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.summary.ko').forEach(el => el.classList.toggle('hidden', lang !== 'ko'));
  document.querySelectorAll('.summary.en').forEach(el => el.classList.toggle('hidden', lang !== 'en'));
}}

document.querySelectorAll('.filter-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const filter = btn.dataset.filter;
    document.querySelectorAll('.card').forEach(card => {{
      card.classList.toggle('hidden', filter !== 'all' && card.dataset.importance !== filter);
    }});
  }});
}});
</script>
</body>
</html>"""


# ─── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    today = datetime.now(timezone.utc)
    date_range = today.strftime("%B %d, %Y").upper()
    print(f"\n📰 Daily Newsletter 생성 시작 — {date_range}\n")

    # 1. RSS 수집
    print("1. RSS 헤드라인 수집 중...")
    all_articles = []
    for source in SOURCES:
        all_articles.extend(fetch_headlines(source))
    print(f"   총 {len(all_articles)}개 기사 수집\n")

    # 2. AI 요약
    print("2. Claude AI 요약 및 중요도 분류 중...")
    # 배치로 나눠서 처리 (API 토큰 한계 고려)
    batch_size = 10
    enriched = []
    for i in range(0, len(all_articles), batch_size):
        batch = all_articles[i:i+batch_size]
        enriched.extend(summarize_articles(batch))
    print(f"   총 {len(enriched)}개 요약 완료\n")

    # 3. HTML 생성
    print("3. HTML 생성 중...")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    html = build_html(enriched, date_range)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"   ✓ 저장: {OUTPUT_FILE}\n")

    print("✅ 완료!\n")


if __name__ == "__main__":
    main()
