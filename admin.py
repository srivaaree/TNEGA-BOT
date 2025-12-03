import os
from telegram import Update, InputFile
from telegram.ext import ContextTypes

ADMIN_CHAT_ID = 1538155602  # your admin chat ID


async def notify_admin(app_no: str, user_chat_id: int):
    """
    Notify admin that CAPTCHA/manual action is required.
    """

    text = (
        "⚠️ ADMIN ACTION REQUIRED\n\n"
        f"Application: {app_no}\n"
        f"User Chat ID: {user_chat_id}\n\n"
        "Open the website, enter application number, solve CAPTCHA, and upload certificate.\n\n"
        "Send file here when ready."
    )

    from bot import application   # lazy-import to avoid circular import

    await application.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=text
    )


async def handle_admin_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin uploads certificate PDF or image.
    """
    user = update.message.from_user.id

    if user != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to upload certificate.")
        return

    if not update.message.document:
        await update.message.reply_text("Please upload PDF.")
        return

    file_name = update.message.document.file_name
    file_path = os.path.join("uploads", file_name)

    file = await update.message.document.get_file()
    await file.download_to_drive(file_path)

    await update.message.reply_text(f"✔ Certificate saved.\nFile: {file_name}\n\nUser will receive it shortly.")
