# handlers/status_handler.py
import asyncio
from telegram import Update, InputFile
from telegram.ext import ContextTypes
from utils.scraper import query_tnedistrict_status

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args:
        await update.message.reply_text("Please send: /check <application_number>\nExample: /check TN-2120251111709")
        return

    app_no = args[0].strip()
    info_msg = await update.message.reply_text(f"Checking status for {app_no} ... please wait (may take a few seconds).")

    # run scraper in a thread so we do not block the event loop
    try:
        # first try
        result = await asyncio.to_thread(query_tnedistrict_status, app_no, True, 60000)
    except Exception as e:
        await update.message.reply_text(f"Error while checking (exception): {e}")
        return

    # If ambiguous or error, try one quick retry
    if not result or result.get("status") in ("error", "ambiguous", None):
        try:
            await info_msg.edit_text(f"Retrying check for {app_no} ...")
            result = await asyncio.to_thread(query_tnedistrict_status, app_no, True, 60000)
        except Exception as e:
            await update.message.reply_text(f"Second attempt failed: {e}")
            return

    status = result.get("status", "error")
    data = result.get("data", {})

    # Normal flows
    if status == "pending":
        await update.message.reply_text("Status: PENDING — your application is under review. Please check after 48 hours.")
        return

    if status == "approved":
        text = (
            f"Status: APPROVED ✅\n"
            f"Application: {data.get('application_number', app_no)}\n"
            f"Name: {data.get('applicant_name') or data.get('name') or 'N/A'}\n"
            f"Father: {data.get('father_name') or 'N/A'}\n"
            f"Remarks: {data.get('remarks') or 'N/A'}\n\n"
            f"If you want to download the certificate, use /getcert {app_no}"
        )
        await update.message.reply_text(text)
        return

    if status == "rejected":
        remarks = data.get("remarks", "No remarks provided.")
        await update.message.reply_text(f"Status: REJECTED ❌\nRemarks: {remarks}\nPlease reapply with corrected documents or visit VAO.")
        return

    if status == "no_record":
        await update.message.reply_text("No record found for that application number. Please check the number and try again.")
        return

    if status == "captcha_required":
        # inform user and notify admin later
        await update.message.reply_text("This application requires operator action (captcha). We will notify an operator.")
        return

    # --- Debug / fallback path: show raw info so we can see what happened ---
    raw = result.get("raw_text") or "No raw text captured."
    screenshot = result.get("screenshot")
    await update.message.reply_text("Unexpected result from the checker — showing debug info below. Please share this with the developer if needed.")
    # send short snippet of raw_text (trim to 2000 chars so Telegram accepts)
    snippet = raw if len(raw) <= 1800 else raw[:1800] + "\n\n...[truncated]"
    await update.message.reply_text(f"DEBUG - status: {status}\n\n{snippet}")

    # if a screenshot path is provided, send it
    if screenshot:
        try:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=InputFile(screenshot), caption="Screenshot captured by scraper")
        except Exception:
            # if send_photo fails, just inform path
            await update.message.reply_text(f"Screenshot saved at: {screenshot}")
