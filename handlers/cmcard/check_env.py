# check_env.py
import os, sys, ssl
from shutil import which
from subprocess import Popen, PIPE

def run_cmd(cmd):
    try:
        p = Popen(cmd, stdout=PIPE, stderr=PIPE, shell=True, text=True)
        out, err = p.communicate(timeout=10)
        return out.strip() or err.strip()
    except Exception as e:
        return str(e)

print("=== ENV CHECK for CMCHIS Bot ===")
print("Python:", sys.version.splitlines()[0])
print("OpenSSL:", ssl.OPENSSL_VERSION)
print("Chrome (which):", which("chrome") or which("google-chrome") or which("chrome.exe"))
print("Chromedriver (which):", which("chromedriver") or which("chromedriver.exe"))
print("Chromedriver version (if in PATH):", run_cmd("chromedriver --version"))
print("Installed packages (pip freeze snippet):")
print(run_cmd("pip show selenium || true"))
print(run_cmd("pip show webdriver-manager || true"))

print("\n— Environment variables —")
for k in [
    "8488603942:AAF710TLBOxfNURLJk5-fCyziJ3Wh9VEvF8",
    "1538155602",
    "rzp_live_RjAPUQRIP0Lgn3",
    "jeKY9m8u3sIAk3Pi7uBogfQi",
    "C:/Users/lenovo/Downloads/cmcard/cmchis_output"
]:
    print(k, "=", os.getenv(k, "(missing)"))

print("\n— Quick HTTP test to CMCHIS site —")
try:
    import requests
    r = requests.get("https://claim.cmchistn.com/payer/payermemberpolicyinfodetails.aspx", timeout=10)
    print("CMCHIS HTTP status:", r.status_code)
except Exception as e:
    print("CMCHIS request failed:", e)

print("\nIf selenium fails, run your scraper debug script and check the saved screenshots/html artifacts.")
