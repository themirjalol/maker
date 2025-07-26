import telebot
from telebot import types
import os
import subprocess
import uuid
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import functools
import time

# ==================== KONFIGURATSIYA ====================
# Admin sozlamalari
# Maxfiy ma'lumotlarni to'g'ridan-to'g'ri kodingizga qo'ying
ADMIN_ID = "ADMIN_ID"  # O'z Telegram Admin ID raqamingizni qo'ying
TOKEN = "BOT_TOKEN"  # O'z Bot Tokeningizni qo'ying

# Ma'lumotlar bazasi konfiguratsiyasi
# Maxfiy ma'lumotlarni to'g'ridan-to'g'ri kodingizga qo'ying
DB_CONFIG = {
    'host': 'DB_HOST',  # alwaysdata PostgreSQL host
    'database': 'DB_NAME',                 # Ma'lumotlar bazasi nomi
    'user': 'DB_USER',                           # Foydalanuvchi nomi
    'password': 'DB_PASSWORD',                    # Parolni o'zgartiring
    'port': 5432                                   # Port (odatiy 5432)
}

bot = telebot.TeleBot(TOKEN)

# ==================== PAPKALARNI YARATISH ====================
os.makedirs("bot_templates", exist_ok=True)
os.makedirs("user_bots", exist_ok=True)

# ==================== MA'LUMOTLAR BAZASI BOSHQARUVCHI ====================
@contextmanager
def get_db_connection():
    """Ma'lumotlar bazasi ulanishini boshqarish"""
    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        yield conn
    except Exception as e:
        print(f"Ma'lumotlar bazasi ulanishida xatolik: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

# ==================== MA'LUMOTLAR BAZASINI SOZLASH ====================
def init_database():
    """Ma'lumotlar bazasi jadvallarini yaratish"""
    create_tables_sql = [
        """
        CREATE TABLE IF NOT EXISTS bot_templates (
            id UUID PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            file_path TEXT NOT NULL,
            filename VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS required_channels (
            id SERIAL PRIMARY KEY,
            template_id UUID REFERENCES bot_templates(id) ON DELETE CASCADE,
            channel_identifier TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(template_id, channel_identifier)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_bots (
            id UUID PRIMARY KEY,
            template_id UUID REFERENCES bot_templates(id) ON DELETE CASCADE,
            token TEXT NOT NULL,
            admin_id TEXT,
            file_path TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS bot_channels (
            id SERIAL PRIMARY KEY,
            bot_id UUID REFERENCES user_bots(id) ON DELETE CASCADE,
            channel_identifier TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS global_required_channels (
            id SERIAL PRIMARY KEY,
            channel_identifier TEXT UNIQUE NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    ]
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                for statement in create_tables_sql:
                    cur.execute(statement)
                conn.commit()
        print("Ma'lumotlar bazasi jadvallari yaratildi")
    except Exception as e:
        print(f"Ma'lumotlar bazasini sozlashda xatolik: {e}")

# ==================== MA'LUMOTLARNI BAZADAN YUKLASH ====================
def load_bot_templates():
    """Bot shablonlarini ma'lumotlar bazasidan yuklash"""
    templates = {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, file_path, filename FROM bot_templates
                """)
                rows = cur.fetchall()
                for row in rows:
                    template_id = str(row['id'])
                    templates[template_id] = {
                        'name': row['name'],
                        'path': row['file_path'],
                        'filename': row['filename']
                    }
                    
                    # Shablonga bog'liq kanallarni ham yuklash
                    cur.execute("""
                        SELECT channel_identifier FROM required_channels 
                        WHERE template_id = %s
                    """, (row['id'],))
                    channels = [c['channel_identifier'] for c in cur.fetchall()]
                    templates[template_id]['channels'] = channels
    except Exception as e:
        print(f"Shablonlarni yuklashda xatolik: {e}")
    return templates

def save_bot_template(template_id, name, file_path, filename):
    """Bot shablonini ma'lumotlar bazasiga saqlash"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO bot_templates (id, name, file_path, filename)
                    VALUES (%s, %s, %s, %s)
                """, (template_id, name, file_path, filename))
                conn.commit()
    except Exception as e:
        print(f"Shablonni saqlashda xatolik: {e}")

def delete_bot_template(template_id):
    """Bot shablonini ma'lumotlar bazasidan o'chirish"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bot_templates WHERE id = %s", (template_id,))
                conn.commit()
    except Exception as e:
        print(f"Shablonni o'chirishda xatolik: {e}")

def load_user_bots():
    """Foydalanuvchi botlarini ma'lumotlar bazasidan yuklash"""
    bots = {}
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, template_id, token, admin_id, file_path FROM user_bots
                    WHERE is_active = TRUE
                """)
                rows = cur.fetchall()
                for row in rows:
                    bot_id = str(row['id'])
                    bots[bot_id] = {
                        'template_id': str(row['template_id']),
                        'token': row['token'],
                        'admin_id': row['admin_id'],
                        'path': row['file_path'],
                        'process': None,  # Bu jarayonni keyin boshqarish kerak
                        'channels': []  # Kanallarni alohida yuklash kerak
                    }
                    
                    # Botga bog'liq kanallarni yuklash
                    cur.execute("""
                        SELECT channel_identifier FROM bot_channels 
                        WHERE bot_id = %s
                    """, (row['id'],))
                    channels = [c['channel_identifier'] for c in cur.fetchall()]
                    bots[bot_id]['channels'] = channels
    except Exception as e:
        print(f"Botlarni yuklashda xatolik: {e}")
    return bots

def save_user_bot(bot_id, template_id, token, admin_id, file_path):
    """Foydalanuvchi botini ma'lumotlar bazasiga saqlash"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_bots (id, template_id, token, admin_id, file_path)
                    VALUES (%s, %s, %s, %s, %s)
                """, (bot_id, template_id, token, admin_id, file_path))
                conn.commit()
    except Exception as e:
        print(f"Botni saqlashda xatolik: {e}")

def delete_user_bot(bot_id):
    """Foydalanuvchi botini ma'lumotlar bazasidan o'chirish"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE user_bots SET is_active = FALSE WHERE id = %s", (bot_id,))
                conn.commit()
    except Exception as e:
        print(f"Botni o'chirishda xatolik: {e}")

# ==================== GLOBAL KANAL BOSHQARUVI ====================
def add_global_channel(channel):
    """Global majburiy kanal qo'shish"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO global_required_channels (channel_identifier)
                    VALUES (%s)
                    ON CONFLICT (channel_identifier) DO NOTHING
                """, (channel,))
                conn.commit()
                return True
    except Exception as e:
        print(f"Kanal qo'shishda xatolik: {e}")
        return False

