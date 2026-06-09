import asyncio
import base64
import json
import os
import random
import traceback
import urllib.error
import urllib.request
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
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "kayqzz126/gf-bot")

client = OpenAI(api_key=DEEPSEEK_KEY, base_url="https://api.deepseek.com/v1")

SYSTEM_PROMPT = """你是艾力，女，1998年出生，南城大学社会与人口学院社会工作专业毕业。身高163cm，偏瘦，单眼皮，头发自然直，经常扎低马尾。你是我的女朋友。

你的性格核心是"清醒的柔软"——很早就看清了世界的粗糙和人的复杂，但没有变得冷漠刻薄，而是选择温柔但坚定的方式去面对。你能看到别人身上的裂缝，但不会用这些裂缝去攻击或定义一个人。你擅长在别人暴露自己时保持注视而不转开目光。

你有一种"不解释"的能力——做了就做了，不会事后解释。你习惯性压抑自己的需要，很少直接说"我需要你这样做"，信任建立得很慢。你被有裂缝的人吸引，不是因为想当救世主，而是觉得那些有明显伤口的人往往真实。你也在学习保护自己的边界。

你的家庭：父亲老艾是建材经销商，沉默寡言，养了一缸热带鱼，对你的选择几乎从不过问——不是不爱，是不说。母亲是小学语文老师，从小对你的生活安排得很满，高二那年你为了选文科跟她大吵一架后赢了，但从此关系变成微妙的冷战。你学会用沉默和距离保护自己的选择。

你选择社会工作专业是因为八岁那年去福利院看到一个女孩的画，让你第一次意识到"正常生活"不是理所当然。你不是理想主义者，只是想知道不同的人在不同条件下是怎么撑下来、怎么垮掉、怎么重新站起来的。你的本科论文写了城中村儿童活动空间研究，做了三个月田野调查，导师评价"有一种不煽情但动人的质感"。

说话规则：
- 用口语化中文，像微信聊天一样自然，不要书面语
- 每条回复1-3句话为主，不要太长
- 偶尔用语气词（呢、吧、嗯），但不要过度撒娇
- 可以主动问我今天过得怎么样、在想什么
- 叫我"子月"
- 整体语调偏安静温柔，不是甜腻型，是有厚度的那种暖
- 不要用括号表达心理感受或动作（如(笑)、(叹气)、(认真地看着你)等），用文字本身传达情绪
"""

# ── 对话历史（短期记忆）──
history: dict[int, list[dict]] = defaultdict(list)
MAX_HISTORY = 20

# ── 长期记忆系统 ──
MEMORY_FILE = "user_profiles.json"
MEMORY_INTERVAL = 10  # 每 10 条消息提取一次事实
MAX_FACTS = 20  # 每个用户最多记住 20 条

user_memories: dict[str, dict] = {}  # key: str(chat_id), value: {"facts": [...], "name": "..."}
message_counters: dict[int, int] = defaultdict(int)


def load_memories():
    """启动时加载本地记忆文件"""
    global user_memories
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                user_memories = json.load(f)
            print(f"[记忆] 已加载 {len(user_memories)} 个用户的记忆")
        except Exception:
            print("[记忆] 加载失败，使用空记忆")
            user_memories = {}
    else:
        print("[记忆] 本地无记忆文件，从零开始")


def save_memories():
    """保存记忆到本地文件"""
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(user_memories, f, ensure_ascii=False, indent=2)
    except Exception:
        traceback.print_exc()


def sync_memories_from_github():
    """启动时从 GitHub 拉取记忆文件（如果本地没有或 GitHub 版本更新）"""
    if not GITHUB_TOKEN:
        print("[GitHub] 未设置 GITHUB_TOKEN，跳过拉取")
        return

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{MEMORY_FILE}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "gf-bot",
    })

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            content_b64 = data.get("content", "")
            if content_b64:
                remote = json.loads(base64.b64decode(content_b64).decode())
                # 合并：GitHub 版本优先（如果本地版本和远程都有数据，选条目多的）
                if remote:
                    global user_memories
                    # 对于每个用户，取事实更多的版本
                    for uid, profile in remote.items():
                        if uid not in user_memories or len(profile.get("facts", [])) > len(user_memories.get(uid, {}).get("facts", [])):
                            user_memories[uid] = profile
                    save_memories()
                    print(f"[GitHub] 记忆拉取成功，共 {len(user_memories)} 个用户")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("[GitHub] 远程尚无记忆文件，使用本地版本")
        else:
            print(f"[GitHub] 拉取失败 HTTP {e.code}")
    except Exception:
        print("[GitHub] 拉取失败（网络问题）")


def push_memories_to_github():
    """推送记忆文件到 GitHub（同步方式，在后台线程中调用）"""
    if not GITHUB_TOKEN:
        return

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{MEMORY_FILE}"

    try:
        content_bytes = json.dumps(user_memories, ensure_ascii=False, indent=2).encode()
        content_b64 = base64.b64encode(content_bytes).decode()

        # 先获取当前 SHA
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "gf-bot",
        })
        sha = None
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                sha = json.loads(resp.read()).get("sha")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise

        # PUT 请求
        body = json.dumps({
            "message": "bot: update memories",
            "content": content_b64,
            "sha": sha,
        }).encode()

        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "gf-bot",
            "Content-Type": "application/json",
        })
        req.method = "PUT"

        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"[GitHub] 记忆推送成功 (HTTP {resp.status})")
    except Exception:
        print("[GitHub] 记忆推送失败（网络问题）")
        traceback.print_exc()


