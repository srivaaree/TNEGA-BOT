# scraper.py
"""
Playwright-based scraper for TN e-District certificate/status check.
Function: query_tnedistrict_status(app_no: str) -> dict

Notes:
- You must run `python -m playwright install` once after installing deps.
- Some CSS selectors are placeholders; test and update them based on the actual page HTML.
- If page requires captcha/reCAPTCHA, function returns {"status":"captcha_required", "admin_url": "...", "screenshot": "<path>"}
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional

from playwright.async_api import async_playwright

ROOT = Path(__file__).parent
SCREENSHOT_DIR = ROOT / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


async def _query_playwright(app_no: str, headless: bool = True, timeout: int = 20000) -> Dict[str, Any]:
    """
    Returns a dictionary:
    {
       "status": "pending"|"approved"|"rejected"|"no_record"|"captcha_required"|"error",
       "data": {...parsed fields...},
       "raw_html": "<html...>",
       "admin_url": "<prefilled url if captcha>",
       "screenshot": "<path if present>"
    }
    """
    url_verify_page = "https://tnedistrict.tn.gov.in/tneda/VerifyCerti.xhtml"
    result: Dict[str, Any] = {"status": "error", "data": {}, "raw_html": ""}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # 1) Load verify page
            await page.goto(url_verify_page, timeout=timeout)

            # 2) OPTIONAL: if there is a top-search bar that accepts application number,
            # fill it. This is based on your screenshots: top-right 'acknowledgement no' field.
            # Replace selector below with actual input selector (inspect in browser devtools).
            top_search_selector = "input[placeholder='acknowledgement no'], input[name='ackNo'], #ackNo"
            try:
                # try multiple selectors
                await page.fill(top_search_selector, app_no, timeout=2000)
            except Exception:
                # fallback: try the main certificate number field on the page
                main_cert_selector = "input[name='certificateNumber'], input#certificateNumber"
                try:
                    await page.fill(main_cert_selector, app_no, timeout=2000)
                except Exception:
                    # not fatal, continue to navigate manually
                    pass

            # 3) click the search button (update selector if needed)
            # common candidate selectors:
            search_btn_selectors = [
                "button[type='submit']",
                "button#searchBtn",
                "button:has-text('Search')",
                "input[type='submit']"
            ]
            clicked = False
            for s in search_btn_selectors:
                try:
                    await page.click(s, timeout=1500)
                    clicked = True
                    break
                except Exception:
                    pass

            # if not clicked, attempt to press Enter in focused element
            if not clicked:
                await page.keyboard.press("Enter")

            # Wait for navigation or results render
            await page.wait_for_timeout(1200)
            await page.wait_for_load_state("networkidle", timeout=8000)

            # 4) Look for captcha images or recaptcha markers
            # If site shows captcha image/field, return captcha_required
            # placeholder checks:
            captcha_img_selectors = [
                "img[src*='captcha']",
                "img[id*='captcha']",
                "div.g-recaptcha",
                "iframe[src*='recaptcha']"
            ]
            for sel in captcha_img_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        screenshot_path = str(SCREENSHOT_DIR / f"captcha_{app_no}_{int(time.time())}.png")
                        await page.screenshot(path=screenshot_path, full_page=True)
                        admin_url = page.url  # admin can open this URL and act
                        return {
                            "status": "captcha_required",
                            "admin_url": admin_url,
                            "screenshot": screenshot_path,
                            "raw_html": await page.content()
                        }
                except Exception:
                    continue

            # 5) Parse result content
            html = await page.content()
            result["raw_html"] = html

            # Attempt to extract some fields via common labels from the screenshots
            # Replace these selectors with the page's actual structure (inspect and adapt)
            def _text(sel):
                try:
                    node = page.locator(sel)
                    if node:
                        return (await node.all_inner_texts())[0].strip()
                except Exception:
                    return ""

            # Some candidate selectors/heuristics:
            # The status box often contains "Application Number" and "Status"
            # We'll search for text nodes containing "Application Number" and read the sibling.

            # Minimal parsing: look for "Application Number" label and the following text
            app_no_text = ""
            applicant_name = ""
            status_text = ""
            remarks = ""
            # Try a few approaches
            try:
                # approach A: find by label text using xpath
                appno_xpath = "//th[contains(translate(., 'APPLICATION NUMBER', 'application number'), 'application number')]/following-sibling::td"
                app_nodes = await page.query_selector_all("xpath=" + appno_xpath)
                if app_nodes:
                    app_no_text = (await app_nodes[0].inner_text()).strip()
            except Exception:
                pass

            # fallback generic search in page text
            page_text = await page.inner_text("body")
            if not app_no_text and app_no in page_text:
                app_no_text = app_no

            # status - try to find "Status" label
            try:
                status_xpath = "//td[contains(., 'Status') or contains(., 'Application Approved') or contains(., 'Application Rejected')]/following-sibling::td"
                nodes = await page.query_selector_all("xpath=" + status_xpath)
                if nodes:
                    status_text = (await nodes[0].inner_text()).strip()
            except Exception:
                pass

            # quick heuristics on page_text
            lowered = page_text.lower()
            if "application approved" in lowered or "certificate approved" in lowered or "approved" in lowered:
                status_text = "Application approved"
                status = "approved"
            elif "application rejected" in lowered or "rejected" in lowered:
                status_text = "Application rejected"
                status = "rejected"
            elif "no record" in lowered or "record not found" in lowered or "no data found" in lowered:
                status_text = "no_record"
                status = "no_record"
            elif "pending" in lowered or "in progress" in lowered:
                status_text = "pending"
                status = "pending"
            else:
                # ambiguous - set to ambiguous and include raw html
                status_text = status_text or "ambiguous"
                status = "ambiguous"

            # extract applicant name using heuristics (try label 'Applicant Name')
            try:
                name_xpath = "//th[contains(translate(., 'Applicant Name', 'applicant name'),'applicant name')]/following-sibling::td"
                n_nodes = await page.query_selector_all("xpath=" + name_xpath)
                if n_nodes:
                    applicant_name = (await n_nodes[0].inner_text()).strip()
            except Exception:
                applicant_name = ""

            # extract remarks if present
            try:
                rem_xpath = "//th[contains(translate(., 'Remarks', 'remarks'),'remarks')]/following-sibling::td"
                r_nodes = await page.query_selector_all("xpath=" + rem_xpath)
                if r_nodes:
                    remarks = (await r_nodes[0].inner_text()).strip()
            except Exception:
                remarks = ""

            result["status"] = status
            result["data"] = {
                "application_number": app_no_text or app_no,
                "applicant_name": applicant_name,
                "status_text": status_text,
                "remarks": remarks,
                "page_url": page.url,
            }
            await browser.close()
            return result

        except Exception as exc:
            # on failure, capture screenshot and return error info
            try:
                path = str(SCREENSHOT_DIR / f"error_{app_no}_{int(time.time())}.png")
                await page.screenshot(path=path, full_page=True)
                return {"status": "error", "error": str(exc), "screenshot": path, "raw_html": await page.content()}
            except Exception:
                return {"status": "error", "error": str(exc)}
        finally:
            try:
                await context.close()
            except Exception:
                pass


def query_tnedistrict_status_sync(app_no: str, headless: bool = True, timeout: int = 20000) -> Dict[str, Any]:
    return asyncio.run(_query_playwright(app_no=app_no, headless=headless, timeout=timeout))
