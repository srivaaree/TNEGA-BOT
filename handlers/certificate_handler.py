from admin import notify_admin, handle_admin_file
import os
import time
from telegram import Update, InputFile
from telegram.ext import ContextTypes
import config
from utils.scraper import query_tnedistrict_status

# Map admin handler
admin_file_handler = handle_admin_file


# ------------------------
# /check command handler
# ------------------------
async def cmd_getcert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text.strip()

    # expect: "/check TN-12345678"
    parts = message.split()
    if len(parts) < 2:
        await update.message.reply_text("❌ Please send like:\n/check TN-2120251031226")
        return

    app_no = parts[1]

    await update.message.reply_text("⏳ Checking status... Please wait...")

    # call scraper
    result = query_tnedistrict_status(app_no, headless=True)

    if result["status"] == "pending":
        await update.message.reply_text("🟡 Status: PENDING\nPlease wait 1–2 days. Approval is in process.")
        return

    if result["status"] == "rejected":
        remarks = result["data"].get("remarks", "No remarks")
        await update.message.reply_text(f"🔴 Status: REJECTED\nRemarks: {remarks}\nVisit VAO with valid documents.")
        return

    if result["status"] == "approved":
        name = result["data"].get("name", "Name not found")
        service = result["data"].get("service", "Service not found")
        await update.message.reply_text(
            f"🟢 Status: APPROVED\n"
            f"Name: {name}\n"
            f"Service: {service}\n\n"
            f"To download certificate, pay ₹10 using the link below:\n{config.PAYMENT_LINK}\n\n"
            f"After payment, type /paid {app_no}"
        )
        return

    if result["status"] == "captcha_required":
        await notify_admin(app_no, update.message.chat_id)
        await update.message.reply_text(
            "⚠️ Certificate requires CAPTCHA verification.\n"
            "Our admin will check manually and upload the certificate soon."
        )
        return

    else:
        await update.message.reply_text("❌ Unable to fetch status. Try again later.")
        return


# ------------------------
# /paid command handler
# ------------------------
async def cmd_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split()
    if len(parts) < 2:
        await update.message.reply_text("❌ Usage: /paid TN-2120251031226")
        return

    app_no = parts[1]

    await update.message.reply_text(
        "⏳ Waiting for admin to upload certificate...\n"
        "You will receive the file here once ready."
    )
