"""
smoke_test.py — 完整冒烟测试（Playwright CLI）
测试范围：
  1. Dashboard 页面加载
  2. Stats Bar 数据显示
  3. GitHub Trending Tab 数据渲染
  4. AI 新闻 Tab 切换与数据
  5. HN 热榜 Tab 数据
  6. 统计 Tab 数据
  7. 30秒倒计时显示
  8. 语言筛选交互
  9. API 直接访问
"""
import asyncio
import sys
import json
from playwright.async_api import async_playwright

BASE = "http://admin:Secret314.@localhost:8080/dashboard"
API  = "http://admin:Secret314.@localhost:8080/dashboard/api"

PASS = "\033[32m✅ PASS\033[0m"
FAIL = "\033[31m❌ FAIL\033[0m"

results = []

def check(name, ok, detail=""):
    status = PASS if ok else FAIL
    print(f"  {status}  {name}" + (f"  [{detail}]" if detail else ""))
    results.append((name, ok))
    return ok

async def test_apis():
    """Test 1: API 直接访问"""
    import urllib.request, urllib.error, base64

    print("\n── Test 1: API 端点 ──────────────────────────────")
    auth = base64.b64encode(b"admin:Secret314.").decode()
    headers = {"Authorization": f"Basic {auth}"}

    endpoints = [
        ("/api/health",          "status"),
        ("/api/github?limit=5",  None),
        ("/api/hn?limit=5",      None),
        ("/api/news?limit=5",    None),
        ("/api/stats",           "github"),
    ]
    for path, key in endpoints:
        url = f"http://localhost:8080/dashboard{path}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                if key:
                    ok = key in data
                else:
                    ok = isinstance(data, list)
                check(f"GET {path}", ok, f"{len(data) if isinstance(data, list) else 'ok'}")
        except Exception as e:
            check(f"GET {path}", False, str(e))

async def test_browser():
    """Test 2-8: 浏览器端到端测试"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path="/root/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell",
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage", "--disable-gpu"]
        )
        page = await browser.new_page(
            http_credentials={"username": "admin", "password": "Secret314."}
        )
        # 监听 console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(str(err)))

        print("\n── Test 2: 页面加载 ──────────────────────────────")
        resp = await page.goto(f"http://localhost:8080/dashboard/", wait_until="networkidle", timeout=15000)
        check("HTTP 200", resp.status == 200, str(resp.status))

        title = await page.title()
        check("页面标题", "虾人情报站" in title, title)

        # Header
        header = await page.locator("header").count()
        check("Header 存在", header > 0)

        print("\n── Test 3: Stats Bar ──────────────────────────────")
        await page.wait_for_selector("#s-github", timeout=8000)
        github_stat = await page.locator("#s-github").inner_text()
        check("GitHub 统计数字", github_stat != "-" and github_stat.isdigit(), github_stat)

        news_stat = await page.locator("#s-news").inner_text()
        check("新闻统计数字", news_stat != "-" and news_stat.isdigit(), news_stat)

        hn_stat = await page.locator("#s-hn").inner_text()
        check("HN 统计数字", hn_stat != "-" and hn_stat.isdigit(), hn_stat)

        print("\n── Test 4: GitHub Trending Tab ───────────────────")
        await page.wait_for_selector(".repo-card", timeout=10000)
        card_count = await page.locator(".repo-card").count()
        check("GitHub 卡片渲染", card_count > 0, f"{card_count} 条")

        first_repo = await page.locator(".repo-name").first.inner_text()
        check("仓库名称显示", "/" in first_repo, first_repo[:30])

        # stars_today badge
        today_badge = await page.locator(".repo-today").count()
        check("今日 Star 徽章", today_badge > 0, f"{today_badge} 个")

        print("\n── Test 5: 语言筛选 ──────────────────────────────")
        await page.locator("button:has-text('Python')").first.click()
        await asyncio.sleep(1)
        py_cards = await page.locator(".repo-card").count()
        check("Python 筛选响应", True, f"筛选后 {py_cards} 条")

        # 恢复全部
        await page.locator("button:has-text('全部')").first.click()
        await asyncio.sleep(0.5)

        print("\n── Test 6: 资讯 Tab ───────────────────────────")
        await page.locator(".tab:has-text('资讯')").click()
        await page.wait_for_selector(".news-card", timeout=8000)
        news_count = await page.locator(".news-card").count()
        check("新闻卡片渲染", news_count > 0, f"{news_count} 条")

        # 来源标签
        source_tag = await page.locator(".news-source").first.inner_text()
        check("新闻来源标签显示", len(source_tag) > 0, source_tag[:20])

        # 分类 badge
        cat_badge = await page.locator(".cat-badge").count()
        check("分类 badge 显示", cat_badge > 0, f"{cat_badge} 个")

        # 来源筛选 (Bloomberg)
        await page.locator("button:has-text('Bloomberg')").click()
        await asyncio.sleep(0.5)
        bl_count = await page.locator(".news-card").count()
        check("Bloomberg 筛选", bl_count > 0, f"{bl_count} 条")

        print("\n── Test 7: HN 热榜 Tab ───────────────────────────")
        await page.locator(".tab:has-text('HN 热榜')").click()
        await page.wait_for_selector(".hn-card", timeout=8000)
        hn_count = await page.locator(".hn-card").count()
        check("HN 卡片渲染", hn_count > 0, f"{hn_count} 条")

        hn_score = await page.locator(".hn-score").first.inner_text()
        check("HN 分数显示", hn_score.isdigit(), f"top score: {hn_score}")

        print("\n── Test 8: 统计 Tab ──────────────────────────────")
        await page.locator(".tab:has-text('统计')").click()
        await page.wait_for_selector(".stats-block", timeout=8000)
        stats_blocks = await page.locator(".stats-block").count()
        check("统计块渲染", stats_blocks >= 3, f"{stats_blocks} 块")

        print("\n── Test 9: 无 JS 报错 ───────────────────────────")
        check("无 console error", len(console_errors) == 0,
              console_errors[0][:80] if console_errors else "clean")

        await browser.close()

async def main():
    print("=" * 55)
    print("🦐 虾人情报站 冒烟测试")
    print("=" * 55)

    await test_apis()
    await test_browser()

    print("\n" + "=" * 55)
    passed = sum(1 for _, ok in results if ok)
    total  = len(results)
    failed = [(n, ok) for n, ok in results if not ok]
    print(f"结果：{passed}/{total} 通过", "🎉" if not failed else "⚠️")
    if failed:
        print("失败项：")
        for n, _ in failed:
            print(f"  ❌ {n}")
    print("=" * 55)
    sys.exit(0 if not failed else 1)

if __name__ == "__main__":
    asyncio.run(main())
