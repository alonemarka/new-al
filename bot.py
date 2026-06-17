import os
import json
import sqlite3
from io import BytesIO
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

from groq import Groq
import openai

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
openai.api_key = DEEPSEEK_API_KEY
openai.base_url = "https://api.deepseek.com/v1"

conn = sqlite3.connect('bot.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users
             (user_id INTEGER PRIMARY KEY, username TEXT, mode TEXT DEFAULT 'normal', history TEXT)''')
conn.commit()

broadcast_mode = {}

def get_user_data(user_id):
    c.execute("SELECT mode, history FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    if not result:
        c.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return "normal", "[]"
    return result[0], result[1] or "[]"

def save_history(user_id, history_list):
    history_json = json.dumps(history_list[-30:])
    c.execute("UPDATE users SET history=? WHERE user_id=?", (history_json, user_id))
    conn.commit()

def get_all_users():
    c.execute("SELECT user_id FROM users")
    return [row[0] for row in c.fetchall()]

def get_system_prompt(mode):
    prompts = {
        "coder": "Sen uzman bir yazılımcısın. Kodları güzelce formatla, açıklamalı ver.",
        "normal": "Yararlı, eğlenceli ve dostça bir AI'sın.",
        "sokak": "Sokak çocuğu gibi konuş, argo kullan, kanka diye hitap et."
    }
    return prompts.get(mode, prompts["normal"])

async def get_deepseek_response(prompt, history, mode="normal"):
    try:
        system = get_system_prompt(mode)
        messages = [{"role": "system", "content": system}]
        for h in history[-15:]:
            messages.append({"role": "user", "content": h.get("user", "")})
            messages.append({"role": "assistant", "content": h.get("assistant", "")})
        messages.append({"role": "user", "content": prompt})

        response = openai.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=0.7,
            max_tokens=1200
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ alone chat api Hatası: {str(e)}"

async def get_groq_vision_response(prompt, image_url, history, mode="normal"):
    if not groq_client:
        return "Groq API Key eksik."
    try:
        messages = [{"role": "system", "content": get_system_prompt(mode)}]
        for h in history[-10:]:
            messages.append({"role": "user", "content": h.get("user", "")})
            messages.append({"role": "assistant", "content": h.get("assistant", "")})
        
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
        })

        chat = groq_client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=messages,
            temperature=0.7,
            max_tokens=1000
        )
        return chat.choices[0].message.content
    except Exception as e:
        return f"❌ alone api Hatası: {str(e)[:150]}"

# ====================== KOMUTLAR ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Alone AI Bot** aktif!\n"
        "Sohbet → DeepSeek\n"
        "Görsel → Groq Vision\n\n"
        "Komutlar:\n"
        "/coder /normal /sokak /clear /ses /stt /cevir"
    )

async def set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    user_id = update.effective_user.id
    c.execute("UPDATE users SET mode=? WHERE user_id=?", (mode, user_id))
    conn.commit()
    await update.message.reply_text(f"✅ **{mode.upper()}** modu aktif!")

async def clear_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    save_history(user_id, [])
    await update.message.reply_text("🗑️ Hafıza temizlendi!")

async def ses_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Kullanım: `/ses Merhaba`")
        return
    text = " ".join(context.args)
    try:
        tts = gTTS(text, lang='tr')
        bio = BytesIO()
        tts.write_to_fp(bio)
        bio.seek(0)
        await update.message.reply_voice(voice=bio)
    except Exception as e:
        await update.message.reply_text(f"Ses hatası: {e}")

async def stt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Sesli mesaj at.")

async def cevir_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Kullanım: `/cevir metin`")
        return
    text = " ".join(context.args)
    resp = await get_deepseek_response(f"Bu metni Türkçe'ye çevir: {text}", [], "normal")
    await update.message.reply_text(resp)

# Broadcast basit
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Admin değilsin.")
        return
    await update.message.reply_text("Broadcast hazır.")

# ====================== MESAJ HANDLER ======================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode, history_json = get_user_data(user_id)
    history = json.loads(history_json)

    text = update.message.text

    if text.startswith('/'):
        if text == '/coder': await set_mode(update, context, 'coder'); return
        if text == '/normal': await set_mode(update, context, 'normal'); return
        if text == '/sokak': await set_mode(update, context, 'sokak'); return
        if text == '/clear': await clear_memory(update, context); return
        if text == '/stt': await stt_command(update, context); return
        if text == '/cevir': await cevir_command(update, context); return

    response = await get_deepseek_response(text, history, mode)
    await update.message.reply_text(response)
    
    history.append({"user": text, "assistant": response})
    save_history(user_id, history)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode, history_json = get_user_data(user_id)
    history = json.loads(history_json)

    photo = update.message.photo[-1]
    file = await photo.get_file()
    caption = update.message.caption or "Bu görseli detaylı analiz et ve anlat."

    response = await get_groq_vision_response(caption, file.file_path, history, mode)
    await update.message.reply_text(response)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice = update.message.voice
    file = await voice.get_file()
    audio_bytes = await file.download_as_bytearray()
    try:
        transcription = groq_client.audio.transcriptions.create(
            file=("voice.ogg", audio_bytes),
            model="whisper-large-v3-turbo",
            response_format="text"
        )
        await update.message.reply_text(f"🎤 Dinledim: {transcription}")
        response = await get_deepseek_response(transcription, [], "normal")
        await update.message.reply_text(response)
    except Exception as e:
        await update.message.reply_text(f"STT Hatası: {str(e)}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("coder", lambda u,c: set_mode(u,c,'coder')))
    app.add_handler(CommandHandler("normal", lambda u,c: set_mode(u,c,'normal')))
    app.add_handler(CommandHandler("sokak", lambda u,c: set_mode(u,c,'sokak')))
    app.add_handler(CommandHandler("clear", clear_memory))
    app.add_handler(CommandHandler("stt", stt_command))
    app.add_handler(CommandHandler("cevir", cevir_command))
    
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 Bot (DeepSeek + Groq Vision) hazır!")
    app.run_polling()

if __name__ == "__main__":
    main()