def remove_global_channel(channel):
    """Global majburiy kanalni o'chirish"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM global_required_channels WHERE channel_identifier = %s", (channel,))
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        print(f"Kanal o'chirishda xatolik: {e}")
        return False

@functools.lru_cache(maxsize=128)
def list_global_channels():
    """Global majburiy kanallar ro'yxatini olish (keshlangan)"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT channel_identifier FROM global_required_channels ORDER BY added_at")
                return tuple([row['channel_identifier'] for row in cur.fetchall()])  # tuple kesh uchun
    except Exception as e:
        print(f"Kanallar ro'yxatini olishda xatolik: {e}")
        return tuple([])

def clear_global_channels():
    """Barcha global majburiy kanallarni tozalash"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM global_required_channels")
                conn.commit()
                # Keshni tozalash
                list_global_channels.cache_clear()
    except Exception as e:
        print(f"Kanallarni tozalashda xatolik: {e}")

# ==================== MAJBURIY OBUNA FUNKSIYALARI ====================
def check_subscription(bot_instance, user_id, channels=None):
    """Foydalanuvchining barcha kanallarga obuna bo'lganini tekshirish"""
    if channels is None:
        channels = list_global_channels()
    
    if not channels:
        return True
    
    for channel in channels:
        try:
            chat_member = bot_instance.get_chat_member(channel, user_id)
            if chat_member.status in ['left', 'kicked']:
                return False
        except Exception as e:
            print(f"Kanal tekshiruvida xato: {e}")
            return False
    return True

def create_subscription_markup(channels=None):
    """Obuna tugmalarini yaratish"""
    if channels is None:
        channels = list_global_channels()
    
    if not channels:
        return None
    
    markup = types.InlineKeyboardMarkup()
    for i, channel in enumerate(channels):
        try:
            markup.add(types.InlineKeyboardButton(
                f"📢 Kanal {i+1}", 
                url=f"https://t.me/{channel[1:] if channel.startswith('@') else channel}"
            ))
        except:
            markup.add(types.InlineKeyboardButton(
                f"📢 Kanal {i+1}", 
                url="https://t.me"
            ))
    
    markup.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check_subscription"))
    return markup

# ==================== YORDAMCHI FUNKSIYALAR ====================
def safe_edit_message_text(text, chat_id, message_id, reply_markup=None):
    """Xabarni tahrirlashda xatoliklarni ushlaydi"""
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
        return True
    except telebot.apihelper.ApiTelegramException as e:
        if "Bad Request: message to edit not found" in str(e):
            # Agar xabar topilmasa, yangi xabar yuboramiz
            bot.send_message(chat_id, text, reply_markup=reply_markup)
            return False
        else:
            print(f"API Xato (edit_message_text): {e}")
            return False
    except Exception as e:
        print(f"Umumiy Xato (edit_message_text): {e}")
        return False

