from telegram.ext import Application, CommandHandler, MessageHandler, filters
from handlers.status_handler import cmd_check
from handlers.certificate_handler import cmd_getcert, cmd_paid, admin_file_handler
import config


# ---------------------------------------------------------
# BUILD THE APPLICATION FIRST
# ---------------------------------------------------------
app = Application.builder().token(config.BOT_TOKEN).build()


# ---------------------------------------------------------
# COMMAND HANDLERS
# ---------------------------------------------------------
async def start_cmd(update, context):
    await update.message.reply_text(
        "Welcome! 😊\nSend /check <application_no> to check your certificate status."
    )

app.add_handler(CommandHandler("start", start_cmd))
app.add_handler(CommandHandler("check", cmd_check))
app.add_handler(CommandHandler("getcert", cmd_getcert))
app.add_handler(CommandHandler("paid", cmd_paid))


# ---------------------------------------------------------
# ADMIN (PDF or screenshots uploaded by operator)
# ---------------------------------------------------------
app.add_handler(MessageHandler(filters.Document.ALL, admin_file_handler))


# ---------------------------------------------------------
# RUN BOT
# ---------------------------------------------------------
if __name__ == "__main__":
    print("Starting TNEGA Bot…")
    app.run_polling()
