# 📰 Daily Newsletter Generator

RSS 헤드라인을 Claude AI로 자동 요약해서 GitHub Pages에 매일 발행하는 도구입니다.

## 커버 언론사

| 분류 | 언론사 |
|------|--------|
| 국내 경제 | 한국경제, 매일경제 |
| 글로벌 경제 | Reuters, Bloomberg |
| 패션/의류 | WWD, Business of Fashion |

## 기능

- 중요도 자동 분류 (Critical / High / Medium / Low)
- 한/영 요약 토글
- 매일 오전 8시 KST 자동 생성
- GitHub Pages 무료 호스팅

---

## 세팅 방법 (5단계)

### 1. 이 저장소를 GitHub에 올리기

```bash
git init
git add .
git commit -m "init"
git remote add origin https://github.com/YOUR_USERNAME/daily-newsletter.git
git push -u origin main
```

### 2. Anthropic API 키 발급

1. https://console.anthropic.com 접속
2. API Keys → Create Key
3. 키 복사해두기

### 3. GitHub Secrets에 API 키 등록

1. GitHub 저장소 → Settings → Secrets and variables → Actions
2. **New repository secret** 클릭
3. Name: `ANTHROPIC_API_KEY`
4. Secret: 위에서 복사한 키 붙여넣기

### 4. GitHub Pages 활성화

1. 저장소 → Settings → Pages
2. Source: **GitHub Actions** 선택

### 5. 첫 실행

1. 저장소 → Actions → Daily Newsletter
2. **Run workflow** 클릭
3. 약 1~2분 후 `https://YOUR_USERNAME.github.io/daily-newsletter/` 에서 확인

---

## 로컬에서 테스트

```bash
pip install feedparser anthropic
export ANTHROPIC_API_KEY="your-key-here"
python generate.py
# output/index.html 브라우저로 열기
```

## 언론사 RSS 변경/추가

`generate.py` 상단 `SOURCES` 리스트를 수정하세요:

```python
SOURCES = [
    {"name": "한국경제", "name_en": "Korea Economic Daily",
     "url": "https://feeds.hankyung.com/hankyung/economy.xml", "lang": "ko"},
    # 여기에 추가...
]
```

## 실행 시간 변경

`.github/workflows/newsletter.yml`의 cron을 수정하세요:

```yaml
- cron: '0 23 * * *'   # KST 오전 8시 = UTC 23:00 (전날)
- cron: '30 22 * * *'  # KST 오전 7시 30분
```