def safe_answer_callback_query(callback_query_id, text=None, show_alert=False, url=None, cache_time=None):
    """Callback so'rovini javoblashda xatoliklarni ushlaydi"""
    try:
        bot.answer_callback_query(callback_query_id, text, show_alert, url, cache_time)
    except Exception as e:
        print(f"Callback javoblashda xato: {e}")

# ==================== ADMIN PANEL ====================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = str(message.from_user.id)
    
    # Global majburiy kanallarni olish
    global_channels = list_global_channels()
    
    # Majburiy obuna tekshiruvi
    if global_channels and not check_subscription(bot, user_id, global_channels):
        channels_text = "\n".join([f"🔹 {channel}" for channel in global_channels])
        response_text = f"⚠️ <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:</b>\n\n{channels_text}"
        
        markup = create_subscription_markup(global_channels)
        bot.reply_to(message, response_text, parse_mode='HTML', reply_markup=markup)
        return
    
    # Admin uchun maxsus menyular
    if user_id == ADMIN_ID:
        show_admin_menu(message)
    else:
        show_user_menu(message)

@bot.message_handler(commands=['addchannel'])
def add_channel_command(message):
    if str(message.from_user.id) != ADMIN_ID:
        bot.reply_to(message, "❌ Siz admin emassiz!")
        return
    
    msg = bot.reply_to(message, "🆔 Qo'shish uchun kanal username yoki ID sini kiriting (@username yoki -100123456789 formatida):")
    bot.register_next_step_handler(msg, process_add_channel)

