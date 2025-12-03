# utils/scraper.py
# Coordinate-click scraper — fills ack input and clicks to the right of it to hit the small search icon.
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Error as PWError
from pathlib import Path
import time, traceback, json

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT_DIR = ROOT / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

VERIFY_PAGE = "https://tnedistrict.tn.gov.in/tneda/VerifyCerti.xhtml"

def query_tnedistrict_status(app_no: str, headless: bool = True, timeout_ms: int = 60000):
    out = {"status":"error","data":{},"debug":{},"raw_text":"","screenshot":"","page_url":""}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless, args=["--no-sandbox"])
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            page = context.new_page()
            page.set_default_navigation_timeout(timeout_ms)

            page.goto(VERIFY_PAGE, wait_until="load", timeout=timeout_ms)
            out["page_url"] = page.url
            page.wait_for_timeout(900)

            # debug frames
            out["debug"]["frames"] = [{"url": f.url, "name": f.name} for f in page.frames]

            # Try to locate the ack input explicitly
            ack_selectors = ["#form1\\:acknumber","input[id*='ack']","input[name*='ack']","input[type='text']"]
            filled = False
            used_selector = None
            for sel in ack_selectors:
                try:
                    page.wait_for_selector(sel, timeout=2500)
                    page.fill(sel, app_no, timeout=2000)
                    used_selector = sel
                    out["debug"].setdefault("fill_attempts", []).append({"selector": sel, "ok": True})
                    filled = True
                    break
                except Exception as e:
                    out["debug"].setdefault("fill_attempts", []).append({"selector": sel, "ok": False, "error": str(e)})

            if not filled:
                # screenshot and return debug
                ss = SCREENSHOT_DIR / f"no_fill_{app_no}_{int(time.time())}.png"
                page.screenshot(path=str(ss), full_page=True)
                out["screenshot"] = str(ss)
                out["raw_text"] = page.content()[:2000]
                out["status"] = "error"
                page.close(); browser.close()
                return out

            # Get bounding box of the used input and click to the right (coordinate-based)
            try:
                handle = page.query_selector(used_selector)
                box = handle.bounding_box()
                if not box:
                    raise Exception("bounding_box returned None")
                # click a bit to the right and vertically centered on the input (offset 30 px to right, adjust if needed)
                click_x = box["x"] + box["width"] + 18
                click_y = box["y"] + box["height"]/2
                # do the click
                page.wait_for_timeout(300)\n                page.mouse.click(click_x, click_y)
                out["debug"]["click_attempts"] = [{"method":"coord_click","x":click_x,"y":click_y,"ok":True}]
                page.wait_for_timeout(1600)
            except Exception as e_coord:
                out["debug"].setdefault("click_attempts", []).append({"method":"coord_click","ok":False,"error":str(e_coord)})
                # fallback: try common small icon selectors
                fallback = ["#form1\\:acksearch","a#form1\\:acksearch","img#form1\\:acksearch","button#form1\\:acksearch","input[type='submit']"]
                clicked = None
                for fsel in fallback:
                    try:
                        page.wait_for_selector(fsel, timeout=1500)
                        page.click(fsel, timeout=2000)
                        clicked = fsel
                        out["debug"].setdefault("click_attempts", []).append({"method":"fallback_selector","selector":fsel,"ok":True})
                        page.wait_for_timeout(1200)
                        break
                    except Exception as efs:
                        out["debug"].setdefault("click_attempts", []).append({"method":"fallback_selector","selector":fsel,"ok":False,"error":str(efs)})
                if not clicked:
                    ss = SCREENSHOT_DIR / f"click_fail_{app_no}_{int(time.time())}.png"
                    page.screenshot(path=str(ss), full_page=True)
                    out["screenshot"] = str(ss)
                    out["raw_text"] = page.content()[:2000]
                    out["status"] = "error"
                    page.close(); browser.close()
                    return out

            # screenshot after click
            ss = SCREENSHOT_DIR / f"afterclick_{app_no}_{int(time.time())}.png"
            page.screenshot(path=str(ss), full_page=True)
            out["screenshot"] = str(ss)

            # read page body
            try:
                body = page.inner_text("body", timeout=3000)
            except Exception:
                body = ""
            out["raw_text"] = (body or "")[:4000]
            lower = (body or "").lower()
            if "captcha" in lower or "enter captcha" in lower or "recaptcha" in lower:
                out["status"] = "captcha_required"
            elif "approved" in lower and ("application" in lower or "certificate" in lower):
                out["status"] = "approved"
            elif "rejected" in lower:
                out["status"] = "rejected"
            elif "pending" in lower or "in progress" in lower:
                out["status"] = "pending"
            elif "no record" in lower or "record not found" in lower:
                out["status"] = "no_record"
            else:
                out["status"] = "filled_but_unknown"

            page.close(); browser.close()
            return out

    except PWTimeout as t:
        out["status"]="error"; out["raw_text"]=f"Timeout: {t}"; return out
    except PWError as e:
        out["status"]="error"; out["raw_text"]=f"Playwright error: {e}"; return out
    except Exception as e:
        out["status"]="error"; out["raw_text"]=f"Exception: {e}\\n{traceback.format_exc()}"; return out



