import asyncio
import re
import json
from playwright.async_api import async_playwright

class PimpBunnyEngine:
    def __init__(self, headless=True):
        self.headless = headless

    async def get_creator_videos(self, creator_url):
        """
        Scrapes a creator's profile across all pages and returns a list of video URLs.
        Example: https://pimpbunny.com/onlyfans-creators/jak-knife/
        """
        video_links = []
        page_num = 1

        # Ensure base URL is clean for pagination
        base_url = creator_url.split('?')[0].rstrip('/')

        print(f"Starting bulk scrape for: {base_url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page = await context.new_page()

            while True:
                # Construct pagination URL (Page 1 has no number, Page 2+ does)
                if page_num == 1:
                    target_url = f"{base_url}/"
                else:
                    target_url = f"{base_url}/{page_num}/?sort_by=rating"

                print(f"Scraping page {page_num}...")
                response = await page.goto(target_url, wait_until="domcontentloaded")

                # If page 404s or redirects away, we've hit the end
                if response.status == 404:
                    print("Reached end of pagination.")
                    break

                # Update this selector based on PB's actual video grid HTML
                # Looking for standard anchor tags wrapping video thumbnails
                hrefs = await page.evaluate(
                    "Array.from(document.querySelectorAll('.video-list-item a, .item-video a')).map(a => a.href)"
                )

                # Filter out non-video links (like profile links or ads)
                valid_videos = [href for href in hrefs if "/video/" in href or ".html" in href]

                if not valid_videos:
                    print("No more videos found on this page. Exiting loop.")
                    break

                video_links.extend(valid_videos)
                page_num += 1

            await browser.close()

        # Deduplicate while preserving order
        unique_links = list(dict.fromkeys(video_links))
        print(f"Total unique videos found: {len(unique_links)}")
        return unique_links

    async def probe_video(self, video_url):
        """
        Visits a specific video URL and extracts all available qualities and their tokenized direct links.
        Returns a dictionary compatible with your existing downloader.
        """
        print(f"Probing video: {video_url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page = await context.new_page()

            await page.goto(video_url, wait_until="networkidle")

            # 1. Grab cookies for the downloader
            cookies = await context.cookies()
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

            # 2. Extract page HTML to find the tokenized media links
            content = await page.content()

            # Regex to find the direct mp4 links.
            # Matches: https://pimpbunny.com/get_file/.../12345_1080p.mp4/?v-acctoken=...&rnd=...
            # Groups the quality (e.g., '1080p') to map it dynamically.
            pattern = r'(https?://[^\s"\'<>]+_(\d{3,4}p)\.mp4/\?v-acctoken=[^\s"\'<>&]+(?:&|&)rnd=\d+)'
            matches = re.findall(pattern, content)

            qualities_dict = {}
            for full_url, quality in matches:
                # Clean up HTML escaped ampersands
                clean_url = full_url.replace("&", "&")
                qualities_dict[quality] = clean_url

            await browser.close()

            # 3. Format output similarly to your existing PROBE_DATA
            if not qualities_dict:
                return {"error": "No tokenized media links found."}

            probe_data = {
                "referer": video_url,
                "cookies": cookie_str,
                "streams": qualities_dict,
                "available_qualities": list(qualities_dict.keys())
            }

            return probe_data

# Standalone execution for testing in terminal
if __name__ == "__main__":
    engine = PimpBunnyEngine(headless=True)

    # Example: Run the prober directly
    test_url = "https://pimpbunny.com/the-nanny-s-secret_v1/" # Replace with valid PB link
    result = asyncio.run(engine.probe_video(test_url))

    print("\n--- PROBE DATA ---")
    print(json.dumps(result, indent=4))