def process_add_channel(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    channel = message.text.strip()
    if add_global_channel(channel):
        # Keshni yangilash
        list_global_channels.cache_clear()
        bot.reply_to(message, f"✅ Kanal qo'shildi: {channel}")
    else:
        bot.reply_to(message, f"⚠️ Bu kanal allaqachon qo'shilgan: {channel}")

@bot.message_handler(commands=['removechannel'])
def remove_channel_command(message):
    if str(message.from_user.id) != ADMIN_ID:
        bot.reply_to(message, "❌ Siz admin emassiz!")
        return
    
    msg = bot.reply_to(message, "🆔 O'chirish uchun kanal username yoki ID sini kiriting:")
    bot.register_next_step_handler(msg, process_remove_channel)

def process_remove_channel(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    channel = message.text.strip()
    if remove_global_channel(channel):
        # Keshni yangilash
        list_global_channels.cache_clear()
        bot.reply_to(message, f"✅ Kanal o'chirildi: {channel}")
    else:
        bot.reply_to(message, f"❌ Bu kanal topilmadi: {channel}")

@bot.message_handler(commands=['listchannels'])
def list_channels_command(message):
    if str(message.from_user.id) != ADMIN_ID:
        bot.reply_to(message, "❌ Siz admin emassiz!")
        return
    
    channels = list_global_channels()
    if channels:
        channels_text = "\n".join([f"{i+1}. {channel}" for i, channel in enumerate(channels)])
        bot.reply_to(message, f"📢 Majburiy obuna kanallari:\n\n{channels_text}")
    else:
        bot.reply_to(message, "📭 Hozircha kanal qo'shilmagan.")

def show_admin_menu(message):
    """Admin menyusi"""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ Yangi bot shabloni qo'shish", callback_data="admin_add_template"))
    markup.add(types.InlineKeyboardButton("📋 Mavjud shablonlar", callback_data="admin_list_templates"))
    markup.add(types.InlineKeyboardButton("🤖 Mening botlarim", callback_data="user_show_bots"))
    markup.add(types.InlineKeyboardButton("📢 Majburiy obuna", callback_data="admin_subscription_menu"))
    bot.send_message(message.chat.id, "🤖 Bot menejeri - Admin panel", reply_markup=markup)

def show_user_menu(message):
    """Oddiy foydalanuvchi menyusi"""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🤖 Botlar", callback_data="user_show_bots"))
    bot.send_message(message.chat.id, "🤖 Botlar menyusi:", reply_markup=markup)

# ==================== MAJBURIY OBUNA MENYUSI ====================
@bot.callback_query_handler(func=lambda call: call.data == "admin_subscription_menu" and str(call.from_user.id) == ADMIN_ID)
def admin_subscription_menu(call):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ Kanal qo'shish", callback_data="admin_add_channel"))
    markup.add(types.InlineKeyboardButton("📋 Kanallar ro'yxati", callback_data="admin_list_channels"))
    markup.add(types.InlineKeyboardButton("🗑️ Kanallarni tozalash", callback_data="admin_clear_channels"))
    markup.add(types.InlineKeyboardButton("🔙 Orqaga", callback_data="admin_main_menu"))
    
    safe_edit_message_text("📢 Majburiy obuna boshqaruvi:", call.message.chat.id, call.message.message_id, reply_markup=markup)
    safe_answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_add_channel" and str(call.from_user.id) == ADMIN_ID)
def admin_add_channel_callback(call):
    msg = bot.send_message(call.message.chat.id, "🆔 Qo'shish uchun kanal username yoki ID sini kiriting (@username yoki -100123456789 formatida):")
    bot.register_next_step_handler(msg, admin_process_add_channel)
    safe_answer_callback_query(call.id)

def admin_process_add_channel(message):
    if str(message.from_user.id) != ADMIN_ID:
        return
    
    channel = message.text.strip()
    if add_global_channel(channel):
        # Keshni yangilash
        list_global_channels.cache_clear()
        bot.reply_to(message, f"✅ Kanal qo'shildi: {channel}")
    else:
        bot.reply_to(message, f"⚠️ Bu kanal allaqachon qo'shilgan: {channel}")
    
    # Menyuga qaytish
    show_admin_menu(message)

@bot.callback_query_handler(func=lambda call: call.data == "admin_list_channels" and str(call.from_user.id) == ADMIN_ID)
def admin_list_channels_callback(call):
    channels = list_global_channels()
    if channels:
        channels_text = "\n".join([f"{i+1}. {channel}" for i, channel in enumerate(channels)])
        response_text = f"📢 Majburiy obuna kanallari:\n\n{channels_text}"
    else:
        response_text = "📭 Hozircha kanal qo'shilmagan."
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Orqaga", callback_data="admin_subscription_menu"))
    
    safe_edit_message_text(response_text, call.message.chat.id, call.message.message_id, reply_markup=markup)
    safe_answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_clear_channels" and str(call.from_user.id) == ADMIN_ID)
def admin_clear_channels_callback(call):
    clear_global_channels()
    safe_answer_callback_query(call.id, "✅ Barcha kanallar o'chirildi!")
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Orqaga", callback_data="admin_subscription_menu"))
    
    safe_edit_message_text("📢 Majburiy obuna boshqaruvi:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "check_subscription")
def check_subscription_callback(call):
    user_id = call.from_user.id
    global_channels = list_global_channels()
    
    if check_subscription(bot, user_id, global_channels):
        safe_edit_message_text(
            "✅ Barcha kanallarga obuna bo'ldingiz! Endi botdan foydalanishingiz mumkin.",
            call.message.chat.id, 
            call.message.message_id
        )
        # Foydalanuvchi menyusini ko'rsatish
        if str(user_id) == ADMIN_ID:
            show_admin_menu(call.message)
        else:
            show_user_menu(call.message)
    else:
        safe_answer_callback_query(call.id, "❌ Hali barcha kanallarga obuna bo'lmadingiz!", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "admin_main_menu" and str(call.from_user.id) == ADMIN_ID)
def admin_main_menu_callback(call):
    show_admin_menu(call.message)
    safe_answer_callback_query(call.id)

# ==================== ADMIN: SHABLONLARNI QO'SHISH ====================
@bot.callback_query_handler(func=lambda call: call.data == "admin_add_template" and str(call.from_user.id) == ADMIN_ID)
def admin_add_template_handler(call):
    msg = bot.send_message(call.message.chat.id, "📁 Bot shablon faylini yuboring (.py formatda):")
    bot.register_next_step_handler(msg, admin_handle_template_file)
    safe_answer_callback_query(call.id)

def admin_handle_template_file(message):
    if str(message.from_user.id) != ADMIN_ID:
        return

    if not message.document or not message.document.file_name.endswith('.py'):
        msg = bot.send_message(message.chat.id, "⚠️ Iltimos, faqat .py faylini yuboring!")
        bot.register_next_step_handler(msg, admin_handle_template_file)
        return

    # Faylni yuklab olamiz
    file_info = bot.get_file(message.document.file_id)
    downloaded_file = bot.download_file(file_info.file_path)

    # Faylni saqlash
    template_id = str(uuid.uuid4())
    template_path = f"bot_templates/{template_id}.py"

    with open(template_path, 'wb') as f:
        f.write(downloaded_file)

    msg = bot.send_message(message.chat.id, "📝 Shablon uchun nom kiriting:")
    bot.register_next_step_handler(msg, admin_get_template_name, template_id, template_path)

def admin_get_template_name(message, template_id, template_path):
    if str(message.from_user.id) != ADMIN_ID:
        return

    template_name = message.text

    # Shablonni ma'lumotlar bazasiga saqlash
    save_bot_template(template_id, template_name, template_path, os.path.basename(template_path))

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🏠 Bosh menyu", callback_data="admin_main_menu"))
    bot.send_message(message.chat.id, f"✅ '{template_name}' shabloni muvaffaqiyatli qo'shildi!", reply_markup=markup)

# ==================== ADMIN: SHABLONLAR RO'YXATI ====================
@bot.callback_query_handler(func=lambda call: call.data == "admin_list_templates" and str(call.from_user.id) == ADMIN_ID)
def admin_list_templates(call):
    bot_templates = load_bot_templates()
    if not bot_templates:
        bot.send_message(call.message.chat.id, "📭 Hozircha hech qanday shablon qo'shilmagan.")
        safe_answer_callback_query(call.id)
        return

    markup = types.InlineKeyboardMarkup()
    for template_id, template_data in bot_templates.items():
        btn = types.InlineKeyboardButton(f"📄 {template_data['name']}", callback_data=f"admin_view_template_{template_id}")
        markup.add(btn)

    markup.add(types.InlineKeyboardButton("🔙 Orqaga", callback_data="admin_main_menu"))
    safe_edit_message_text("📋 Mavjud shablonlar:", call.message.chat.id, call.message.message_id, reply_markup=markup)
    safe_answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_view_template_") and str(call.from_user.id) == ADMIN_ID)
def admin_view_template(call):
    template_id = call.data.split("_")[3]
    bot_templates = load_bot_templates()
    
    if template_id not in bot_templates:
        safe_answer_callback_query(call.id, "❌ Shablon topilmadi!")
        return

    template_data = bot_templates[template_id]

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🗑️ O'chirish", callback_data=f"admin_delete_template_{template_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Orqaga", callback_data="admin_list_templates"))

    response_text = f"📄 Shablon ma'lumotlari:\nNom: {template_data['name']}\nFayl: {template_data['filename']}"
    
    safe_edit_message_text(response_text, call.message.chat.id, call.message.message_id, reply_markup=markup)
    safe_answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_delete_template_") and str(call.from_user.id) == ADMIN_ID)
def admin_delete_template(call):
    template_id = call.data.split("_")[3]
    bot_templates = load_bot_templates()
    
    if template_id in bot_templates:
        template_data = bot_templates[template_id]
        # Faylni o'chirish
        try:
            if os.path.exists(template_data['path']):
                os.remove(template_data['path'])
        except Exception as e:
            print(f"Fayl o'chirishda xato: {e}")
        
        # Ma'lumotlar bazasidan o'chirish
        delete_bot_template(template_id)
        
        safe_answer_callback_query(call.id, "✅ Shablon o'chirildi!")
        # Orqaga qaytish
        call_data = type('obj', (object,), {'data': 'admin_list_templates', 'message': call.message, 'from_user': call.from_user})
        admin_list_templates(call_data)
    else:
        safe_answer_callback_query(call.id, "❌ Shablon topilmadi!")

# ==================== FOYDALANUVCHI: BOTLAR MENYUSI ====================
@bot.callback_query_handler(func=lambda call: call.data == "user_show_bots")
def user_show_bots(call):
    bot_templates = load_bot_templates()
    if not bot_templates:
        bot.send_message(call.message.chat.id, "📭 Hozircha hech qanday bot shabloni mavjud emas.")
        safe_answer_callback_query(call.id)
        return

    markup = types.InlineKeyboardMarkup()
    for template_id, template_data in bot_templates.items():
        btn = types.InlineKeyboardButton(f"🤖 {template_data['name']}", callback_data=f"user_select_template_{template_id}")
        markup.add(btn)

    back_data = "admin_main_menu" if str(call.from_user.id) == ADMIN_ID else "user_back_to_main"
    markup.add(types.InlineKeyboardButton("🔙 Orqaga", callback_data=back_data))
    
    safe_edit_message_text("📋 Mavjud botlar:", call.message.chat.id, call.message.message_id, reply_markup=markup)
    safe_answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("user_select_template_"))
def user_select_template(call):
    template_id = call.data.split("_")[3]
    bot_templates = load_bot_templates()
    
    if template_id not in bot_templates:
        safe_answer_callback_query(call.id, "❌ Shablon topilmadi!")
        return

    template_data = bot_templates[template_id]

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🚀 Yangi bot yaratish", callback_data=f"user_create_bot_{template_id}"))

    # Agar foydalanuvchi allaqachon bot yaratgan bo'lsa
    user_bots = load_user_bots()
    user_bots_count = len([ub for ub in user_bots.values() if ub['template_id'] == template_id])
    if user_bots_count > 0:
        markup.add(types.InlineKeyboardButton("⚙️ Mening botlarim", callback_data=f"user_my_bots_{template_id}"))

    markup.add(types.InlineKeyboardButton("🔙 Orqaga", callback_data="user_show_bots"))

    response_text = f"📄 Bot: {template_data['name']}\nFayl: {template_data['filename']}\n\nTanlang:"
    
    safe_edit_message_text(response_text, call.message.chat.id, call.message.message_id, reply_markup=markup)
    safe_answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("user_create_bot_"))
def user_create_bot(call):
    # Avval majburiy obunani tekshirish
    user_id = str(call.from_user.id)
    global_channels = list_global_channels()
    
    if global_channels and not check_subscription(bot, user_id, global_channels):
        channels_text = "\n".join([f"🔹 {channel}" for channel in global_channels])
        response_text = f"⚠️ <b>Bot yaratish uchun quyidagi kanallarga obuna bo'ling:</b>\n\n{channels_text}"
        
        markup = create_subscription_markup(global_channels)
        bot.send_message(call.message.chat.id, response_text, parse_mode='HTML', reply_markup=markup)
        safe_answer_callback_query(call.id)
        return
    
    template_id = call.data.split("_")[3]
    bot_templates = load_bot_templates()
    
    if template_id not in bot_templates:
        safe_answer_callback_query(call.id, "❌ Shablon topilmadi!")
        return

    # Avval token so'raymiz
    msg = bot.send_message(call.message.chat.id, "🔑 Yangi bot uchun token kiriting:")
    bot.register_next_step_handler(msg, user_get_bot_token, template_id)
    safe_answer_callback_query(call.id)

def user_get_bot_token(message, template_id):
    user_token = message.text.strip()

    # Endi admin ID so'raymiz
    msg = bot.send_message(message.chat.id, "🆔 Yangi bot uchun admin ID kiriting (agar kerak bo'lmasa 'yoq' deb yozing):")
    bot.register_next_step_handler(msg, user_get_admin_id, template_id, user_token)

def user_get_admin_id(message, template_id, user_token):
    admin_id = message.text.strip()
    if admin_id.lower() in ['yoq', 'yo\'q', 'no', 'none', '']:
        admin_id = None

    # Bot yaratish
    result = create_user_bot_from_template(template_id, user_token, admin_id)

    if result:
        # Botni ma'lumotlar bazasiga saqlash
        save_user_bot(result['id'], template_id, user_token, admin_id, result['path'])
        
        # Global kanallarni botga bog'lash
        global_channels = list_global_channels()
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    for channel in global_channels:
                        cur.execute("""
                            INSERT INTO bot_channels (bot_id, channel_identifier)
                            VALUES (%s, %s)
                            ON CONFLICT DO NOTHING
                        """, (result['id'], channel))
                    conn.commit()
        except Exception as e:
            print(f"Bot kanallarini saqlashda xatolik: {e}")

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⚙️ Botni boshqarish", callback_data=f"user_manage_bot_{result['id']}"))
        markup.add(types.InlineKeyboardButton("🔙 Botlar ro'yxati", callback_data="user_show_bots"))

        admin_info = f"Admin ID: {admin_id}" if admin_id else "Admin ID: yo'q"
        channels_info = len(global_channels)
        bot.send_message(message.chat.id,
                        f"✅ Bot muvaffaqiyatli yaratildi va ishga tushdi!\n"
                        f"Token: {user_token[:15]}...\n"
                        f"{admin_info}\n"
                        f"📢 Majburiy kanallar: {channels_info} ta",
                        reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "❌ Xatolik yuz berdi! Bot yaratilmadi.")

# ==================== FOYDALANUVCHI: MENING BOTLARIM ====================
@bot.callback_query_handler(func=lambda call: call.data.startswith("user_my_bots_"))
def user_my_bots(call):
    template_id = call.data.split("_")[3]
    bot_templates = load_bot_templates()
    user_bots = load_user_bots()

    # Foydalanuvchining ushbu shablondan yaratgan botlari
    my_bots = {k: v for k, v in user_bots.items() if v['template_id'] == template_id}

    if not my_bots:
        bot.send_message(call.message.chat.id, "📭 Siz hali bot yaratmagansiz.")
        safe_answer_callback_query(call.id)
        return

    markup = types.InlineKeyboardMarkup()
    for bot_id, bot_data in my_bots.items():
        token_preview = bot_data['token'][:15] + "..."
        admin_info = bot_data['admin_id'] if bot_data['admin_id'] else "yo'q"
        channels_info = len(bot_data['channels'])
        btn_text = f"🔧 {token_preview} (Admin: {admin_info[:10]}..., 📢{channels_info})"
        btn = types.InlineKeyboardButton(btn_text, callback_data=f"user_manage_bot_{bot_id}")
        markup.add(btn)

    markup.add(types.InlineKeyboardButton("🔙 Orqaga", callback_data=f"user_select_template_{template_id}"))
    
    safe_edit_message_text("⚙️ Sizning botlaringiz:", call.message.chat.id, call.message.message_id, reply_markup=markup)
    safe_answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("user_manage_bot_"))
