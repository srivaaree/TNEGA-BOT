# rzp_test.py
import os
from dotenv import load_dotenv
load_dotenv()
import razorpay
RZP_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RZP_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()

if not (RZP_ID and RZP_SECRET):
    print("Missing RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET in .env")
    raise SystemExit(1)

print("Testing Razorpay credentials (creating a tiny test order)...")
try:
    client = razorpay.Client(auth=(RZP_ID, RZP_SECRET))
    test_order = client.order.create({
        "amount": 100,        # ₹1 just to test; nothing will be charged by this call
        "currency": "INR",
        "receipt": "rzp_test_1"
    })
    print("Razorpay auth OK — test order created (cleanup this order in Razorpay dashboard if needed).")
    print("Order id:", test_order.get("id"))
except Exception as e:
    msg = str(e)
    if "Authentication failed" in msg or "BadRequestError" in msg:
        print("Razorpay AUTH failed. Check:
// 1) RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env (no extra spaces, correct live/test keys).
// 2) Are you using live keys on a test account? Try test keys from Razorpay dashboard for dev.")
    else:
        print("Razorpay error:", e)
