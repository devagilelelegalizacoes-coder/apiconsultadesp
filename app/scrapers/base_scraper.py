import os
import random
import asyncio
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth
from dotenv import load_dotenv

load_dotenv()

class BaseScraper:
    def __init__(self, use_proxy: bool = True):
        self.use_proxy = use_proxy
        self.proxy_server = os.getenv("PROXY_SERVER")
        self.proxy_user = os.getenv("PROXY_USER")
        self.proxy_pass = os.getenv("PROXY_PASS")
        self.playwright = None
        self.browser = None
        self.context: Optional[BrowserContext] = None

    def _get_proxy_config(self) -> Optional[Dict[str, str]]:
        if not self.use_proxy or not self.proxy_server:
            return None
        
        config = {"server": self.proxy_server}
        if self.proxy_user and self.proxy_pass:
            config["username"] = self.proxy_user
            config["password"] = self.proxy_pass
        return config

    async def init_browser(self):
        if not self.playwright:
            self.playwright = await async_playwright().start()
            
        proxy = self._get_proxy_config()
        
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            proxy=proxy,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        
        # TLS/JA3 Impersonation via User-Agent and specific headers
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        
        self.context = await self.browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            device_scale_factor=1,
        )
        
        # Apply stealth plugin
        page = await self.context.new_page()
        await Stealth().apply_stealth_async(page)
        return page

    async def human_delay(self, min_ms: int = 500, max_ms: int = 2000):
        await asyncio.sleep(random.randint(min_ms, max_ms) / 1000)

    async def simulate_interaction(self, page: Page):
        """Simulate mouse movement and scrolling."""
        await page.mouse.move(random.randint(0, 500), random.randint(0, 500))
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await self.human_delay(300, 800)

    async def close(self):
        """Closes browser and stops Playwright correctly."""
        try:
            if self.browser:
                print("[*] [BaseScraper] Closing browser...")
                await self.browser.close()
                self.browser = None
                
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
        except Exception as e:
            print(f"[!] [BaseScraper] Error during closure: {e}")
            self.browser = None
            self.playwright = None
