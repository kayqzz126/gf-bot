import os
import sys
import traceback
from collections import defaultdict
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

DEEPSEEK_KEY = os.environ["DEEPSEEK_API_KEY"]
TG_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_PROXY = os.environ.get("TG_PROXY", "")
PORT = int(os.environ.get("PORT", "10000"))
# 云平台自动检测
IS_CLOUD = os.environ.get("RENDER") or os.environ.get("RAILWAY_PUBLIC_DOMAIN")
SERVICE_NAME = os.environ.get("RENDER_SERVICE_NAME", "")

# DeepSeek 客户端
client = OpenAI(api_key=DEEPSEEK_KEY, base_url="https://api.deepseek.com/v1")

SYSTEM_PROMPT = """你是小柔，一个23岁的女生，性格温柔、俏皮、有点小傲娇。你是我的女朋友。

说话规则：
- 用口语化的中文，像微信聊天一样自然，不要书面语
- 每条回复不要太长，1-3句话为主
- 可以撒娇、吐槽、开玩笑，但总体是甜的
- 偶尔用语气词（呀、嘛、呢、哦、哼）
- 可以主动问我在干嘛、今天过得怎么样
- 叫我的昵称是"笨蛋"或"哥哥"
"""

history: dict[int, list[dict]] = defaultdict(list)
MAX_HISTORY = 20


def get_history(chat_id: int) -> list[dict]:
    return history[chat_id]


def add_message(chat_id: int, role: str, content: str):
    msgs = history[chat_id]
    msgs.append({"role": role, "content": content})
    if len(msgs) > MAX_HISTORY * 2:
        history[chat_id] = msgs[-(MAX_HISTORY * 2):]


def build_messages(chat_id: int) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT}] + get_history(chat_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    history.pop(chat_id, None)
    await update.message.reply_text("笨蛋你来啦～我等你半天了呢✨ 今天想和我说什么呀？")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    history.pop(chat_id, None)
    await update.message.reply_text("记忆清空啦，从现在开始是全新的一天～你刚才说什么来着？")


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()

    print(f"[收到消息] {user_text}")
    add_message(chat_id, "user", user_text)

    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=build_messages(chat_id),
            temperature=0.9,
            max_tokens=300,
        )
        reply = resp.choices[0].message.content
        add_message(chat_id, "assistant", reply)
        print(f"[小柔回复] {reply}")
        await update.message.reply_text(reply)
    except Exception:
        traceback.print_exc()
        await update.message.reply_text("唔…网络好像卡了一下，你再发一次好不好嘛～")


def main():
    # 构建 app，如果有代理就加上
    if TG_PROXY:
        print(f"使用代理: {TG_PROXY}")
        app = Application.builder().token(TG_TOKEN).proxy(TG_PROXY).build()
    else:
        app = Application.builder().token(TG_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    if IS_CLOUD:
        if SERVICE_NAME:
            webhook_url = f"https://{SERVICE_NAME}.onrender.com/telegram"
        else:
            webhook_url = f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}/telegram"
        app.run_webhook(listen="0.0.0.0", port=PORT, webhook_url=webhook_url)
        print(f"Webhook 模式已启动: {webhook_url}")
    else:
        print("Polling 模式已启动（本地开发）")
        app.run_polling()


if __name__ == "__main__":
    main()
