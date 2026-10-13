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

def chromium_user_agent(version: str) -> str:
    """User-Agent coerente com a versão real do Chromium e com o sistema.

    Um UA com versão diferente da que o navegador informa nos Client Hints
    (navigator.userAgentData / Sec-CH-UA) é um sinal clássico de automação.
    """
    major = (version or "131").split(".")[0]
    platform = "Windows NT 10.0; Win64; x64" if sys.platform == "win32" else "X11; Linux x86_64"
    return f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"


# Corrige o que o modo headless ainda expõe mesmo com o headless novo. Cada
# ajuste só age quando o vazamento existe, para não alterar o modo visível.
CHROMIUM_EVASIONS_JS = r"""
(() => {
  const uad = navigator.userAgentData;
  if (uad && uad.brands.some(b => /Headless/i.test(b.brand))) {
    const fix = list => list.map(b => /Headless/i.test(b.brand) ? {brand: 'Google Chrome', version: b.version} : b);
    const brands = fix(uad.brands);
    const proxy = new Proxy(uad, {
      get(target, prop) {
        if (prop === 'brands') return brands;
        if (prop === 'getHighEntropyValues') {
          return hints => target.getHighEntropyValues(hints).then(v => {
            if (v.brands) v.brands = fix(v.brands);
            if (v.fullVersionList) v.fullVersionList = fix(v.fullVersionList);
            return v;
          });
        }
        const value = Reflect.get(target, prop, target);
        return typeof value === 'function' ? value.bind(target) : value;
      },
    });
    Object.defineProperty(Navigator.prototype, 'userAgentData', {get: () => proxy, configurable: true});
  }
  if (window.Notification && Notification.permission === 'denied') {
    Object.defineProperty(Notification, 'permission', {get: () => 'default', configurable: true});
  }
})();
"""

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
    # Canal do Chromium (ver init_browser); Bradesco usa "chromium".
    chromium_channel: Optional[str] = None

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
            # None/"none": headless-shell padrão do Playwright. "chromium": headless
            # novo (navegador completo), mais difícil de detectar, mas trava o
            # networkidle do SEFAZ IPVA. "chrome": Google Chrome instalado.
            # CHROMIUM_CHANNEL no .env sobrepõe o canal de todos os scrapers.
            channel = (os.getenv("CHROMIUM_CHANNEL") or self.chromium_channel or "").strip().lower()
            channel = None if channel in ("", "none") else channel
            self.browser = await self.playwright.chromium.launch(
                headless=headless_env,
                proxy=proxy,
                channel=channel,
                # Remove a flag que liga navigator.webdriver e a barra de automação
                ignore_default_args=["--enable-automation"],
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    # Sem GPU o WebGL some, o que denuncia automação; o SwiftShader
                    # faz a renderização por software em servidores sem GPU.
                    "--enable-unsafe-swiftshader",
                ]
            )
            user_agent = chromium_user_agent(self.browser.version)

        self.context = await self.browser.new_context(
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            device_scale_factor=1,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        if not is_firefox:
            await self.context.add_init_script(CHROMIUM_EVASIONS_JS)

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
