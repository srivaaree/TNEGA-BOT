# cmchis_bot.py
import os, re, asyncio, logging, csv
from pathlib import Path
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

import razorpay

from scraper import scrape_by_ration

# Config
TOKEN = os.getenv("TELEGRAM_TOKEN")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID") or 0)
SAVE_DIR = Path(os.getenv("SAVE_DIR", "./cmchis_output"))
SAVE_DIR.mkdir(parents=True, exist_ok=True)

RZP_ID = os.getenv("RAZORPAY_KEY_ID")
RZP_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
USE_RAZORPAY = bool(RZP_ID and RZP_SECRET)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("cmchis")

SESSION = {}  # chat_id -> session dict
AUDIT_FILE = SAVE_DIR / "audit.csv"
AUDIT_HEADERS = ["ts_utc","chat_id","ration","action","status","order_id","file_path","note"]

def append_audit(chat_id, ration, action, status="", order_id="", file_path="", note=""):
    try:
        write_header = not AUDIT_FILE.exists()
        with AUDIT_FILE.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=AUDIT_HEADERS)
            if write_header:
                writer.writeheader()
            writer.writerow({
                "ts_utc": datetime.utcnow().isoformat(),
                "chat_id": str(chat_id),
                "ration": ration or "",
                "action": action,
                "status": status,
                "order_id": order_id or "",
                "file_path": file_path or "",
                "note": note or ""
            })
    except Exception:
        log.exception("append_audit failed")

def is_ration(s: str) -> bool:
    return bool(re.fullmatch(r"\d{12}", (s or "").strip()))

def pdf_valid(path):
    try:
        from pathlib import Path
        p = Path(path)
        return p.exists() and p.stat().st_size > 50000  # require >50 KB
    except Exception:
        return False

def owner_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = (update.effective_user.id if update.effective_user else None)
        if OWNER_CHAT_ID and uid != OWNER_CHAT_ID:
            try:
                if update.callback_query:
                    await update.callback_query.answer("Access restricted.")
                elif update.message:
                    await update.message.reply_text("Access restricted.")
            except Exception:
                pass
            return
        return await func(update, context)
    return wrapper

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "வணக்கம் 👋\nHello E Sevaiyaa - CMCHIS\nSend 12-digit ration card number."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send 12-digit ration card number to check CMCHIS e-card.")

