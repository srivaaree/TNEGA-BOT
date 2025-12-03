# scraper.py
# Robust Selenium-based scraper for CMCHIS site.
# Returns dict: { has_card, has_generate, fields, pdf (path) , preview_img (optional), error }

import time, traceback, base64, os
from pathlib import Path
from typing import Dict, Any
import requests
from bs4 import BeautifulSoup

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

DEBUG_DIR = Path("debug_output")
DEBUG_DIR.mkdir(exist_ok=True)

CMCHIS_URL = "https://claim.cmchistn.com/payer/payermemberpolicyinfodetails.aspx"

def _write_debug(name: str, html: str | None = None, img_bytes: bytes | None = None):
    try:
        if html is not None:
            p = DEBUG_DIR / f"{name}.html"
            p.write_text(html, encoding="utf-8")
        if img_bytes is not None:
            p2 = DEBUG_DIR / f"{name}.png"
            p2.write_bytes(img_bytes)
    except Exception:
        pass

def _start_driver(headless=True):
    opts = Options()
    # Use new headless mode where available
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-infobars")
    # Chrome binary detection: let chromedriver find chrome from registry
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(45)
    return driver

def _print_pdf_via_cdp(driver, out_pdf_path: str) -> bool:
    """Use Chrome DevTools Protocol to print page to PDF (returns True on success)."""
    try:
        res = driver.execute_cdp_cmd("Page.printToPDF", {
            "printBackground": True,
            "preferCSSPageSize": True,
            "paperWidth": 8.27,
            "paperHeight": 11.69,
        })
        data = res.get("data")
        if not data:
            return False
        pdf_bytes = base64.b64decode(data)
        Path(out_pdf_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_pdf_path).write_bytes(pdf_bytes)
        return True
    except Exception:
        return False

def _requests_quick_check(ration: str) -> Dict[str, Any]:
    """Lightweight HTML check to see if the page contains 'Generate e-card' (fast path)."""
    try:
        r = requests.get(CMCHIS_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        html = r.text
        _write_debug(f"req_start_{ration}", html=html)
        lower = html.lower()
        has_generate = ("generate e-card" in lower) or ("generate e card" in lower)
        has_ration = ration in lower
        # quick parse attempt
        soup = BeautifulSoup(html, "lxml")
        fields = {}
        for tr in soup.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) >= 2:
                k = tds[0].get_text(strip=True)
                v = tds[1].get_text(strip=True)
                if k:
                    fields[k] = v
        return {"ok": True, "has_generate": has_generate, "has_card": has_generate or has_ration, "fields": fields, "html": html}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _selenium_flow(ration: str, out_pdf_path: str, headless=True) -> Dict[str, Any]:
    """
    Full selenium flow:
     - open page
     - locate ration input field heuristically (prefer 'ration' in id/name/placeholder)
     - submit and check result
     - detect 'Generate e-card' presence
     - if present, attempt printToPDF and produce pdf
    """
    OUT = {"has_card": False, "has_generate": False, "fields": {}, "pdf": None, "preview_img": None}
    driver = None
    try:
        driver = _start_driver(headless=headless)
        driver.get(CMCHIS_URL)
        time.sleep(0.8)

        inputs = driver.find_elements(By.TAG_NAME, "input")
        ration_input = None
        for inp in inputs:
            try:
                attrs = " ".join(filter(None, [inp.get_attribute("id") or "", inp.get_attribute("name") or "", inp.get_attribute("placeholder") or ""])).lower()
                if "ration" in attrs:
                    ration_input = inp
                    break
            except Exception:
                continue
        if not ration_input and inputs:
            # fallback to first input
            ration_input = inputs[0]

        if not ration_input:
            OUT["error"] = "NO_INPUT_FIELD"
            return OUT

        try:
            ration_input.clear()
        except Exception:
            pass
        ration_input.send_keys(ration)
        time.sleep(0.5)

        # click search heuristics
        clicked = False
        try:
            # guess clickable search buttons
            candidates = driver.find_elements(By.XPATH, "//input[@type='submit' or @type='button' or @type='image' or contains(@value,'Search') or contains(@class,'search')]")
            for b in candidates:
                try:
                    if b.is_displayed():
                        b.click()
                        clicked = True
                        break
                except Exception:
                    continue
        except Exception:
            pass

        if not clicked:
            try:
                form = driver.find_element(By.TAG_NAME, "form")
                form.submit()
            except Exception:
                pass

        time.sleep(1.0)
        page_html = driver.page_source
        _write_debug(f"selenium_after_search_{ration}", html=page_html)

        # take quick screenshot preview
        try:
            png = driver.get_screenshot_as_png()
            _write_debug(f"selenium_preview_{ration}", img_bytes=png)
            preview_path = DEBUG_DIR / f"preview_{ration}.png"
            preview_path.write_bytes(png)
            OUT["preview_img"] = str(preview_path)
        except Exception:
            pass

        # extract table fields heuristically
        fields = {}
        try:
            rows = driver.find_elements(By.XPATH, "//tr")
            for tr in rows:
                try:
                    tds = tr.find_elements(By.TAG_NAME, "td")
                    if len(tds) >= 2:
                        k = tds[0].text.strip()
                        v = tds[1].text.strip()
                        if k:
                            fields[k] = v
                except Exception:
                    continue
        except Exception:
            pass
        OUT["fields"] = fields

        page_lower = page_html.lower()
        if "generate e-card" in page_lower or "generate e card" in page_lower:
            OUT["has_generate"] = True
            OUT["has_card"] = True
        elif ration in page_lower:
            OUT["has_card"] = True

        # If generate present, attempt PDF
        if OUT["has_generate"]:
            for attempt in range(1, 4):
                ok = _print_pdf_via_cdp(driver, out_pdf_path)
                if ok and Path(out_pdf_path).exists() and Path(out_pdf_path).stat().st_size > 15000:
                    OUT["pdf"] = out_pdf_path
                    break
                time.sleep(0.6 * attempt)
        return OUT

    except Exception as e:
        return {"error": "SEL_FAIL", "error_msg": str(e), "trace": traceback.format_exc()}
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass

def scrape_by_ration(ration: str, out_pdf_path: str, headless=True) -> Dict[str, Any]:
    """
    Public function to call from bot.
    Returns dict { has_card, has_generate, fields, pdf (path) or None, error (optional), preview_img (optional) }
    """
    # Quick HTTP check
    rq = _requests_quick_check(ration)
    # If request-level HTML contains generate text, prefer that path
    if rq.get("ok") and rq.get("has_generate"):
        try:
            res = _selenium_flow(ration, out_pdf_path, headless=headless)
            # merge quick parsed fields if selenium missed
            if not res.get("fields"):
                res["fields"] = rq.get("fields", {})
            return res
        except Exception:
            return {"has_card": True, "has_generate": True, "fields": rq.get("fields", {}), "pdf": None, "error": "NO_CHROME_OR_PDF"}
    # Otherwise run selenium to render JS and check
    return _selenium_flow(ration, out_pdf_path, headless=headless)