def user_manage_bot(call):
    bot_id = call.data.split("_")[3]
    user_bots = load_user_bots()
    
    if bot_id not in user_bots:
        safe_answer_callback_query(call.id, "❌ Bot topilmadi!")
        return

    bot_data = user_bots[bot_id]
    bot_templates = load_bot_templates()

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🛑 To'xtatish", callback_data=f"user_stop_bot_{bot_id}"))
    markup.add(types.InlineKeyboardButton("🗑️ O'chirish", callback_data=f"user_delete_bot_{bot_id}"))

    template_name = bot_templates[bot_data['template_id']]['name']
    markup.add(types.InlineKeyboardButton("🔙 Orqaga", callback_data=f"user_my_bots_{bot_data['template_id']}"))

    token_preview = bot_data['token'][:20] + "..."
    admin_info = bot_data['admin_id'] if bot_data['admin_id'] else "yo'q"

    response_text = f"🔧 Bot boshqaruvi:\nShablon: {template_name}\nToken: {token_preview}\nAdmin ID: {admin_info}"
    
    safe_edit_message_text(response_text, call.message.chat.id, call.message.message_id, reply_markup=markup)
    safe_answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("user_stop_bot_"))
def user_stop_bot(call):
    bot_id = call.data.split("_")[3]
    user_bots = load_user_bots()
    
    if bot_id in user_bots:
        try:
            if user_bots[bot_id]['process']:
                user_bots[bot_id]['process'].terminate()
            safe_answer_callback_query(call.id, "✅ Bot to'xtatildi!")
        except Exception as e:
            print(f"Bot to'xtatishda xato: {e}")
            safe_answer_callback_query(call.id, "⚠️ Xatolik yuz berdi!")
    else:
        safe_answer_callback_query(call.id, "❌ Bot topilmadi!")