async def handle_ration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    if not is_ration(text):
        return await update.message.reply_text("❗ Send only 12-digit ration card number.")
    ration = text
    await update.message.reply_text("⏳ Checking the site for details...")

    append_audit(chat_id, ration, "check_started")
    outdir = SAVE_DIR / ration
    outdir.mkdir(parents=True, exist_ok=True)
    pdf_path = outdir / f"ecard_{ration}.pdf"

    # call blocking scraper in thread
    res = await asyncio.to_thread(scrape_by_ration, ration, str(pdf_path), True)

    # if scraper returned an error and no detection, show friendly no-card
    if res.get("error") and not res.get("has_generate") and not res.get("has_card"):
        append_audit(chat_id, ration, "check_failed", status=res.get("error"))
        # send friendly Tamil no-card + enrollment pdf if present
        form_candidates = [Path("assets/TNCMCHIS_Enrolment_Form.pdf"), Path("assets/EnrolmentForm2024 -TNCMHEALTHINSURANACE.pdf"), Path("TNCMCHIS_Enrolment_Form.pdf")]
        form = next((f for f in form_candidates if f.exists()), None)
        no_card_text = ("❌ இந்த ரேஷன் அட்டைக்கு பதிவில்லை அல்லது தளம் பதில் தரவில்லை.\n\n"
                        "👉 புதிய அட்டை பெற: படிவத்தை பூர்த்தி செய்து VAO-இல் சேர்க்கவும். (படிவம் 24 மணி முந்தையதாக நீக்கப்படும்).")
        if form:
            await update.message.reply_document(InputFile(str(form)), caption=no_card_text)
        else:
            await update.message.reply_text(no_card_text)
        return

    # Decision: use has_generate as truth for card found
    if res.get("has_generate"):
        # card found and generate link present
        fields = res.get("fields", {})
        name = fields.get("Card Holder Name", "") or next(iter(fields.values()), "N/A")
        urn = fields.get("Card Holder URN Number", "") or ""
        remain = fields.get("Remaining Sum Assured", "") or ""
        caption = f"✅ Card Found!\nName: *{name}*\nURN: *{urn}*\nRemaining: *{remain}*"
        # preview screenshot if debug exists
        preview = Path("debug_output") / f"preview_{ration}.png"
        if preview.exists():
            with open(preview, "rb") as f:
                await update.message.reply_photo(f, caption=caption, parse_mode="Markdown")
        else:
            await update.message.reply_text(caption, parse_mode="Markdown")

        kb = []
        # if pdf was created and valid, allow preview/send
        if res.get("pdf") and pdf_valid(res.get("pdf")):
            SESSION[chat_id] = {"ration": ration, "pdf": res.get("pdf"), "fields": fields, "order_id": None}
            kb.append([InlineKeyboardButton("Proceed to Pay ₹10", callback_data="pay")])
            kb.append([InlineKeyboardButton("Preview PDF", callback_data="preview_pdf")])
        else:
            # no PDF yet (maybe generated but no chrome) -> allow regen or owner manual flow
            SESSION[chat_id] = {"ration": ration, "pdf": None, "fields": fields, "order_id": None}
            kb.append([InlineKeyboardButton("🔁 Generate e-Card PDF", callback_data="regen_pdf")])
            kb.append([InlineKeyboardButton("Contact Support", callback_data="support")])

        await update.message.reply_text("Is this your CMCHIS card?", reply_markup=InlineKeyboardMarkup(kb))
        append_audit(chat_id, ration, "check_completed", status="found")
        return

    # If has_card but no generate -> treat as NO CARD for our flow (show how to enroll)
    append_audit(chat_id, ration, "check_completed", status="no_generate")
    form_candidates = [Path("assets/TNCMCHIS_Enrolment_Form.pdf"), Path("assets/EnrolmentForm2024 -TNCMHEALTHINSURANACE.pdf"), Path("TNCMCHIS_Enrolment_Form.pdf")]
    form = next((f for f in form_candidates if f.exists()), None)
    no_card_text = ("❌ Generate e-Card option not present. This means you do not have a usable e-Card yet.\n\n"
                    "👉 How to enroll:\n1) Print & fill enrollment form.\n2) Get VAO signature & submit at District Collectorate / CMCHIS camp.\n3) After 10–20 days re-check here.")
    if form:
        await update.message.reply_document(InputFile(str(form)), caption=no_card_text)
    else:
        await update.message.reply_text(no_card_text)
    return

