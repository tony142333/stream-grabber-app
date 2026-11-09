import asyncio
import json
import re
import time
from urllib.parse import urljoin

from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# Direct tokenized mp4 links, e.g. https://site/get_file/.../123_1080p.mp4/?v-acctoken=XXX&rnd=123
# page.content() returns attributes HTML-escaped, so "&" may show up as "&amp;".
MP4_PATTERN = re.compile(
    r'(https?://[^\s"\'<>\\]+?_(\d{3,4}p)\.mp4/?\?[^\s"\'<>\\]*?v-acctoken=[^\s"\'<>\\&]+'
    r'(?:(?:&amp;|&)[^\s"\'<>\\]*)?)'
)
# Video pages look like https://pimpbunny.com/videos/<slug>/  (legacy: /video/, .html, _v1/)
VIDEO_HREF = re.compile(r'(/videos?/[^/?#]+/?$|\.html$|_v\d+/?$)')

LINK_JS = """
() => {
    // Class names like "ui-card-link__KxRw6l" carry a build hash, so match on the stable prefix.
    // "b6m-video" is a plain, unhashed class on the card.
    const anchors = Array.from(document.querySelectorAll(
        '.b6m-video a[href], [class*="ui-card-video"] a[href], a[class*="ui-card-link"][href], a[href*="/videos/"], article a[href]'
    ));
    return anchors.map(a => {
        const card = a.closest('.b6m-video, [class*="ui-card-video"], article') || a.parentElement;
        const img = card ? card.querySelector('img') : null;
        const info = card ? card.querySelector('[class*="ui-card-info"]') : null;
        return {
            url: a.href,
            title: ((info && info.innerText) || a.getAttribute('title') || (img && img.alt) || a.innerText || '')
                       .trim().split('\\n')[0],
            thumb: img ? (img.dataset.original || img.dataset.src || img.currentSrc || img.src || '') : ''
        };
    });
}
"""

REDIRECT_CODES = (301, 302, 303, 307, 308)