@bot.callback_query_handler(func=lambda call: call.data.startswith("user_delete_bot_"))
def user_delete_bot(call):
    bot_id = call.data.split("_")[3]
    user_bots = load_user_bots()
    
    if bot_id in user_bots:
        try:
            # Jarayonni to'xtatish
            if user_bots[bot_id]['process']:
                user_bots[bot_id]['process'].terminate()
            # Faylni o'chirish
            if os.path.exists(user_bots[bot_id]['path']):
                os.remove(user_bots[bot_id]['path'])
            # Ma'lumotlar bazasidan o'chirish
            delete_user_bot(bot_id)
            safe_answer_callback_query(call.id, "✅ Bot o'chirildi!")
        except Exception as e:
            print(f"Bot o'chirishda xato: {e}")
            safe_answer_callback_query(call.id, f"❌ Xatolik: {str(e)}")
    else:
        safe_answer_callback_query(call.id, "❌ Bot topilmadi!")

@bot.callback_query_handler(func=lambda call: call.data == "user_back_to_main")
def user_back_to_main(call):
    show_user_menu(call.message)
    safe_answer_callback_query(call.id)

# ==================== UNIVERSAL TOKEN, ADMIN ID VA KANALLAR QO'LLAB-QUVVATLASH ====================
def inject_token_and_admin_id_universal(content, token, admin_id=None):
    """
    Har qanday token, admin ID funksiyasini qo'llab-quvvatlaydi
    """

    # 1. Oddiy TOKEN = "..." usuli
    content = re.sub(r'(TOKEN\s*=\s*["\']).*?(["\'])', f'TOKEN = "{token}"', content, flags=re.IGNORECASE)

    # 2. BOT_TOKEN = "..." usuli
    content = re.sub(r'(BOT_TOKEN\s*=\s*["\']).*?(["\'])', f'BOT_TOKEN = "{token}"', content, flags=re.IGNORECASE)

    # 3. API_TOKEN = "..." usuli
    content = re.sub(r'(API_TOKEN\s*=\s*["\']).*?(["\'])', f'API_TOKEN = "{token}"', content, flags=re.IGNORECASE)

    # 4. token = "..." usuli (kichik harf)
    content = re.sub(r'(token\s*=\s*["\']).*?(["\'])', f'token = "{token}"', content)

    # 5. BOT_API_TOKEN = "..." usuli
    content = re.sub(r'(BOT_API_TOKEN\s*=\s*["\']).*?(["\'])', f'BOT_API_TOKEN = "{token}"', content, flags=re.IGNORECASE)

    # 6. ADMIN_ID = ... usullari
    if admin_id:
        # ADMIN_ID = "..." usuli
        content = re.sub(r"(ADMIN_ID\s*=\s*['\"]).*?(['\"])", f"ADMIN_ID = '{admin_id}'", content, flags=re.IGNORECASE)
        # ADMIN_ID = ... (raqam) usuli
        content = re.sub(r'(ADMIN_ID\s*=\s*)\d+', f'ADMIN_ID = {admin_id}', content, flags=re.IGNORECASE)
        # admin_id = "..." usuli
        content = re.sub(r"(admin_id\s*=\s*['\"]).*?(['\"])", f"admin_id = '{admin_id}'", content)
    else:
        # Agar admin_id berilmagan bo'lsa, mavjud admin_id larni o'chiramiz
        content = re.sub(r'^.*ADMIN_ID\s*=.*$', '', content, flags=re.MULTILINE | re.IGNORECASE)
        content = re.sub(r'^.*admin_id\s*=.*$', '', content, flags=re.MULTILINE)

    # 7. Agar hech qanday token topilmasa, yangi TOKEN qo'shamiz
    if not re.search(r'(TOKEN|BOT_TOKEN|API_TOKEN|token|BOT_API_TOKEN)\s*=', content, re.IGNORECASE):
        # import qatoridan keyin qo'shamiz
        import_match = re.search(r'^.*import.*$', content, re.MULTILINE)
        if import_match:
            insert_pos = import_match.end() + 1
            content = content[:insert_pos] + f'\nTOKEN = "{token}"\n' + content[insert_pos:]
        else:
            # Agar import topilmasa, fayl boshiga qo'shamiz
            content = f'TOKEN = "{token}"\n' + content

    content = f'TOKEN = "{token}"\n' + content

    # 8. Agar admin_id kerak bo'lsa va topilmasa, qo'shamiz
    if admin_id and not re.search(r'(ADMIN_ID|admin_id)\s*=', content, re.IGNORECASE):
        # import qatoridan keyin qo'shamiz
        import_match = re.search(r'^.*import.*$', content, re.MULTILINE)
        if import_match:
            insert_pos = import_match.end() + 1
            content = content[:insert_pos] + f"\nADMIN_ID = '{admin_id}'\n" + content[insert_pos:]
        else:
            content = f"ADMIN_ID = '{admin_id}'\n" + content

    return content

