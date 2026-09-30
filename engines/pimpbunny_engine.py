import asyncio
import json
import re
from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# Direct tokenized mp4 links, e.g. https://site/get_file/.../123_1080p.mp4/?v-acctoken=XXX&rnd=123
# page.content() returns attributes HTML-escaped, so "&" may show up as "&amp;".
MP4_PATTERN = re.compile(
    r'(https?://[^\s"\'<>\\]+?_(\d{3,4}p)\.mp4/?\?[^\s"\'<>\\]*?v-acctoken=[^\s"\'<>\\&]+'
    r'(?:(?:&amp;|&)[^\s"\'<>\\]*)?)'
)
# Video pages look like /the-nanny-s-secret_v1/ or end in .html or contain /video/
VIDEO_HREF = re.compile(r'(/video/|\.html$|_v\d+/?$)')

LINK_JS = """
() => Array.from(document.querySelectorAll(
    '.video-list-item a, .item-video a, .list-videos a, .item a'
)).map(a => {
    const img = a.querySelector('img');
    return {
        url: a.href,
        title: (a.getAttribute('title') || (img && img.alt) || a.textContent || '').trim(),
        thumb: img ? (img.dataset.original || img.dataset.src || img.src || '') : ''
    };
})
"""


class PimpBunnyEngine:
    def __init__(self, headless=True):
        self.headless = headless

    async def get_creator_videos(self, creator_url, max_pages=50):
        """
        Crawl a creator page and all its pagination.
        Returns list of {"url", "title", "thumb"} (deduplicated, order preserved).
        """
        base_url = creator_url.split('?')[0].rstrip('/')
        seen = {}
        print(f"Starting bulk scrape for: {base_url}", flush=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(user_agent=UA)
            page = await context.new_page()
            try:
                for page_num in range(1, max_pages + 1):
                    # Keep the same sort on every page so pagination is consistent
                    target = f"{base_url}/?sort_by=rating" if page_num == 1 \
                        else f"{base_url}/{page_num}/?sort_by=rating"
                    print(f"Scraping page {page_num}...", flush=True)

                    response = await page.goto(target, wait_until="domcontentloaded")
                    if response is None or response.status >= 400:
                        print("Reached end of pagination (HTTP error).", flush=True)
                        break

                    items = await page.evaluate(LINK_JS)
                    new_on_page = 0
                    for it in items:
                        href = it["url"].split('#')[0]
                        if not VIDEO_HREF.search(href):
                            continue
                        if href not in seen:
                            it["url"] = href
                            seen[href] = it
                            new_on_page += 1

                    # Out-of-range pages sometimes re-serve the last page instead of 404
                    if new_on_page == 0:
                        print("No new videos on this page. Stopping.", flush=True)
                        break
            finally:
                await browser.close()

        videos = list(seen.values())
        print(f"Total unique videos found: {len(videos)}", flush=True)
        return videos

    async def probe_video(self, video_url):
        """
        Open one video page, return tokenized direct links per quality plus
        cookies + referer for the downloader.
        """
        print(f"Probing video: {video_url}", flush=True)
        sniffed = {}  # quality -> url captured from real network requests

        def on_request(req):
            m = re.search(r'_(\d{3,4}p)\.mp4', req.url)
            if m and "v-acctoken" in req.url:
                sniffed[m.group(1)] = req.url

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            try:
                context = await browser.new_context(user_agent=UA)
                page = await context.new_page()
                page.on("request", on_request)

                await page.goto(video_url, wait_until="networkidle", timeout=60000)

                cookies = await context.cookies()
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                content = (await page.content()).replace("\\/", "/")  # unescape JSON-style \/
            finally:
                await browser.close()

        qualities = {}
        for full_url, quality in MP4_PATTERN.findall(content):
            qualities[quality] = full_url.replace("&amp;", "&")
        # Network-sniffed URLs are the most reliable, let them win
        qualities.update(sniffed)

        if not qualities:
            return {"error": "No tokenized media links found."}

        ordered = dict(sorted(qualities.items(), key=lambda kv: int(kv[0][:-1]), reverse=True))
        return {
            "referer": video_url,
            "cookies": cookie_str,
            "streams": ordered,
            "available_qualities": list(ordered.keys()),  # highest first
        }


if __name__ == "__main__":
    engine = PimpBunnyEngine(headless=True)
    test_url = "https://pimpbunny.com/the-nanny-s-secret_v1/"
    print(json.dumps(asyncio.run(engine.probe_video(test_url)), indent=4))