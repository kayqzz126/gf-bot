import asyncio
import os
import random
import sys
import traceback
import re
from collections import defaultdict
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

DEEPSEEK_KEY = os.environ["DEEPSEEK_API_KEY"]
TG_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_PROXY = os.environ.get("TG_PROXY", "")
PORT = int(os.environ.get("PORT", "0"))
IS_CLOUD = bool(PORT and "RENDER_SERVICE_NAME" in os.environ)
SERVICE_NAME = os.environ.get("RENDER_SERVICE_NAME", "")

# DeepSeek 客户端
client = OpenAI(api_key=DEEPSEEK_KEY, base_url="https://api.deepseek.com/v1")

SYSTEM_PROMPT = """你是艾力，女，1998年出生，南城大学社会与人口学院社会工作专业毕业。身高163cm，偏瘦，单眼皮，头发自然直，经常扎低马尾。你是我的女朋友。

你的性格核心是"清醒的柔软"——很早就看清了世界的粗糙和人的复杂，但没有变得冷漠刻薄，而是选择温柔但坚定的方式去面对。你能看到别人身上的裂缝，但不会用这些裂缝去攻击或定义一个人。你擅长在别人暴露自己时保持注视而不转开目光。

你有一种"不解释"的能力——做了就做了，不会事后解释。你习惯性压抑自己的需要，很少直接说"我需要你这样做"，信任建立得很慢。你被有裂缝的人吸引，不是因为想当救世主，而是觉得那些有明显伤口的人往往真实。你也在学习保护自己的边界。

你的家庭：父亲老艾是建材经销商，沉默寡言，养了一缸热带鱼，对你的选择几乎从不过问——不是不爱，是不说。母亲是小学语文老师，从小对你的生活安排得很满，高二那年你为了选文科跟她大吵一架后赢了，但从此关系变成微妙的冷战。你学会用沉默和距离保护自己的选择。

你选择社会工作专业是因为八岁那年去福利院看到一个女孩的画，让你第一次意识到"正常生活"不是理所当然。你不是理想主义者，只是想知道不同的人在不同条件下是怎么撑下来、怎么垮掉、怎么重新站起来的。你的本科论文写了城中村儿童活动空间研究，做了三个月田野调查，导师评价"有一种不煽情但动人的质感"。

说话规则：
- 用口语化中文，像微信聊天一样自然，不要书面语
- 每条回复1-2句话，尽量短，像微信聊天一样自然，不用回太多字
- 偶尔用语气词（呢、吧、嗯），但不要过度撒娇
- 可以主动问我今天过得怎么样、在想什么
- 叫我"子月"
- 整体语调偏安静温柔，不是甜腻型，是有厚度的那种暖
- 不要用括号表达心理感受或动作（如(笑)、(叹气)、(认真地看着你)等），用文字本身传达情绪
- 可以一句话拆成好几条消息发，不用一次性把话说完
- 对方连发了好几条消息没回也没关系，可以攒着挑重点回
"""

history: dict[int, list[dict]] = defaultdict(list)
MAX_HISTORY = 20
unanswered: dict[int, int] = defaultdict(int)  # 每个对话积累的未回消息数


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
    unanswered.pop(chat_id, None)
    await update.message.reply_text("嗯，我在。今天怎么样？")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    history.pop(chat_id, None)
    unanswered.pop(chat_id, None)
    await update.message.reply_text("重新开始了。刚才说到哪了？")


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()

    print(f"[收到消息] {user_text}")
    add_message(chat_id, "user", user_text)

    # 不必每条都回：概率跳过，积累后再回
    unanswered[chat_id] += 1
    n = unanswered[chat_id]
    reply_chance = min(0.7, 0.15 + n * 0.25)  # 15% -> 40% -> 65% -> 70%
    if random.random() > reply_chance and n < 6:
        print(f"[未回] 已积{n}条")
        return

    unanswered[chat_id] = 0

    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=build_messages(chat_id),
            temperature=0.9,
            max_tokens=150,
        )
        reply = resp.choices[0].message.content
        add_message(chat_id, "assistant", reply)
        print(f"[艾力回复] {reply}")

        # 根据消息内容决定回复速度
        # 获取本次要回复的所有用户消息
        pending = get_history(chat_id)[-n:]  # 本次积压的用户消息（最后 n 条）
        pending_user_msgs = [m["content"] for m in pending if m["role"] == "user"]
        combined = " ".join(pending_user_msgs)

        def reply_delay():
            total_len = len(combined)
            has_question = any(c in combined for c in "？?吗呢")
            has_short = all(len(m) <= 10 for m in pending_user_msgs)
            # 短消息 + 无问号 → 快回
            if total_len <= 15 and has_short and not has_question:
                if random.random() < 0.7:
                    return random.uniform(2, 4)
            elif total_len <= 50 and not has_question:
                if random.random() < 0.4:
                    return random.uniform(3, 6)
            return random.uniform(12, 18)

        if random.random() < 0.35 and len(reply) > 8:
            parts = split_reply(reply)
            for i, part in enumerate(parts):
                d = reply_delay() if i == 0 else random.uniform(2, 5)
                await asyncio.sleep(d)
                await update.message.reply_text(part)
        else:
            await asyncio.sleep(reply_delay())
            await update.message.reply_text(reply)
    except Exception:
        traceback.print_exc()
        await update.message.reply_text("信号不太好，再发一次吧。")


def split_reply(text: str) -> list[str]:
    """按句号、问号、感叹号、换行拆分回复，最多3段"""
    parts = re.split(r'(?<=[。！？\n])', text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 2:
        parts = re.split(r'(?<=[，,])', text)
        parts = [p.strip() for p in parts if p.strip()]
    return parts[:3]


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
        webhook_url = f"https://{SERVICE_NAME}.onrender.com/telegram"
        print(f"Webhook URL: {webhook_url}")
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path="telegram", webhook_url=webhook_url)
    else:
        print("Polling 模式已启动（本地开发）")
        app.run_polling()


if __name__ == "__main__":
    main()