async def on_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    chat_id = q.message.chat.id
    s = SESSION.get(chat_id, {})

    if data == "regen_pdf":
        ration = s.get("ration")
        if not ration:
            return await q.edit_message_text("Session expired.")
        await q.edit_message_text("⏳ Generating e-Card PDF (this can take ~10–30s)...")
        outdir = SAVE_DIR / ration
        outdir.mkdir(parents=True, exist_ok=True)
        pdf_path = outdir / f"ecard_{ration}.pdf"
        res = await asyncio.to_thread(scrape_by_ration, ration, str(pdf_path), True)
        if res.get("pdf") and pdf_valid(res.get("pdf")):
            s["pdf"] = res.get("pdf")
            append_audit(chat_id, ration, "pdf_generated", status="ok", file_path=res.get("pdf"))
            await q.edit_message_text("✅ PDF ready. Proceed to payment.")
            await context.bot.send_document(chat_id, InputFile(res["pdf"], filename=f"CMCHIS_{ration}.pdf"))
            kb = [[InlineKeyboardButton("Proceed to Pay ₹10", callback_data="pay")]]
            await context.bot.send_message(chat_id, "Proceed:", reply_markup=InlineKeyboardMarkup(kb))
        else:
            append_audit(chat_id, ration, "pdf_failed", status=res.get("error","error"))
            await q.edit_message_text("❌ Unable to create valid PDF. Please contact support or try later.")
        return

    if data == "preview_pdf":
        pdf = s.get("pdf")
        if pdf and pdf_valid(pdf):
            await context.bot.send_document(chat_id, InputFile(pdf, filename=f"CMCHIS_{s.get('ration')}.pdf"))
        else:
            await q.edit_message_text("PDF missing or invalid. Use Generate e-Card PDF first.")
        return

    if data == "support":
        await q.edit_message_text("Support: helloesevaiyaa@gmail.com — owner will follow up. (Or use /release <chat_id> if owner)")
        return

    if data == "pay":
        ration = s.get("ration")
        name = (s.get("fields", {}).get("Card Holder Name") or "Beneficiary").split("\n")[0]
        amount_paise = 1000  # ₹10
        payment_link = None
        order_id = None

    # Try Razorpay API first, if configured
    if USE_RAZORPAY:
        try:
            client = razorpay.Client(auth=(RZP_ID.strip(), RZP_SECRET.strip()))
            order = client.order.create({
                "amount": amount_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {"ration": ration}
            })
            order_id = order.get("id")
            # Try create payment link (optional, may fail for some accounts)
            try:
                pl = client.payment_link.create({
                    "amount": amount_paise,
                    "currency": "INR",
                    "accept_partial": False,
                    "description": f"CMCHIS e-Card for {ration}",
                    "customer": {"name": name, "email": "na@example.com"},
                    "notify": {"sms": False, "email": False},
                    "reminder_enable": False,
                    "notes": {"ration": ration, "order_id": order_id},
                })
                payment_link = pl.get("short_url") or pl.get("url")
                s["payment_link_id"] = pl.get("id")
            except Exception:
                # payment_link creation not supported -> will verify by order.payments
                payment_link = None
        except Exception:
            log.exception("razorpay create failed; falling back to static link")

    # Fallback to static link if no dynamic link created
    if not payment_link:
        payment_link = os.getenv("STATIC_PAYMENT_LINK", "").strip() or None

    # save session info
    s["order_id"] = order_id
    s["payment_link"] = payment_link
    s["amount_paise"] = amount_paise

    if payment_link:
        text = (f"✅ Details: *{name}* — Ration: *{ration}*\n\n"
                f"💳 Fee: ₹10\n🔗 Payment link: {payment_link}\n\n"
                "After paying tap 'I've Paid ₹10'.")
        kb = [[InlineKeyboardButton("I've Paid ₹10", callback_data="paid")]]
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    else:
        text = ("⚠️ Payment currently unavailable via online gateway.\n"
                "Please contact support: helloesevaiyaa@gmail.com or use manual payment. Owner can manually release PDF.")
        kb = [[InlineKeyboardButton("I've Paid (manual)", callback_data="manual_paid")]]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return

    if data == "refresh_pay":
        ok = await verify_paid(s)
        if ok:
            await q.edit_message_text("✅ Payment verified. Tap 'I've Paid ₹10' again to receive PDF.")
        else:
            await q.edit_message_text("⌛ Payment not found yet. Try again.")
        return

    if data == "paid":
        ok = await verify_paid(s)
        if not ok:
            await q.edit_message_text("❌ Payment not detected yet. Please pay or refresh.")
            return
        # send pdf if valid else regenerate
        if s.get("pdf") and pdf_valid(s.get("pdf")):
            await q.edit_message_text("✅ Payment confirmed. Sending your e-Card PDF now.")
            await context.bot.send_document(chat_id, InputFile(s.get("pdf"), filename=f"CMCHIS_{s.get('ration')}.pdf"))
            append_audit(chat_id, s.get("ration"), "pdf_sent", status="ok", file_path=s.get("pdf"))
            return
        # regenerate once
        await q.edit_message_text("⚠️ PDF missing/invalid. Re-generating...")
        outdir = SAVE_DIR / s.get("ration")
        outdir.mkdir(parents=True, exist_ok=True)
        pdf_path = outdir / f"ecard_{s.get('ration')}.pdf"
        res = await asyncio.to_thread(scrape_by_ration, s.get("ration"), str(pdf_path), True)
        if res.get("pdf") and pdf_valid(res.get("pdf")):
            s["pdf"] = res.get("pdf")
            await context.bot.send_document(chat_id, InputFile(res["pdf"], filename=f"CMCHIS_{s.get('ration')}.pdf"))
            append_audit(chat_id, s.get("ration"), "pdf_regen_sent", status="ok", file_path=res.get("pdf"))
            return
        await context.bot.send_message(chat_id, "❌ Unable to generate PDF. Support will follow up.")
        append_audit(chat_id, s.get("ration"), "pdf_regen_failed", status=res.get("error","error"))
        return