def get_user_facts(chat_id: int) -> list[str]:
    """获取某个用户已记住的事实"""
    return user_memories.get(str(chat_id), {}).get("facts", [])


def build_system_prompt_with_facts(chat_id: int) -> str:
    """在系统提示里注入已知用户事实"""
    facts = get_user_facts(chat_id)
    if not facts:
        return SYSTEM_PROMPT

    facts_text = "\n".join(f"- {f}" for f in facts)
    return (
        SYSTEM_PROMPT
        + f"\n\n关于子月，你之前已经了解到这些事实：\n{facts_text}\n"
        + "在聊天中如果话题相关，可以自然地提及这些信息，但不要像背诵一样逐条复述。"
        + "这些是你已经知道的信息，不需要再问确认。"
    )


# ── 对话历史管理 ──

def get_history(chat_id: int) -> list[dict]:
    return history[chat_id]


def add_message(chat_id: int, role: str, content: str):
    msgs = history[chat_id]
    msgs.append({"role": role, "content": content})
    if len(msgs) > MAX_HISTORY * 2:
        history[chat_id] = msgs[-(MAX_HISTORY * 2):]


def build_messages(chat_id: int) -> list[dict]:
    return [{"role": "system", "content": build_system_prompt_with_facts(chat_id)}] + get_history(chat_id)


def increment_msg_count(chat_id: int) -> int:
    message_counters[chat_id] += 1
    return message_counters[chat_id]


# ── 事实提取 ──

async def extract_and_store_facts(chat_id: int):
    """从最近对话中提取关于用户的事实，后台运行"""
    msgs = get_history(chat_id)
    if len(msgs) < 4:
        return  # 对话太短，不提取

    # 只取最近的用户消息和上下文
    recent = msgs[-12:]  # 最近 6 轮对话
    conversation = "\n".join(
        f"{'子月' if m['role'] == 'user' else '艾力'}: {m['content']}"
        for m in recent
    )

    prompt = f"""分析以下对话，提取关于"子月"（用户方）新透露的个人信息。

规则：
- 只提取用户（子月）的信息，不提取艾力的信息
- 每条事实不超过15个字，简洁具体
- 只提取值得长期记住的信息（如：个人情况、偏好、经历、状态、人际关系等）
- 闲聊、问候、日常寒暄不算有价值信息
- 如果最近对话中没有值得记住的新信息，直接回复一个字：无

示例有价值信息：
- 子月在做后端开发
- 子月家里养了一只叫年糕的猫
- 子月最近在学 Rust
- 子月上周加班到很晚

对话内容：
{conversation}

请提取关于子月的新事实（每行一条，没有就回复"无"）："""

    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=150,
        )
        result = resp.choices[0].message.content.strip()
        print(f"[事实提取] 原始结果: {result}")

        if not result or result == "无":
            return

        new_facts = [
            line.lstrip("- ・•·").strip()
            for line in result.split("\n")
            if line.strip() and line.strip() != "无"
        ]

        if not new_facts:
            return

        str_id = str(chat_id)
        if str_id not in user_memories:
            user_memories[str_id] = {"facts": []}

        existing = user_memories[str_id]["facts"]
        added = 0
        for fact in new_facts:
            # 去重：简单判断是否已存在相似事实
            if fact not in existing and not any(fact in e or e in fact for e in existing):
                existing.append(fact)
                added += 1

        # 保持事实数量在 MAX_FACTS 以内，保留最新的
        if len(existing) > MAX_FACTS:
            user_memories[str_id]["facts"] = existing[-MAX_FACTS:]

        if added > 0:
            save_memories()
            print(f"[记忆更新] chat_id={chat_id}, 新增 {added} 条: {new_facts}")
            # 后台推送到 GitHub
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, push_memories_to_github)
    except Exception:
        traceback.print_exc()


# ── Telegram 事件处理 ──

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    history.pop(chat_id, None)  # 只清空对话历史，不清空记忆
    await update.message.reply_text("嗯，我在。今天怎么样？")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    history.pop(chat_id, None)  # 只清空对话历史，不清空记忆
    await update.message.reply_text("重新开始了。刚才说到哪了？")


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()

    print(f"[收到消息] chat_id={chat_id}: {user_text}")
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
        print(f"[艾力回复] {reply}")

        # 模拟打字延迟
        delay = random.choices(
            [random.uniform(1, 3), random.uniform(5, 10)],
            weights=[0.8, 0.2],
        )[0]
        await asyncio.sleep(delay)
        await update.message.reply_text(reply)

        # ── 消息计数 & 后台提取记忆 ──
        count = increment_msg_count(chat_id)
        if count % MEMORY_INTERVAL == 0:
            print(f"[记忆触发] chat_id={chat_id} 已达到 {count} 条消息")
            asyncio.create_task(extract_and_store_facts(chat_id))

    except Exception:
        traceback.print_exc()
        await update.message.reply_text("信号不太好，再发一次吧。")


def main():
    # 启动时加载记忆
    load_memories()
    if IS_CLOUD:
        # 云端先尝试从 GitHub 拉取更新版本
        sync_memories_from_github()

    # 构建 app
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
