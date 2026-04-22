import os
import httpx
import asyncio
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

class CaptchaSolver:
    """
    Unified captcha solver supporting Anti-Captcha and 2captcha services.
    Configure via .env:
      CAPTCHA_PROVIDER=2captcha   # or anticaptcha (default)
      CAPTCHA_API_KEY=your_key
    """
    def __init__(self):
        self.api_key = os.getenv("CAPTCHA_API_KEY")
        self.provider = os.getenv("CAPTCHA_PROVIDER", "anticaptcha").lower()

        if self.provider == "2captcha":
            self.submit_url = "https://2captcha.com/in.php"
            self.result_url = "https://2captcha.com/res.php"
            print("[*] [CaptchaSolver] Provider: 2captcha")
        else:
            self.create_url = "https://api.anti-captcha.com/createTask"
            self.result_url_anti = "https://api.anti-captcha.com/getTaskResult"
            print("[*] [CaptchaSolver] Provider: Anti-Captcha")

    # ─────────────────────────── 2captcha helpers ──────────────────────────

    async def _poll_2captcha(self, request_id: str) -> Optional[str]:
        """Polls 2captcha for a captcha result."""
        for _ in range(60):
            await asyncio.sleep(3)
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(self.result_url, params={
                        "key": self.api_key,
                        "action": "get",
                        "id": request_id
                    }, timeout=10)
                    text = resp.text.strip()
                    if text.startswith("OK|"):
                        return text.split("|", 1)[1]
                    if text == "CAPCHA_NOT_READY":
                        continue
                    print(f"[!] 2captcha error: {text}")
                    return None
            except Exception as e:
                print(f"[!] 2captcha polling error: {str(e)}")
        return None

    async def _solve_image_2captcha(self, base64_image: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.submit_url, data={
                    "key": self.api_key,
                    "method": "base64",
                    "body": base64_image,
                    "json": 1
                }, timeout=15)
                data = resp.json()
                if data.get("status") == 1:
                    return await self._poll_2captcha(str(data["request"]))
                print(f"[!] 2captcha submit error: {data}")
        except Exception as e:
            print(f"[!] 2captcha image error: {str(e)}")
        return None

    async def _solve_recaptcha_2captcha(self, sitekey: str, url: str, invisible: bool = False) -> Optional[str]:
        try:
            params = {
                "key": self.api_key,
                "method": "userrecaptcha",
                "googlekey": sitekey,
                "pageurl": url,
                "json": 1
            }
            if invisible:
                params["invisible"] = 1
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.submit_url, data=params, timeout=15)
                data = resp.json()
                if data.get("status") == 1:
                    return await self._poll_2captcha(str(data["request"]))
                print(f"[!] 2captcha recaptcha submit error: {data}")
        except Exception as e:
            print(f"[!] 2captcha recaptcha error: {str(e)}")
        return None

    # ──────────────────────── Anti-Captcha helpers ─────────────────────────

    async def _poll_anticaptcha(self, task_id: int) -> Optional[str]:
        for _ in range(60):
            await asyncio.sleep(2)
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(self.result_url_anti, json={
                        "clientKey": self.api_key,
                        "taskId": task_id
                    }, timeout=10)
                    data = resp.json()
                    if data.get("status") == "ready":
                        solution = data.get("solution", {})
                        return solution.get("text") or solution.get("gRecaptchaResponse")
                    if data.get("errorId"):
                        print(f"[!] Captcha error: {data.get('errorDescription')}")
                        return None
            except Exception as e:
                print(f"[!] Polling error: {str(e)}")
        return None

    async def _solve_image_anticaptcha(self, base64_image: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.create_url, json={
                    "clientKey": self.api_key,
                    "task": {"type": "ImageToTextTask", "body": base64_image}
                }, timeout=10)
                task_id = resp.json().get("taskId")
                if task_id:
                    return await self._poll_anticaptcha(task_id)
        except Exception as e:
            print(f"[!] Create task error (Image): {str(e)}")
        return None

    async def _solve_recaptcha_anticaptcha(self, sitekey: str, url: str, task_type: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.create_url, json={
                    "clientKey": self.api_key,
                    "task": {
                        "type": task_type,
                        "websiteURL": url,
                        "websiteKey": sitekey
                    }
                }, timeout=10)
                task_id = resp.json().get("taskId")
                if task_id:
                    return await self._poll_anticaptcha(task_id)
        except Exception as e:
            print(f"[!] Create task error (ReCaptcha): {str(e)}")
        return None

    # ───────────────────── Public unified interface ─────────────────────────

    async def solve_image_captcha(self, base64_image: str) -> Optional[str]:
        if not self.api_key:
            print("[!] ERROR: CAPTCHA_API_KEY not configured in environment variables!")
            return None
        if self.provider == "2captcha":
            return await self._solve_image_2captcha(base64_image)
        return await self._solve_image_anticaptcha(base64_image)

    async def solve_recaptcha_v2(self, sitekey: str, url: str, task_type: str = "NoCaptchaTaskProxyless") -> Optional[str]:
        if not self.api_key:
            print("[!] ERROR: CAPTCHA_API_KEY not configured in environment variables!")
            return None
        if self.provider == "2captcha":
            invisible = "invisible" in task_type.lower()
            return await self._solve_recaptcha_2captcha(sitekey, url, invisible=invisible)
        return await self._solve_recaptcha_anticaptcha(sitekey, url, task_type)

solver = CaptchaSolver()
