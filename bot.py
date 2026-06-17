import os
import json
import sqlite3
from io import BytesIO
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

from groq import Groq
from gtts import gTTS

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

client = Groq(api_key=GROQ_API_KEY)

# Database
conn = sqlite3.connect('bot.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users 
             (user_id INTEGER PRIMARY KEY, username TEXT, mode TEXT DEFAULT 'normal', history TEXT)''')
conn.commit()

# Broadcast state
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
        "sokak": "Sokak çocuğu gibi konuş, argo kullan, kanka diye hitap et, samimi ol."
    }
    return prompts.get(mode, prompts["normal"])

async def get_ai_response(prompt, history, mode="normal", image=None):
    messages = [{"role": "system", "content": get_system_prompt(mode)}]
    for h in history:
        messages.append({"role": "user", "content": h.get("user", "")})
        messages.append({"role": "assistant", "content": h.get("assistant", "")})
    
    user_content = [{"type": "text", "text": prompt}]
    if image:
        user_content.append({"type": "image_url", "image_url": {"url": image}})
    
    messages.append({"role": "user", "content": user_content})

    try:
        chat = client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=messages,
            temperature=0.75,
            max_tokens=1200
        )
        return chat.choices[0].message.content
    except Exception as e:
        return f"❌ Hata: {str(e)}"

# ====================== KOMUTLAR ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Alone AI Bot** aktif!\n\n"
        "Özel mesajlarda **her mesaja** AI ile cevap veriyorum.\n\n"
        "Komutlar:\n"
        "/coder - Coder Modu\n"
        "/normal - Normal Mod\n"
        "/sokak - Sokak Modu\n"
        "/clear - Hafızayı temizle\n"
        "/ses <metin> - Sesli cevap\n"
        "/stt - Sesli mesajı yazıya çevir\n"
        "/cevir <metin> - Türkçe'ye çevir\n"
        "/broadcast - Admin duyuru (sadece admin)"
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

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Senin ID: `{update.effective_user.id}`", parse_mode=ParseMode.MARKDOWN)

async def ses_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Kullanım: `/ses Merhaba kanka`")
        return
    text = " ".join(context.args)
    try:
        tts = gTTS(text, lang='tr')
        bio = BytesIO()
        tts.write_to_fp(bio)
        bio.seek(0)
        await update.message.reply_voice(voice=bio, caption="Sesli Cevap")
    except Exception as e:
        await update.message.reply_text(f"Ses hatası: {e}")

async def stt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎤 Sesli mesaj at, hemen yazıya çevireyim.")

async def cevir_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Kullanım: `/cevir Hello world`")
        return
    text = " ".join(context.args)
    response = await get_ai_response(f"Bu metni doğal ve akıcı Türkçe'ye çevir: {text}", [], "normal")
    await update.message.reply_text(response)

# ====================== BROADCAST ======================
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Bu komut sadece admin'e özel!")
        return
    
    keyboard = [
        [InlineKeyboardButton("📝 Metin Duyuru", callback_data="bc_text")],
        [InlineKeyboardButton("🖼 Görsel Duyuru", callback_data="bc_photo")],
        [InlineKeyboardButton("🎥 Video Duyuru", callback_data="bc_video")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📢 Broadcast türü seç:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        return

    if query.data == "bc_text":
        broadcast_mode[user_id] = "text"
        await query.edit_message_text("📝 Metin duyurusunu yaz.")
    elif query.data == "bc_photo":
        broadcast_mode[user_id] = "photo"
        await query.edit_message_text("🖼 Görsel + caption at.")
    elif query.data == "bc_video":
        broadcast_mode[user_id] = "video"
        await query.edit_message_text("🎥 Video at.")

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in broadcast_mode:
        return

    mode = broadcast_mode.pop(user_id)
    users = get_all_users()
    success = 0

    if mode == "text" and update.message.text:
        text = update.message.text
        for uid in users:
            try:
                await context.bot.send_message(uid, text)
                success += 1
            except:
                pass
    elif mode == "photo" and update.message.photo:
        photo = update.message.photo[-1]
        caption = update.message.caption or ""
        file = await photo.get_file()
        for uid in users:
            try:
                await context.bot.send_photo(uid, photo=file.file_id, caption=caption)
                success += 1
            except:
                pass
    elif mode == "video" and update.message.video:
        video = update.message.video
        caption = update.message.caption or ""
        for uid in users:
            try:
                await context.bot.send_video(uid, video=video.file_id, caption=caption)
                success += 1
            except:
                pass

    await update.message.reply_text(f"✅ Broadcast tamamlandı! {success}/{len(users)} kişiye gönderildi.")

# ====================== ANA MESAJ (ÖZEL MESAJ ÖNCELİKLİ) ======================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Broadcast kontrolü
    if update.effective_user.id == ADMIN_ID and update.effective_user.id in broadcast_mode:
        await handle_broadcast(update, context)
        return

    user_id = update.effective_user.id
    mode, history_json = get_user_data(user_id)
    history = json.loads(history_json)

    text = update.message.text

    if text.startswith('/'):
        if text == '/coder': await set_mode(update, context, 'coder'); return
        if text == '/normal': await set_mode(update, context, 'normal'); return
        if text == '/sokak': await set_mode(update, context, 'sokak'); return
        if text == '/clear': await clear_memory(update, context); return
        if text == '/id': await id_command(update, context); return
        if text == '/ses': await ses_command(update, context); return
        if text == '/stt': await stt_command(update, context); return
        if text == '/cevir': await cevir_command(update, context); return
        if text == '/broadcast': await broadcast_command(update, context); return

    # HER ÖZEL MESAJA AI CEVAP
    response = await get_ai_response(text, history, mode)
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
    
    history.append({"user": text, "assistant": response})
    save_history(user_id, history)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode, history_json = get_user_data(user_id)
    history = json.loads(history_json)

    photo = update.message.photo[-1]
    file = await photo.get_file()
    caption = update.message.caption or "Bu görseli analiz et ve cevap ver."

    response = await get_ai_response(caption, history, mode, image=file.file_path)
    await update.message.reply_text(response)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice = update.message.voice
    file = await voice.get_file()
    audio_bytes = await file.download_as_bytearray()
    
    try:
        transcription = client.audio.transcriptions.create(
            file=("voice.ogg", audio_bytes),
            model="whisper-large-v3-turbo",
            response_format="text"
        )
        await update.message.reply_text(f"🎤 Dinledim:\n`{transcription}`")

        user_id = update.effective_user.id
        mode, _ = get_user_data(user_id)
        response = await get_ai_response(transcription, [], mode)
        await update.message.reply_text(response)
    except Exception as e:
        await update.message.reply_text(f"STT Hatası: {str(e)}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Komut Handler'ları
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("coder", lambda u,c: set_mode(u,c,'coder')))
    app.add_handler(CommandHandler("normal", lambda u,c: set_mode(u,c,'normal')))
    app.add_handler(CommandHandler("sokak", lambda u,c: set_mode(u,c,'sokak')))
    app.add_handler(CommandHandler("clear", clear_memory))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("ses", ses_command))
    app.add_handler(CommandHandler("stt", stt_command))
    app.add_handler(CommandHandler("cevir", cevir_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    
    # Mesaj Handler'ları
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 Alone AI Bot - Özel mesajlarda her mesaja AI cevap verecek şekilde hazır!")
    app.run_polling()

if __name__ == "__main__":
    main()
