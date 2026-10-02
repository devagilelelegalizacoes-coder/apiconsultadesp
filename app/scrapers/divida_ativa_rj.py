import asyncio
from typing import Dict, Any, List, Optional
from app.scrapers.base_scraper import BaseScraper

URL = (
    "https://www.consultadividaativa.rj.gov.br/consultadebitosdividaativarj/servlet/StartCISPage"
    "?PAGEURL=/cisnatural/NatLogon.html&xciParameters.natsession=Consulta_Debitos_DA"
)
TIPO_RENAVAM = "P4"

EXTRACT_TABLE_JS = """() => {
  const header = [...document.querySelectorAll('td.TEXTGRIDCellHeader[id^="TG"]')]
    .filter(td => td.offsetParent !== null).map(td => td.innerText.trim());
  if (!header.includes('Certidão')) return null;
  const rows = [...document.querySelectorAll('tr[id^="TGROW"]')]
    .filter(tr => tr.offsetParent !== null)
    .map(tr => [...tr.children].map(td => td.innerText.trim()))
    .filter(cells => cells[0]);
  return [header, ...rows];
}"""


def _parse_money(v: str) -> float:
    try:
        return float(v.replace("R$", "").replace(".", "").replace(",", ".").strip())
    except (ValueError, AttributeError):
        return 0.0


def _fmt(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class DividaAtivaRJScraper(BaseScraper):
    """Consulta de débitos inscritos em Dívida Ativa (PGE-RJ) por RENAVAM."""

    def _app_frames(self, page):
        return [f for f in page.frames if "/UICRDG/" in f.url and not f.url.endswith("_JLIBS.html")]

    async def _find_table(self, page) -> Optional[List[List[str]]]:
        for f in self._app_frames(page):
            try:
                rows = await f.evaluate(EXTRACT_TABLE_JS)
                if rows:
                    return rows
            except Exception:
                continue
        return None

    async def _visible_text(self, page) -> str:
        texts = []
        for f in self._app_frames(page):
            try:
                texts.append(await f.evaluate(
                    "() => [...document.querySelectorAll('body *')].filter(e => e.offsetParent !== null && e.children.length === 0 && e.tagName !== 'SCRIPT').map(e => e.innerText.trim()).filter(Boolean).join(' | ')"
                ))
            except Exception:
                continue
        return " | ".join(texts)

    async def get_divida_ativa(self, renavam: str) -> Dict[str, Any]:
        renavam = "".join(filter(str.isdigit, str(renavam or "")))
        if not renavam:
            return {"source": "DividaAtivaRJ", "status": "error", "message": "RENAVAM inválido"}

        last_error = ""
        for attempt in range(1, 3):
            page = await self.init_browser(use_stealth=False)
            try:
                print(f"[*] [DividaAtivaRJ] Consultando RENAVAM {renavam} (Tentativa {attempt})")
                await page.goto(URL, wait_until="networkidle", timeout=60000)

                form = None
                for _ in range(20):
                    for f in self._app_frames(page):
                        if await f.locator("#CDYN_31").count():
                            form = f
                            break
                    if form:
                        break
                    await asyncio.sleep(0.5)
                if not form:
                    raise Exception("Formulário de consulta não carregou")

                await form.locator("#CDYN_31").select_option(TIPO_RENAVAM)
                await form.locator("#F_91").wait_for(state="visible", timeout=15000)
                await form.locator("#F_91").fill(renavam)
                await form.locator("#F_91").press("Tab")
                await form.locator("#B_110").click()

                rows = None
                for _ in range(40):
                    await asyncio.sleep(0.5)
                    rows = await self._find_table(page)
                    if rows:
                        break

                if not rows:
                    text = await self._visible_text(page)
                    low = text.lower()
                    if any(k in low for k in ("não foram encontrad", "nao foram encontrad", "não existe", "nenhum débito", "não há débito", "não constam", "não inscrito em dívida ativa")):
                        return {
                            "source": "DividaAtivaRJ", "renavam": renavam, "status": "success",
                            "tem_divida": False, "debitos": [], "total_divida": _fmt(0),
                            "message": "Nada consta em Dívida Ativa",
                        }
                    raise Exception(f"Resultado não reconhecido: {text[:300]}")

                header = [h.lower() for h in rows[0]]
                debitos = []
                for r in rows[1:]:
                    if len(r) != len(header) or not r[0]:
                        continue
                    item = dict(zip(header, r))
                    debitos.append({
                        "certidao": item.get("certidão", ""),
                        "situacao": item.get("situação", ""),
                        "natureza": item.get("natureza", ""),
                        "principal": item.get("principal", ""),
                        "multa": item.get("multa", ""),
                        "mora": item.get("mora", ""),
                        "total_debito": item.get("total do débito", ""),
                        "honorarios": item.get("honorários", ""),
                        "total": item.get("total", ""),
                    })

                total = sum(_parse_money(d["total"]) for d in debitos)
                print(f"[+] [DividaAtivaRJ] {len(debitos)} certidão(ões), total {_fmt(total)}")
                return {
                    "source": "DividaAtivaRJ", "renavam": renavam, "status": "success",
                    "tem_divida": bool(debitos), "debitos": debitos, "total_divida": _fmt(total),
                }
            except Exception as e:
                last_error = str(e)
                print(f"[-] [DividaAtivaRJ] Erro (Tentativa {attempt}): {last_error}")
            finally:
                await self.close()

        return {"source": "DividaAtivaRJ", "renavam": renavam, "status": "error", "message": last_error}
