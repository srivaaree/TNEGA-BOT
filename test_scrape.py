from utils.scraper import query_tnedistrict_status
import json
app_no = "TN-2120251031226"
print("Running debug scrape for", app_no)
res = query_tnedistrict_status(app_no, headless=False, timeout_ms=60000)
print(json.dumps(res, indent=2, ensure_ascii=False))