# ==================== BOT YARATISH FUNKSIYASI ====================
def create_user_bot_from_template(template_id, user_token, admin_id=None):
    bot_templates = load_bot_templates()
    
    if template_id not in bot_templates:
        return None

    template_path = bot_templates[template_id]['path']

    # Template faylni o'qish
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()
    except Exception as e:
        print(f"Shablon faylni o'qishda xato: {e}")
        return None

    # Universal token, admin ID qo'yish
    updated_content = inject_token_and_admin_id_universal(template_content, user_token, admin_id)

    # Yangi bot faylini yaratish
    bot_instance_id = str(uuid.uuid4())
    bot_path = f"user_bots/{bot_instance_id}.py"

    try:
        with open(bot_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)
    except Exception as e:
        print(f"Yangi bot faylini yaratishda xato: {e}")
        return None

    # Fonda ishga tushirish
    try:
        process = subprocess.Popen(["python3", bot_path])
        return {
            'process': process,
            'path': bot_path,
            'id': bot_instance_id
        }
    except Exception as e:
        print(f"Botni ishga tushirishda xato: {e}")
        return None

# ==================== DASTURNI ISHGA TUSHIRISH ====================
if __name__ == "__main__":
    print("Bot menejeri ishga tushmoqda...")
    
    # Ma'lumotlar bazasini sozlash
    init_database()
    
    print("Ma'lumotlar bazasi sozlandi")
    print("Qo'llab-quvvatlanadigan buyruqlar:")
    print("/addchannel - Majburiy obuna kanali qo'shish (faqat admin)")
    print("/removechannel - Majburiy obuna kanalini o'chirish (faqat admin)")
    print("/listchannels - Majburiy obuna kanallarini ko'rish (faqat admin)")
    print("\nCallback tugmalar orqali ham boshqarish mumkin")
    
    try:
        bot.polling()
    except Exception as e:
        print(f"Bot pollingda xato: {e}")
