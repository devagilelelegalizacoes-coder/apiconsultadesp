import os
import sys
import random
import asyncio
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Page, BrowserContext
try:
    from playwright_stealth import stealth_async
except ImportError:
    from playwright_stealth import Stealth
    async def stealth_async(page):
        await Stealth().apply_stealth_async(page)
from dotenv import load_dotenv

load_dotenv()

SUPPORTED_BROWSERS = ("chromium", "firefox")

CHROMIUM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
FIREFOX_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"


def normalize_browser(name: Optional[str], default: str = "chromium") -> str:
    """Normalizes a browser name (accepts 'chrome'/'google' as chromium)."""
    name = (name or "").strip().lower()
    if name in ("chrome", "google", "google-chrome"):
        name = "chromium"
    return name if name in SUPPORTED_BROWSERS else default


class BaseScraper:
    # Subclasses can override (e.g. Bradesco -> firefox). The env var BROWSER
    # sets the global default; the constructor argument wins over everything.
    default_browser: Optional[str] = None

    def __init__(self, use_proxy: bool = True, browser_type: Optional[str] = None):
        self.browser_type = normalize_browser(
            browser_type or self.default_browser or os.getenv("BROWSER")
        )
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

    async def init_browser(self, use_stealth: bool = True):
        if not self.playwright:
            self.playwright = await async_playwright().start()
            
        proxy = self._get_proxy_config()
        
        # Determina o modo headless: True se em produção/Linux, False se configurado explicitamente
        headless_env = os.getenv("HEADLESS", "true").lower() == "true"
        
        is_firefox = self.browser_type == "firefox"
        print(f"[*] [BaseScraper] Launching {self.browser_type} (headless={headless_env})")

        if is_firefox:
            # Chromium flags are invalid for Firefox; it needs no sandbox flags.
            self.browser = await self.playwright.firefox.launch(
                headless=headless_env,
                proxy=proxy,
            )
            user_agent = FIREFOX_USER_AGENT
        else:
            self.browser = await self.playwright.chromium.launch(
                headless=headless_env,
                proxy=proxy,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--ozone-platform=headless",
                    "--disable-software-rasterizer"
                ]
            )
            user_agent = CHROMIUM_USER_AGENT

        self.context = await self.browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            device_scale_factor=1,
        )

        # Apply stealth plugin (it targets Chromium only, so skip it on Firefox)
        page = await self.context.new_page()
        if use_stealth and not is_firefox:
            try:
                await stealth_async(page)
            except Exception as e:
                print(f"[!] [BaseScraper] Failed to apply stealth: {e}")
        
        # Add page console logging and network error listeners for debugging production server issues
        def safe_print_console(msg):
            try:
                print(f"[*] [Browser Console] {msg.type.upper()}: {msg.text}")
            except UnicodeEncodeError:
                try:
                    encoding = sys.stdout.encoding or 'utf-8'
                    safe_text = msg.text.encode(encoding, errors='backslashreplace').decode(encoding)
                    print(f"[*] [Browser Console] {msg.type.upper()}: {safe_text}")
                except:
                    pass
            except:
                pass

        def safe_print_request_failed(req):
            try:
                failure = req.failure
                err_text = "Unknown error"
                if failure:
                    if isinstance(failure, str):
                        err_text = failure
                    elif hasattr(failure, "error_text"):
                        err_text = failure.error_text
                    elif isinstance(failure, dict) and "errorText" in failure:
                        err_text = failure["errorText"]
                    else:
                        err_text = str(failure)
                print(f"[!] [Browser Net Error] {req.url}: {err_text}")
            except Exception:
                pass

        page.on("console", safe_print_console)
        page.on("pageerror", lambda err: print(f"[!] [Browser PageError] {err}"))
        page.on("requestfailed", safe_print_request_failed)
        
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