async def verify_paid(session: dict) -> bool:
    """Verify payment by checking order.payments via Razorpay API.
       Returns True only when payment with expected amount is captured/authorized."""
    if not USE_RAZORPAY:
        return False
    try:
        client = razorpay.Client(auth=(RZP_ID.strip(), RZP_SECRET.strip()))
        order_id = session.get("order_id")
        if order_id:
            try:
                resp = client.order.payments(order_id)  # returns dict with 'items'
                items = resp.get("items", []) if isinstance(resp, dict) else resp
                for p in (items or []):
                    status = p.get("status")
                    amt = int(p.get("amount", 0))
                    if status in ("captured", "authorized") and amt == int(session.get("amount_paise", 1000)):
                        return True
            except Exception:
                log.exception("verify_paid: order.payments failed")
        # If using payment_link id (pl_id) you may check link payments via REST API endpoint,
        # but older SDKs may not support client.payment_link.payments(). So prefer order.payments flow.
        return False
    except Exception:
        log.exception("verify_paid error")
        return False

@owner_only
async def release_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = (update.message.text or "").split()
    if len(parts) < 2:
        return await update.message.reply_text("Usage: /release <chat_id>")
    try:
        target = int(parts[1])
    except:
        return await update.message.reply_text("Invalid chat_id.")
    s = SESSION.get(target)
    if not s:
        return await update.message.reply_text("No session for that chat_id.")
    pdf = s.get("pdf")
    if pdf and pdf_valid(pdf):
        await context.bot.send_document(target, InputFile(pdf, filename=f"CMCHIS_{s.get('ration')}.pdf"))
        append_audit(target, s.get("ration"), "manual_release", status="ok", file_path=pdf)
        return await update.message.reply_text("Released.")
    return await update.message.reply_text("No valid PDF to release.")

async def hourly_cleanup_task():
    while True:
        try:
            cutoff = datetime.utcnow().timestamp() - (24*3600)
            for d in SAVE_DIR.iterdir():
                if d.is_dir():
                    for f in d.iterdir():
                        try:
                            if f.stat().st_mtime < cutoff:
                                f.unlink()
                        except Exception:
                            pass
                    try:
                        if not any(d.iterdir()):
                            d.rmdir()
                    except Exception:
                        pass
        except Exception:
            log.exception("cleanup error")
        await asyncio.sleep(3600)

async def on_startup(app):
    # start background cleanup
    app.create_task(hourly_cleanup_task())

def main():
    if not TOKEN:
        print("Missing TELEGRAM_TOKEN in .env")
        return
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("release", release_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_ration))
    app.add_handler(CallbackQueryHandler(on_buttons))
    app.post_init = on_startup

    log.info("CMCHIS bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
