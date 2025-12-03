# debug_generate_check.py  (robust input finder + diagnostic)
import sys, time, os, traceback
from pathlib import Path

from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

OUT = Path("./debug_output")
OUT.mkdir(exist_ok=True)

CMCHIS_URL = "https://claim.cmchistn.com/payer/payermemberpolicyinfodetails.aspx"

def start_driver(headless=False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--ignore-certificate-errors")
    chromedriver_path = ChromeDriverManager().install()
    service = Service(chromedriver_path)
    driver = webdriver.Chrome(service=service, options=opts)
    driver.implicitly_wait(2)
    return driver

def save_artifacts(driver, prefix):
    ts = int(time.time())
    ss = OUT / f"{prefix}_{ts}.png"
    html = OUT / f"{prefix}_{ts}.html"
    try:
        driver.save_screenshot(str(ss))
    except Exception as e:
        print("screenshot failed:", e)
    try:
        with open(html, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception as e:
        print("html save failed:", e)
    print("Saved:", ss, html)
    return ss, html

# ---------------- New helper ----------------
def describe_input(driver, el):
    """Return a dict of human-friendly descriptors for an input element."""
    try:
        attrs = driver.execute_script("""
            var el = arguments[0];
            return {
                id: el.id || '',
                name: el.name || '',
                placeholder: el.placeholder || '',
                aria: el.getAttribute('aria-label') || '',
                type: el.getAttribute('type') || '',
                value: el.value || ''
            };
        """, el)
    except Exception:
        attrs = {"id":"", "name":"", "placeholder":"", "aria":"", "type":"", "value":""}
    # try to get label text (label[for=id] or nearest preceding label)
    label_text = ""
    try:
        label_text = driver.execute_script("""
            var el = arguments[0];
            var lab = null;
            if (el.id) lab = document.querySelector("label[for='" + el.id + "']");
            if (!lab) {
                // try nearest preceding label
                var p = el;
                while (p = p.previousElementSibling) {
                    if (p.tagName && p.tagName.toLowerCase() === 'label') { lab = p; break; }
                }
            }
            if (!lab) {
                // try parent label
                var par = el.parentElement;
                while (par) {
                    if (par.tagName && par.tagName.toLowerCase() === 'label') { lab = par; break; }
                    par = par.parentElement;
                }
            }
            return lab ? lab.innerText || lab.textContent : '';
        """, el) or ""
    except Exception:
        label_text = ""
    # aggregate
    attrs["label"] = (label_text or "").strip()
    return attrs

def find_ration_input(driver):
    """
    Heuristic: iterate visible text inputs and pick one with label/name/placeholder matching ration hints.
    Avoid fields that contain URN or '22' or 'URN No'.
    """
    inputs = []
    try:
        candidates = driver.find_elements(By.XPATH, "//input[not(@type) or @type='text' or @type='search' or @type='tel']")
    except Exception:
        candidates = []
    for el in candidates:
        try:
            desc = describe_input(driver, el)
            # skip hidden or disabled
            if el.get_attribute("disabled") or not el.is_displayed():
                continue
            # create a flat string to search
            needle = " ".join([desc.get("label",""), desc.get("placeholder",""), desc.get("aria",""), desc.get("name",""), desc.get("id","")]).lower()
            inputs.append((el, desc, needle))
        except Exception:
            continue

    # Prioritize candidates that explicitly mention 'ration' or 'ration card' or 'card no'
    best = None
    for el, desc, needle in inputs:
        if "ration" in needle or "ration card" in needle or "ration no" in needle or "ration number" in needle or "card number" in needle:
            # also avoid URN labeled fields
            if "urn" not in needle and "22" not in needle and "urn no" not in needle:
                best = (el, desc); break

    # if none matched, try to avoid URN fields (URN fields often ask 22 digit)
    if not best:
        for el, desc, needle in inputs:
            if "urn" in needle or "22" in needle or "urn no" in needle:
                continue
            # prefer placeholder or label with 'card' and numeric length hints maybe 12
            best = (el, desc)
            break

    # fallback to first visible input
    if not best and inputs:
        best = (inputs[0][0], inputs[0][1])

    return best  # (element, desc) or None

# ---------------- Diagnostic flow ----------------
def try_find_generate(driver):
    candidates_xpaths = [
        "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'generate e-card')]",
        "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'generate e card')]",
        "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'generate') and contains(.,'card')]",
        "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'generate')]",
        "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'generate e-card')]",
        "//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'generate')]"
    ]
    for xp in candidates_xpaths:
        try:
            el = driver.find_element(By.XPATH, xp)
            txt = (el.text or "").strip()
            return True, xp, txt
        except Exception:
            continue
    try:
        links = driver.find_elements(By.TAG_NAME, "a")
        for a in links:
            href = (a.get_attribute("href") or "").lower()
            if "ecard" in href or "e-card" in href:
                return True, "href_ecard", href
    except Exception:
        pass
    return False, None, None

def main(ration):
    driver = start_driver(headless=False)
    try:
        driver.get(CMCHIS_URL)
        time.sleep(1.0)

        # find ration input robustly
        found = find_ration_input(driver)
        if not found:
            print("Could not find any suitable input field. Saving artifacts.")
            save_artifacts(driver, "no_input")
            return
        el, desc = found
        print("Selected input descriptors:", desc)
        # try to focus/clear/set value
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.25)
            try:
                el.clear()
            except Exception:
                pass
            try:
                el.send_keys(ration)
            except Exception:
                driver.execute_script("arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input'));", el, ration)
        except Exception as e:
            print("Failed to fill input:", e)

        # press search: try common search buttons
        clicked = False
        try_btn_xps = [
            "//input[@type='image']",
            "//button[contains(@class,'search') or contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'search')]",
            "//input[@type='submit']",
            "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'search') or contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'find')]",
            "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'submit')]"
        ]
        for xp in try_btn_xps:
            try:
                btn = driver.find_element(By.XPATH, xp)
                driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                time.sleep(0.3)
                try:
                    btn.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", btn)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            try:
                el.send_keys("\n")
                clicked = True
            except Exception:
                pass

        time.sleep(3.0)
        save_artifacts(driver, f"after_search_{ration}")

        # handle unexpected alert (like URN prompt)
        try:
            alert = driver.switch_to.alert
            text = alert.text
            print("ALERT present:", text)
            try:
                alert.accept()
            except Exception:
                try:
                    alert.dismiss()
                except Exception:
                    pass
            save_artifacts(driver, f"alert_{ration}")
        except Exception:
            pass

        found_gen, xp, txt = try_find_generate(driver)
        if found_gen:
            print("FOUND generate button/link! xpath:", xp, "text/href:", txt)
            save_artifacts(driver, f"found_generate_{ration}")
        else:
            print("No generate link found (by heuristics). Saved artifacts.")
            save_artifacts(driver, f"no_generate_{ration}")

    except Exception as e:
        print("Exception during diagnostic:", e)
        traceback.print_exc()
        try:
            save_artifacts(driver, "exception")
        except Exception:
            pass
    finally:
        print("Diagnostic finished. Check debug_output folder.")
        driver.quit()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_generate_check.py <ration>")
        sys.exit(1)
    main(sys.argv[1])