class PimpBunnyEngine:
    def __init__(self, headless=True):
        self.headless = headless

    async def get_creator_videos(self, creator_url, max_pages=50):
        """
        Crawl only the provided creator page.
        Does not follow pagination.
        Returns list of {"url", "title", "thumb"} (deduplicated, order preserved).
        """
        base_url = creator_url.split('?')[0].rstrip('/')
        seen = {}
        print(f"Starting bulk scrape for: {base_url}", flush=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            context = await browser.new_context(
                user_agent=UA,
                viewport={"width": 1920, "height": 1080},
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
            )
            page = await context.new_page()
            try:
                target = f"{base_url}/"
                print(f"Scraping page: {target}", flush=True)

                response = await page.goto(target, wait_until="domcontentloaded", timeout=45000)
                if response is None or response.status >= 400:
                    status_code = response.status if response else 'None'
                    print(f"Failed to load page. Status: {status_code}", flush=True)
                    return []

                # Give dynamic elements time to hydrate/render
                await page.wait_for_timeout(3000)
                try:
                    await page.wait_for_selector('a[href*="/videos/"], article a', timeout=15000)
                except Exception:
                    pass

                items = await page.evaluate(LINK_JS)
                for it in items:
                    href = it["url"].split('#')[0]
                    if not VIDEO_HREF.search(href):
                        continue
                    if href not in seen:
                        it["url"] = href
                        seen[href] = it

            except Exception as e:
                print(f"Exception during crawl: {e}", flush=True)
                try:
                    content = await page.content()
                    with open("pb_probe_debug.html", "w", encoding="utf-8") as f:
                        f.write(content)
                except Exception:
                    pass
            finally:
                await browser.close()

        videos = list(seen.values())
        print(f"Total unique videos found: {len(videos)}", flush=True)
        return videos

    @staticmethod
    def _mask(u):
        return re.sub(r"(v-acctoken=)[^&\s]+", r"\1***", u)

    async def _try_play(self, page):
        """Best effort: dismiss an age gate and start the player so it requests its mp4."""
        try:
            btn = page.get_by_role("button", name=re.compile(r"(i am|i'm) 18|enter|agree", re.I)).first
            if await btn.count():
                await btn.click(timeout=1500)
        except Exception:
            pass
        try:
            await page.evaluate("() => { const v = document.querySelector('video');"
                                " if (v) { v.muted = true; v.play().catch(() => {}); } }")
        except Exception:
            pass
        for sel in ['.fp-play', '.jw-icon-display', '.vjs-big-play-button',
                    'button[aria-label*="lay" i]', '[class*="play" i]']:
            try:
                el = page.locator(sel).first
                if await el.count():
                    await el.click(timeout=1500)
                    break
            except Exception:
                continue

    async def _resolve_and_validate(self, context, url, referer, label):
        """
        Use the live browser session (same cookies / UA / IP as the page) to request
        the file with Range: bytes=0-0 and follow redirects manually, hop by hop.

        Returns (ok, final_url, info_string).
        On success final_url is the last URL in the redirect chain, which is the one
        that actually serves bytes (it may carry its own token).
        """
        headers = {
            "Referer": referer,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Range": "bytes=0-0",
        }
        current = url
        for hop in range(6):
            try:
                resp = await context.request.get(
                    current,
                    headers=headers,
                    max_redirects=0,
                    fail_on_status_code=False,
                    timeout=30000,
                )
            except Exception as e:
                print(f"  [resolve] {label} hop {hop}: request error {self._mask(str(e))[:160]}", flush=True)
                return False, url, f"request error: {type(e).__name__}"

            status = resp.status
            loc = resp.headers.get("location", "")
            crange = resp.headers.get("content-range", "")
            print(f"  [resolve] {label} hop {hop}: HTTP {status} "
                  f"{self._mask(current)[:110]}"
                  f"{' -> ' + self._mask(loc)[:110] if loc else ''}"
                  f"{' range=' + crange if crange else ''}", flush=True)

            if status in REDIRECT_CODES and loc:
                current = urljoin(current, loc)
                continue
            if status in (200, 206):
                return True, current, f"HTTP {status} {crange}".strip()
            return False, url, f"HTTP {status} at hop {hop}"

        return False, url, "too many redirects"

    async def probe_video(self, video_url, max_wait=40):
        """
        Open one video page, return tokenized direct links per quality plus
        cookies + referer for the downloader.

        Video pages keep loading ads/trackers forever, so we never wait for
        "networkidle": we load the DOM, then poll until a media link shows up.

        Each link is then verified (and its redirects resolved) from inside the
        browser session, so the downloader receives a URL that is known to work.
        """
        print(f"Probing video: {video_url}", flush=True)
        sniffed = {}        # quality -> url captured from real network requests
        media_seen = []     # every media-looking request (for diagnostics)
        media_headers = {}  # headers the browser itself sent on the first mp4 request

        def on_request(req):
            u = req.url
            if re.search(r"\.(mp4|m3u8|webm)(\?|/|$)", u):
                media_seen.append(u)
                if not media_headers:
                    try:
                        media_headers.update(req.headers)
                    except Exception:
                        pass
            m = re.search(r"_(\d{3,4})p\.mp4", u)
            if m:
                sniffed[m.group(1) + "p"] = u

        def scan(html):
            found = {}
            for full_url, quality in MP4_PATTERN.findall(html):
                found[quality] = full_url.replace("&amp;", "&")
            return found

        validated = {}
        notes = {}

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
            )
            try:
                context = await browser.new_context(
                    user_agent=UA,
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                    timezone_id="America/New_York"
                )
                page = await context.new_page()
                page.on("request", on_request)

                await page.goto(video_url, wait_until="domcontentloaded", timeout=60000)

                # Wait extra time for any Cloudflare/security challenge to automatically pass
                print("Waiting for potential security check / challenge to clear...", flush=True)
                await page.wait_for_timeout(7000)

                qualities, content, played = {}, "", False
                t0 = time.monotonic()
                while time.monotonic() - t0 < max_wait:
                    try:
                        content = (await page.content()).replace("\\/", "/")
                        qualities = {**scan(content), **sniffed}
                        if qualities:
                            await page.wait_for_timeout(2000)   # let other qualities show up
                            content = (await page.content()).replace("\\/", "/")
                            qualities = {**scan(content), **sniffed}
                            break
                    except Exception:
                        # Ignore transient navigation content-refresh errors
                        pass

                    if not played and time.monotonic() - t0 > 6:
                        await self._try_play(page)
                        played = True
                    await page.wait_for_timeout(1000)

                info = {}
                if not qualities:
                    # last resort: what is the <video> element actually playing?
                    try:
                        info = await page.evaluate(
                            "() => { const v = document.querySelector('video');"
                            " return { h: v ? v.videoHeight : 0, src: v ? (v.currentSrc || v.src) : '',"
                            " sources: Array.from(document.querySelectorAll('video source')).map(s => s.src) }; }")
                        src = info.get("src") or ""
                        if ".mp4" in src and info.get("h"):
                            qualities[f"{info['h']}p"] = src
                    except Exception:
                        pass

                # ---- verify + resolve every link inside the live browser session ----
                if qualities:
                    if media_headers:
                        shown = {k: v for k, v in media_headers.items()
                                 if k.lower() in ("range", "referer", "accept", "sec-fetch-dest",
                                                  "sec-fetch-mode", "sec-fetch-site", "user-agent")}
                        print(f"  [debug] headers the browser sent on its own mp4 request: {shown}", flush=True)
                        print(f"  [debug] (all header names: {sorted(media_headers.keys())})", flush=True)

                    ordered_try = sorted(qualities.items(), key=lambda kv: int(kv[0][:-1]), reverse=True)
                    for q, u in ordered_try:
                        ok, final_url, note = await self._resolve_and_validate(context, u, video_url, q)
                        notes[q] = note
                        if ok:
                            validated[q] = final_url
                    print(f"  [debug] browser-verified qualities: {list(validated.keys()) or 'NONE'}", flush=True)
                    for q, n in notes.items():
                        print(f"  [debug]   {q}: {n}", flush=True)

                # cookies AFTER requests so any cookie set during redirects is included
                cookies = await context.cookies()
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

                if not qualities:
                    try:
                        body = (await page.inner_text("body"))[:300].replace("\n", " ")
                    except Exception:
                        body = "Could not retrieve body text during navigation."
                    print(f"  [debug] final URL : {page.url}", flush=True)
                    print(f"  [debug] title     : {await page.title()}", flush=True)
                    print(f"  [debug] <video>   : element check failed or none found, info={info}", flush=True)
                    print(f"  [debug] media requests seen: {len(media_seen)}", flush=True)
                    for u in media_seen[:10]:
                        print(f"    {self._mask(u)[:200]}", flush=True)
                    print(f"  [debug] body start: {body}", flush=True)
                    try:
                        with open("pb_probe_debug.html", "w", encoding="utf-8") as f:
                            f.write(self._mask(content))
                    except Exception:
                        pass
            finally:
                await browser.close()

        if not qualities:
            return {"error": "No media links found on the video page (see [debug] lines)."}

        # Prefer browser-verified (redirect-resolved) URLs. If a quality failed
        # verification, still pass the original so behaviour never regresses.
        final = {}
        for q, original in qualities.items():
            final[q] = validated.get(q, original)

        ordered = dict(sorted(final.items(), key=lambda kv: int(kv[0][:-1]), reverse=True))
        return {
            "referer": video_url,
            "cookies": cookie_str,
            "user_agent": UA,
            "streams": ordered,
            "available_qualities": list(ordered.keys()),  # highest first
            "verified_qualities": sorted(validated.keys(), key=lambda s: int(s[:-1]), reverse=True),
        }


if __name__ == "__main__":
    engine = PimpBunnyEngine(headless=True)
    test_url = "https://pimpbunny.com/the-nanny-s-secret_v1/"
    print(json.dumps(asyncio.run(engine.probe_video(test_url)), indent=4))