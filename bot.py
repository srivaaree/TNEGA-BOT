import logging
import os
import json
import time
import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import config
from utils.scraper import query_tnedistrict_status

# ---------- Logging ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TASK_FILE = "tasks.json"
DOWNLOAD_DIR = getattr(config, "DOWNLOAD_DIR", "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ---------- Helpers: tasks storage ----------

def _load_tasks():
    if not os.path.exists(TASK_FILE):
        return {"jobs": []}
    try:
        with open(TASK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "jobs" not in data:
            data["jobs"] = []
        return data
    except Exception as e:
        logger.error("Failed to load tasks.json: %s", e)
        return {"jobs": []}


def _save_tasks(data):
    try:
        with open(TASK_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Failed to save tasks.json: %s", e)


def _create_job(app_no, parsed, user_chat_id):
    data = _load_tasks()
    ts = int(time.time())
    job_id = f"JOB-{ts}"
    job = {
        "job_id": job_id,
        "app_no": app_no,
        "name": parsed.get("applicant_name") or "",
        "father_name": parsed.get("father_name") or "",
        "service": parsed.get("request_for") or "",
        "status_text": parsed.get("status_text") or "",
        "remarks": parsed.get("remarks") or "",
        "gender": parsed.get("gender") or "",
        "date_of_request": parsed.get("date_of_request") or "",
        "user_chat_id": user_chat_id,
        "state": "pending_admin",  # pending_admin | in_progress | done
        "created_at": ts,
    }
    data["jobs"].append(job)
    _save_tasks(data)
    return job


def _find_job(job_id):
    data = _load_tasks()
    for j in data["jobs"]:
        if j.get("job_id") == job_id:
            return j, data
    return None, data


# ---------- Helpers: parse TN eDistrict text ----------

def parse_tnega_status(raw_text: str):
    """
    Parse the status page text into structured fields.
    Works on the text we saw for TN-2120251031226.
    """
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    parsed = {
        "app_no": "",
        "applicant_name": "",
        "father_name": "",
        "gender": "",
        "request_for": "",
        "date_of_request": "",
        "status_text": "",
        "remarks": "",
    }

    for ln in lines:
        # Use \t splits when present
        parts = [p.strip() for p in ln.split("\t") if p.strip()]
        if not parts:
            continue

        if "Application Number" in ln and not parsed["app_no"]:
            # e.g. ['Application Number', 'TN-2120251031226', 'Transaction Refernce No.', 'XXX']
            if len(parts) >= 2:
                parsed["app_no"] = parts[1]

        elif "Applicant Name" in ln and not parsed["applicant_name"]:
            # ['Applicant Name', 'Kokilavani V', 'Father/ Husband / Guardian / Mother Name', 'Venkatachalam']
            if len(parts) >= 4:
                parsed["applicant_name"] = parts[1]
                parsed["father_name"] = parts[3]

        elif ln.startswith("Gender") and not parsed["gender"]:
            # ['Gender', 'Female']
            if len(parts) >= 2:
                parsed["gender"] = parts[1]

        elif "Request For" in ln and not parsed["request_for"]:
            # ['Request For', 'REV-120 Unmarried Certificate', 'Date of Request', '31-Oct-2025']
            if len(parts) >= 2:
                parsed["request_for"] = parts[1]
            if "Date of Request" in ln and len(parts) >= 4:
                parsed["date_of_request"] = parts[3]

        elif ln.startswith("Status") and not parsed["status_text"]:
            # ['Status', 'Application Approved']
            if len(parts) >= 2:
                parsed["status_text"] = parts[1]

        elif ln.startswith("Remarks") and not parsed["remarks"]:
            # ['Remarks', 'Tamil text ...']
            if len(parts) >= 2:
                parsed["remarks"] = parts[1]

    # Fallback: if app_no still empty, try to guess from any TN- pattern
    if not parsed["app_no"]:
        for ln in lines:
            for token in ln.split():
                if token.startswith("TN-") and len(token) > 5:
                    parsed["app_no"] = token.strip(".,")
                    break
            if parsed["app_no"]:
                break

    return parsed


# ---------- Handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "வணக்கம்! 👋\n"
        "TNEGA சான்றுகள் status check & certificate download bot.\n\n"
        "உங்கள் விண்ணப்ப எண் இருந்தால்:\n"
        "`/check TN-2120251031226`\n"
        "இப்படி type பண்ணி அனுப்புங்க.",
        parse_mode="Markdown",
    )


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "தயவு செய்து உங்கள் விண்ணப்ப / Application Numberஐ இப்படிப் பண்ணி அனுப்புங்க:\n"
            "`/check TN-2120251031226`",
            parse_mode="Markdown",
        )
        return

       app_no = parts[1].strip()

    await update.message.reply_text(
        f"🔍 {app_no} கான status check பண்ணுகிறேன்...\nசிறிது நேரம் காத்திருக்கவும்."
    )

    try:
        # IMPORTANT: run sync scraper in thread, not directly
        result = await asyncio.to_thread(query_tnedistrict_status, app_no)
    except Exception as e:
        logger.exception("Scraper crash for %s: %s", app_no, e)
        await update.message.reply_text(
            "Status check செய்யும் போது சிக்கல் வந்தது.\n"
            "சிறிது நேரம் கழித்து மீண்டும் முயற்சி செய்யுங்கள்."
        )
        return

    logger.info("Scraper status for %s: %s", app_no, result.get("status"))

    if status not in {"approved", "pending", "rejected", "no_record", "captcha_required"}:
        await update.message.reply_text(
            "Unexpected result. Please try again later.\n\nDEBUG:\n" + raw[:1000]
        )
        return

    if status == "captcha_required":
        await update.message.reply_text(
            "அரசு தளத்தில் captcha கேட்கிறது.\n"
            "இப்போ bot மூலம் auto-check முடியவில்லை.\n"
            "கொஞ்சம் நேரம் கழித்து மீண்டும் முயற்சி பண்ணலாம் அல்லது கைமுறையாக தளத்தில் சென்று பார்க்கலாம்."
        )
        return

    if status == "no_record":
        await update.message.reply_text(
            "⚠️ இந்த Application Numberக்கு எந்த பதிவும் இல்லை என்று அரசு தளம் சொல்கிறது.\n"
            "எண் சரியா check பண்ணி மீண்டும் முயற்சி பண்ணுங்க.\n"
            "இல்லையெனில் புதிய விண்ணப்பம் தரலாம்."
        )
        return

    # approved / pending / rejected
    parsed = parse_tnega_status(raw)
    parsed["status_flag"] = status

    # Save to user_data for confirm step
    context.user_data["last_app"] = app_no
    context.user_data["last_parsed"] = parsed

    # Tamil summary
    lines = []
    lines.append("📄 TN eDistrict விண்ணப்ப விவரங்கள்:\n")
    lines.append(f"📄 விண்ணப்ப எண்: {parsed.get('app_no') or app_no}")
    if parsed.get("applicant_name"):
        lines.append(f"👤 விண்ணப்பதாரர் பெயர்: {parsed['applicant_name']}")
    if parsed.get("father_name"):
        lines.append(f"👨‍👧 தந்தை / குடும்பத் தலைவர்: {parsed['father_name']}")
    if parsed.get("gender"):
        lines.append(f"⚧ பாலினம்: {parsed['gender']}")
    if parsed.get("request_for"):
        lines.append(f"📑 சான்று பெயர்: {parsed['request_for']}")
    if parsed.get("date_of_request"):
        lines.append(f"📅 விண்ணப்பித்த தேதி: {parsed['date_of_request']}")
    if parsed.get("status_text"):
        lines.append(f"✅ தற்போதைய நிலை: {parsed['status_text']}")
    if parsed.get("remarks"):
        lines.append(f"🗒️ Remarks: {parsed['remarks']}")

    text = "\n".join(lines)

    if status == "approved":
        # Ask confirmation + move to job flow
        keyboard = [
            [
                InlineKeyboardButton("✅ இது எனது விவரம்", callback_data="CONFIRM_YES"),
                InlineKeyboardButton("❌ இது நான் இல்லை", callback_data="CONFIRM_NO"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text += (
            "\n\nஇது உங்கள் விண்ணப்ப விவரம் தானா?\n"
            "✔️ சரி என்றால் '✅ இது எனது விவரம்'\n"
            "❌ வேறு நபர் என்றால் '❌ இது நான் இல்லை'"
        )
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif status == "pending":
        text += (
            "\n\n⏳ Status: Pending\n"
            "உங்கள் விண்ணப்பம் தற்போது ஆய்வில் உள்ளது.\n"
            "சாதாரணமாக 2–3 நாட்களுக்குள் VAO / RI / Tahsildar அவர்கள்\n"
            "ஆவணங்களை சரிபார்த்து முடிவு எடுப்பார்கள்.\n"
            "3 நாட்கள் ஆகியும் மாற்றமில்லையெனில் அருகிலுள்ள VAO அலுவலகத்தில் தொடர்பு கொள்ளவும்."
        )
        await update.message.reply_text(text)
    elif status == "rejected":
        text += (
            "\n\n❌ Status: Rejected\n"
            "மேலே கொடுக்கப்பட்ட Remarks அடிப்படையில்\n"
            "தேவையான ஆவணங்களுடன் அருகிலுள்ள VAO / e-Sevai மையத்தில்\n"
            "புதிய விண்ணப்பம் தரவும்."
        )
        await update.message.reply_text(text)


async def on_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user saying 'yes this is me' or 'not me'."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "CONFIRM_NO":
        await query.edit_message_text(
            "சரி 👍\n"
            "தயவு செய்து உங்கள் Application Numberஐ மீண்டும் சரி பார்த்து\n"
            "`/check <AppNo>` என்று அனுப்பி முயற்சி பண்ணுங்க.",
            parse_mode="Markdown",
        )
        return

    if data != "CONFIRM_YES":
        await query.edit_message_text("தவறான தேர்வு. மீண்டும் /check அனுப்பி முயற்சி பண்ணுங்க.")
        return

    parsed = context.user_data.get("last_parsed")
    app_no = context.user_data.get("last_app")
    if not parsed or not app_no:
        await query.edit_message_text(
            "Session காலாவதியானது.\nதயவு செய்து மீண்டும் `/check <AppNo>` அனுப்பி முயற்சி பண்ணுங்க.",
            parse_mode="Markdown",
        )
        return

    user_chat_id = query.from_user.id

    # Create job immediately (Phase-1: payment bypass / manual)
    job = _create_job(app_no, parsed, user_chat_id)

    # Message to user
    msg = (
        "✅ உங்கள் விண்ணப்ப விவரம் உறுதிசெய்யப்பட்டது.\n"
        "இந்த சேவைக்கு சாதாரணமாக கட்டணம் ₹10 வசூலிக்கப்படும்.\n"
        "இப்போது *test / soft launch* mode ல இருக்கு.\n\n"
        f"🧾 Job ID: `{job['job_id']}`\n"
        "எங்கள் operator / e-sevai நண்பர் அரசு தளத்தில்\n"
        "captcha enter பண்ணி certificate PDF எடுத்து\n"
        "இதே chat ல உங்களுக்கு அனுப்புவார்.\n\n"
        "⏳ சற்று காத்திருக்கவும்."
    )
    await query.edit_message_text(msg, parse_mode="Markdown")

    # Notify admin
    try:
        admin_text = (
            "🆕 புதிய JOB உருவாக்கப்பட்டது:\n\n"
            f"🧾 Job ID: {job['job_id']}\n"
            f"📄 Application: {job['app_no']}\n"
            f"👤 Name: {job['name']}\n"
            f"👨‍👧 Father: {job['father_name']}\n"
            f"📑 Service: {job['service']}\n"
            f"📅 Date: {job['date_of_request']}\n"
            f"✅ Status: {job['status_text']}\n"
            f"🗒️ Remarks: {job['remarks']}\n\n"
            f"User Chat ID: {job['user_chat_id']}\n"
            "👇 கீழே உள்ள button வழியாக job எடுத்துக் கொள்ளலாம்."
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    "👨‍💻 இந்த JOB நான் எடுக்கிறேன்", callback_data=f"TAKE_JOB|{job['job_id']}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🌐 TN eDistrict Open",
                    url="https://tnedistrict.tn.gov.in/tneda/VerifyCerti.xhtml",
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
            text=admin_text,
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error("Failed to notify admin: %s", e)


async def on_take_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin clicks 'take job' button."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id != config.ADMIN_CHAT_ID:
        await query.edit_message_text("இந்த செயல்பாடு admin க்கு மட்டும்.")
        return

    data = query.data or ""
    parts = data.split("|", 1)
    if len(parts) != 2:
        await query.edit_message_text("JOB ID இல்லை. /jobs மூலம் மீண்டும் பார்க்கவும்.")
        return
    job_id = parts[1]

    job, all_data = _find_job(job_id)
    if not job:
        await query.edit_message_text("இந்த JOB தற்போது இல்லை / முடிவடைந்துவிட்டது.")
        return

    job["state"] = "in_progress"
    job["taken_at"] = int(time.time())
    _save_tasks(all_data)

    # Update admin message
    await query.edit_message_text(
        f"✅ Job {job_id} நீங்கள் எடுத்துக்கொண்டீர்கள்.\n"
        "TN eDistrict தளத்தில் சென்றுட்டு:\n"
        f"- Application Number: {job['app_no']}\n"
        "- Certificate Number இடத்துலவும் இதே எண்னு type பண்ணி\n"
        "- Captcha enter பண்ணி red SEARCH button\n"
        "- Download Certificate → PDF save பண்ணுங்க.\n\n"
        "பின்பு இந்த Telegram bot ல PDF ஐ upload பண்ணும்போது\n"
        f"caption ல `{job_id}` மட்டும் எழுதுங்க.",
        parse_mode="Markdown",
    )

    # Inform user
    try:
        await context.bot.send_message(
            chat_id=job["user_chat_id"],
            text=(
                "🧑‍💻 உங்கள் certificate வேலை operator எடுத்துக் கொண்டார்.\n"
                "அரசு தளத்தில் இருந்து original PDF எடுத்து\n"
                "இங்கே அனுப்புவோம். 2–5 நிமிடங்கள் காத்திருக்கவும்."
            ),
        )
    except Exception as e:
        logger.error("Failed to notify user about job in_progress: %s", e)


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: list pending/in_progress jobs."""
    user_id = update.effective_user.id
    if user_id != config.ADMIN_CHAT_ID:
        await update.message.reply_text("இந்த கட்டளை admin க்கு மட்டும்.")
        return

    data = _load_tasks()
    jobs = [j for j in data["jobs"] if j.get("state") != "done"]

    if not jobs:
        await update.message.reply_text("இப்போது pending / in-progress jobs எதுவும் இல்லை.")
        return

    lines = ["📋 Current Jobs:\n"]
    for j in jobs:
        created = datetime.fromtimestamp(j["created_at"]).strftime("%d-%m-%Y %H:%M")
        lines.append(
            f"{j['job_id']} | {j['app_no']} | {j['name']} | {j['state']} | {created}"
        )

    await update.message.reply_text("\n".join(lines))


async def on_admin_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin uploads final certificate PDF with caption = JOB-xxxx
    """
    msg = update.message
    user_id = msg.from_user.id

    if user_id != config.ADMIN_CHAT_ID:
        await msg.reply_text("இந்த PDF upload admin க்கு மட்டும் அனுமதிக்கப்படுகிறது.")
        return

    if not msg.document:
        await msg.reply_text("PDF document மட்டும் அனுப்பவும்.")
        return

    if not msg.caption:
        await msg.reply_text("caption ல job id (உதா: JOB-1234567890) எழுதவும்.")
        return

    caption = msg.caption.strip()
    job_id = caption.split()[0].strip()

    job, all_data = _find_job(job_id)
    if not job:
        await msg.reply_text(f"JOB {job_id} கிடைக்கவில்லை. caption சரியா check பண்ணுங்க.")
        return

    # Download PDF
    doc = msg.document
    file = await doc.get_file()

    # Build nice filename
    def _safe(s):
        return "".join(c for c in s if c.isalnum() or c in (" ", "_", "-", ".")).strip().replace(" ", "_")

    base_name = f"{job['service']}_{job['name']}_{job['app_no']}".strip() or job_id
    base_name = _safe(base_name)
    if not base_name.lower().endswith(".pdf"):
        base_name += ".pdf"

    dest_path = os.path.join(DOWNLOAD_DIR, base_name)
    await file.download_to_drive(dest_path)

    # Mark job done
    job["state"] = "done"
    job["done_at"] = int(time.time())
    _save_tasks(all_data)

    # Send to user
    try:
        await context.bot.send_message(
            chat_id=job["user_chat_id"],
            text=(
                "✅ உங்கள் certificate தயார்.\n"
                "கீழே உள்ள PDF ஐ download செய்து பாதுகாப்பாக வைத்து கொள்ளவும்.\n"
                "எந்த issue இருந்தாலும் இந்த chat லவே reply பண்ணுங்க."
            ),
        )
        await context.bot.send_document(
            chat_id=job["user_chat_id"],
            document=InputFile(dest_path),
        )
    except Exception as e:
        logger.error("Failed to send PDF to user: %s", e)
        await msg.reply_text("User க்கு PDF அனுப்பும் போது ஒரு பிரச்சனை ஏற்பட்டது. Logs check பண்ணவும்.")

    await msg.reply_text(f"✅ JOB {job_id} completed & PDF sent to user.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception in handler: %s", context.error)


def main():
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CallbackQueryHandler(on_confirm, pattern="^CONFIRM_"))
    app.add_handler(CallbackQueryHandler(on_take_job, pattern="^TAKE_JOB"))

    # Admin PDF upload (any PDF document)
    app.add_handler(MessageHandler(filters.Document.PDF, on_admin_pdf))

    app.add_error_handler(error_handler)

    logger.info("Starting TNEGA bot (Phase-1, no Razorpay automation)...")
    app.run_polling()


if __name__ == "__main__":
    main()
