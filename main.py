import os
import json
import asyncio
import re
import logging
import aiofiles
import random
from datetime import datetime
from telethon import TelegramClient, events, Button, types
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, 
    PhoneCodeInvalidError, 
    PhoneCodeExpiredError,
    FloodWaitError,
    PhoneNumberInvalidError
)
import aiosqlite

# ==========================================
# تنظیمات (تنظیمات خود را اینجا وارد کنید)
# ==========================================
API_ID = 29493929  # ApiID
API_HASH = '8c7b2d8c9fae7d4e4ae7e75cddc838e7'  # ApiHash
BOT_TOKEN = '8069757548:AAEX_yCgEabWCi6JhWOo2C2PwLxJ3JE9wYE'  # Token Bot
ADMIN_ID = 7349237747  # شناسه عددی ادمین

# تنظیمات کانال‌ها
BACKUP_CHANNEL = "t.me/backup2024p"
REQUESTS_CHANNEL = "@Zero_Receiver"
SETTLEMENT_CHANNEL = "@deposit2024p"

# مسیر فایل‌ها
PRICES_FILE = 'settings/prices.json'
COUNTRY_CODES_FILE = 'settings/country_codes.json'
SESSIONS_FOLDER = 'sessions'

# تنظیمات لاگینگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==========================================
# توابع کمکی (Helper Functions)
# ==========================================
def random_sleep(min_sec=1, max_sec=3):
    """تولید مکث تصادفی برای رفتار انسانی"""
    time_to_sleep = random.uniform(min_sec, max_sec)
    return asyncio.sleep(time_to_sleep)

def parse_proxy_string(proxy_str):
    """
    تبدیل متن پروکسی به تاپل مورد نیاز تلگرام.
    فرمت‌های پشتیبانی شده:
    ip:port
    ip:port:user:pass
    socks5://ip:port:user:pass
    http://ip:port
    """
    proxy_str = proxy_str.strip()
    if not proxy_str:
        return None
    
    # بررسی پروتکل
    proxy_type = None
    if proxy_str.startswith('socks5://'):
        proxy_type = 'socks5'
        proxy_str = proxy_str.replace('socks5://', '')
    elif proxy_str.startswith('http://'):
        proxy_type = 'http'
        proxy_str = proxy_str.replace('http://', '')
    else:
        # اگر پروتکسی ذکر نشده بود، بررسی کن یوزر پسورد دارد یا نه
        # اگر یوزر پسورد داشت احتمالا ساکس 5 است، والا اچ تی تی پی
        if ':' in proxy_str and proxy_str.count(':') >= 3:
            proxy_type = 'socks5'
        else:
            proxy_type = 'http' # پیش‌فرض

    parts = proxy_str.split(':')
    
    # حالت ip:port
    if len(parts) == 2:
        return (proxy_type, parts[0], int(parts[1]), True)
    
    # حالت ip:port:user:pass
    elif len(parts) == 4:
        return (proxy_type, parts[0], int(parts[1]), True, parts[2], parts[3])
    
    return None

# ==========================================
# کلاس مدیریت دیتابیس (Database Manager)
# ==========================================
class Database:
    def __init__(self, db_name="bot_data.db"):
        self.db_name = db_name

    async def init_db(self):
        async with aiosqlite.connect(self.db_name) as db:
            # جدول کاربران
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    join_date TEXT,
                    number_count INTEGER DEFAULT 0,
                    balance REAL DEFAULT 0,
                    fullname TEXT,
                    card_number TEXT,
                    wallet_number TEXT
                )
            """)
            # جدول درخواست‌ها
            await db.execute("""
                CREATE TABLE IF NOT EXISTS requests (
                    prefix TEXT PRIMARY KEY,
                    required_count INTEGER DEFAULT 0,
                    country_name TEXT,
                    flag_emoji TEXT
                )
            """)
            # جدول شماره‌ها
            await db.execute("""
                CREATE TABLE IF NOT EXISTS numbers (
                    phone_number TEXT PRIMARY KEY,
                    user_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    country_code TEXT,
                    registered_at TEXT
                )
            """)
            # جدول پروکسی‌ها (جدید)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS proxies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proxy_string TEXT NOT NULL,
                    added_at TEXT
                )
            """)
            await db.commit()
        logger.info("✅ Database initialized successfully.")

    # --- مدیریت کاربران ---
    async def add_or_update_user(self, user_id, first_name, username):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("""
                INSERT INTO users (user_id, first_name, username, join_date)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                first_name=excluded.first_name,
                username=excluded.username
            """, (user_id, first_name, username, str(datetime.now())))
            await db.commit()

    async def get_user(self, user_id):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cursor:
                return await cursor.fetchone()

    async def settle_balance(self, user_id):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("UPDATE users SET balance = 0 WHERE user_id = ?", (user_id,))
            await db.commit()

    async def update_user_bank_info(self, user_id, fullname, card_number, wallet_number):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("""
                UPDATE users SET fullname=?, card_number=?, wallet_number=?
                WHERE user_id=?
            """, (fullname, card_number, wallet_number, user_id))
            await db.commit()

    # --- مدیریت شماره‌ها ---
    async def is_number_globally_exists(self, phone_number):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT 1 FROM numbers WHERE phone_number=?", (phone_number,)) as cursor:
                return await cursor.fetchone() is not None

    async def register_number(self, user_id, phone_number, country_code):
        async with aiosqlite.connect(self.db_name) as db:
            try:
                await db.execute("""
                    INSERT INTO numbers (phone_number, user_id, status, country_code, registered_at)
                    VALUES (?, ?, 'pending', ?, ?)
                """, (phone_number, user_id, country_code, str(datetime.now())))
                await db.execute("UPDATE users SET number_count = number_count + 1 WHERE user_id = ?", (user_id,))
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def verify_and_credit(self, user_id, phone_number, amount):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("UPDATE numbers SET status='verified' WHERE phone_number=?", (phone_number,))
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
            await db.commit()
            async with db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    # --- مدیریت درخواست‌ها ---
    async def save_request(self, prefix, country_name, flag, count, user_id):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("""
                INSERT OR REPLACE INTO requests (prefix, required_count, country_name, flag_emoji)
                VALUES (?, ?, ?, ?)
            """, (prefix, count, country_name, flag))
            await db.commit()
            
    async def get_all_requests(self):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT * FROM requests ORDER BY prefix ASC") as cursor:
                rows = await cursor.fetchall()
            return rows

    async def update_request_state(self, prefix):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT required_count FROM requests WHERE prefix=?", (prefix,)) as cursor:
                row = await cursor.fetchone()
                if not row: return False
                if row[0] > 1:
                    await db.execute("UPDATE requests SET required_count = required_count - 1 WHERE prefix=?", (prefix,))
                    await db.commit()
                    return False
                else:
                    await db.execute("DELETE FROM requests WHERE prefix=?", (prefix,))
                    await db.commit()
                    return True

    # --- مدیریت پروکسی‌ها ---
    async def add_proxy(self, proxy_str):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("INSERT INTO proxies (proxy_string, added_at) VALUES (?, ?)", (proxy_str, str(datetime.now())))
            await db.commit()

    async def add_proxies_bulk(self, proxy_list):
        async with aiosqlite.connect(self.db_name) as db:
            for p in proxy_list:
                try:
                    await db.execute("INSERT INTO proxies (proxy_string, added_at) VALUES (?, ?)", (p, str(datetime.now())))
                except:
                    pass
            await db.commit()

    async def get_random_proxy(self):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT proxy_string FROM proxies ORDER BY RANDOM() LIMIT 1") as cursor:
                row = await cursor.fetchone()
                if row:
                    return parse_proxy_string(row[0])
        return None 

    async def get_proxy_count(self):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT COUNT(*) FROM proxies") as cursor:
                row = await cursor.fetchone()
            return row[0] if row else 0

    async def clear_proxies(self):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("DELETE FROM proxies")
            await db.commit()

    async def get_total_users(self):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cursor:
                row = await cursor.fetchone()
            return row[0] if row else 0

# ==========================================
# کلاس اصلی ربات (BotHandler)
# ==========================================
class BotHandler:
    def __init__(self):
        self.bot = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
        self.db = Database()
        self.country_codes = self.load_json_file(COUNTRY_CODES_FILE, {})
        self.prices = self.load_json_file(PRICES_FILE, {})
        os.makedirs(SESSIONS_FOLDER, exist_ok=True)

    def load_json_file(self, path, default):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading {path}: {e}")
                return default
        return default

    def get_country_code(self, phone_number):
        for code in self.country_codes.keys():
            if phone_number.startswith(code):
                return self.country_codes[code]
        return None

    # --- پنل ادمین ---
    async def send_admin_panel(self, event):
        count = await self.db.get_proxy_count()
        req_count = len(await self.db.get_all_requests())
        total_users = await self.db.get_total_users()
        
        text = f"""
        🛠 **پنل مدیریت ادمین:**
        ────────────────
        👥 کاربران: {total_users}
        📩 درخواست‌های باز: {req_count}
        🌐 پروکسی‌های فعال: {count}
        ────────────────
        """
        buttons = [
            [Button.inline("📦 مدیریت درخواست‌ها", b"manage_requests")],
            [Button.inline("🌐 مدیریت پروکسی‌ها", b"proxy_menu")],
            [Button.inline("📊 آمار دقیق", b"stats_detail")],
        ]
        await event.edit(text, buttons=buttons)

    async def proxy_menu_handler(self, event):
        count = await self.db.get_proxy_count()
        text = f"🌐 **مدیریت پروکسی‌ها**\nتعداد پروکسی در سیستم: {count} عدد"
        buttons = [
            [Button.inline("➕ افزودن تکی", b"add_single_proxy")],
            [Button.inline("📂 آپلود فایل لیست", b"upload_proxy_file")],
            [Button.inline("🗑 حذف همه پروکسی‌ها", b"clear_proxies")],
            [Button.inline("🔙 بازگشت به پنل", b"back_to_panel")]
        ]
        await event.edit(text, buttons=buttons)

    async def add_single_proxy_flow(self, event):
        async with self.bot.conversation(ADMIN_ID, timeout=120) as conv:
            await conv.send_message("📝 **لطفاً پروتکل و آدرس پروکسی را ارسال کنید.**\n\nمثال:\n`socks5://ip:port:user:pass`\n`http://ip:port`\n`ip:port`", parse_mode='markdown')
            resp = await conv.get_response()
            proxy_str = resp.text.strip()
            
            parsed = parse_proxy_string(proxy_str)
            if parsed:
                await self.db.add_proxy(proxy_str)
                await event.respond("✅ پروکسی با موفقیت اضافه شد.")
                await self.send_admin_panel(await event.respond("بروزرسانی..."))
            else:
                await event.respond("❌ فرمت پروکسی اشتباه است.")
                await self.proxy_menu_handler(event)

    async def upload_proxy_file_flow(self, event):
        async with self.bot.conversation(ADMIN_ID, timeout=300) as conv:
            await conv.send_message("📂 **لطفاً فایل متنی (.txt) حاوی لیست پروکسی‌ها را ارسال کنید.**\n(هر خط یک پروکسی)")
            file_msg = await conv.get_response()
            
            if file_msg.file:
                path = await file_msg.download_media()
                try:
                    with open(path, 'r') as f:
                        lines = f.read().splitlines()
                    
                    valid_proxies = []
                    for line in lines:
                        if parse_proxy_string(line.strip()):
                            valid_proxies.append(line.strip())
                    
                    if valid_proxies:
                        await self.db.add_proxies_bulk(valid_proxies)
                        await conv.send_message(f"✅ {len(valid_proxies)} پروکسی از فایل استخراج و ذخیره شدند.")
                        os.remove(path)
                        await self.send_admin_panel(await conv.send_message("بازگشت..."))
                    else:
                        await conv.send_message("⚠️ هیچ پروکسی معتبری در فایل یافت نشد.")
                        await self.proxy_menu_handler(await conv.send_message("بازگشت..."))
                except Exception as e:
                    await conv.send_message(f"❌ خطا در خواندن فایل: {e}")
            else:
                await conv.send_message("❌ فایلی ارسال نشد.")

    # --- مدیریت شماره‌ها ---
    async def background_verification_task(self, user_id, phone_number, country_code):
        try:
            await asyncio.sleep(600) 
            prefix = phone_number[:3]
            price = self.prices.get(prefix, 0)
            
            if price > 0:
                new_balance = await self.db.verify_and_credit(user_id, phone_number, price)
                request_completed = await self.db.update_request_state(prefix)
                
                try:
                    await self.bot.send_message(user_id, f"🎉 **شماره تایید شد و {price} تومان اضافه گردید.**\n💰 موجودی: {new_balance} تومان")
                    if request_completed:
                        logger.info(f"Request {prefix} completed and removed from DB.")
                except Exception as e:
                    logger.error(f"Error sending success msg to {user_id}: {e}")
        except Exception as e:
            logger.error(f"Background task failed for {phone_number}: {e}")

    async def login_user(self, event):
        user_id = event.sender_id
        async with self.bot.conversation(event.sender_id, timeout=300) as conv:
            await conv.send_message("📱 **لطفاً شماره تلفن خود را با فرمت + وارد کنید:**")
            phone_message = await conv.get_response()
            phone_number = phone_message.text.strip()

            if not re.match(r'^\+[1-9]\d{1,14}$', phone_number):
                await conv.send_message("❌ **فرمت شماره اشتباه است. (مثال: +98912...)**")
                return

            is_duplicate = await self.db.is_number_globally_exists(phone_number)
            if is_duplicate:
                await conv.send_message("⚠️ **این شماره قبلاً در سیستم ثبت شده است.**")
                return

            country_code = self.get_country_code(phone_number)
            if not country_code:
                await conv.send_message("❌ **کشور مربوط به این پیش‌شماره پشتیبانی نمی‌شود.**")
                return

            session_folder = os.path.join(SESSIONS_FOLDER, country_code)
            os.makedirs(session_folder, exist_ok=True)
            
            proxy = await self.db.get_random_proxy()
            
            if proxy:
                logger.info(f"Using Proxy for {phone_number}: {proxy[0]}")
            else:
                logger.info(f"No Proxy found for {phone_number}, connecting directly.")

            user_client = TelegramClient(
                StringSession(), 
                API_ID, 
                API_HASH,
                proxy=proxy,
                device_model="Samsung S21",
                system_version="Android 12",
                app_version="10.0.1",
                lang_code="en"
            )
            
            await user_client.connect()
            
            try:
                try:
                    await user_client.send_code_request(phone_number)
                    await random_sleep(2, 4)
                except FloodWaitError as e:
                    await conv.send_message(f"⏳ **تلگرام محدودیت اعمال کرد. لطفاً {e.seconds} ثانیه صبر کنید.**")
                    return
                except PhoneNumberInvalidError:
                    await conv.send_message("❌ **شماره معتبر نیست.**")
                    return

                await conv.send_message("🔑 **کد تأیید ارسال شد. لطفاً کد را وارد کنید:**")
                code_message = await conv.get_response()
                code = code_message.text.strip()
                
                await random_sleep(1, 3)
                
                try:
                    await user_client.sign_in(phone=phone_number, code=code)
                except SessionPasswordNeededError:
                    await conv.send_message("🔒 **این شماره دارای رمز دوم است. لطفاً رمز دوم را وارد کنید:**")
                    pwd_message = await conv.get_response()
                    password = pwd_message.text.strip()
                    await user_client.sign_in(password=password)
                except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
                    await conv.send_message("❌ **کد اشتباه یا منقضی شده است.**")
                    logger.info(f"Login failed for {user_id}: {e}")
                    return
                except FloodWaitError as e:
                     await conv.send_message(f"⏳ **لطفاً {e.seconds} ثانیه صبر کنید.**")
                     return
                
                success = await self.db.register_number(user_id, phone_number, country_code)
                if not success:
                    await conv.send_message("⚠️ **خطای دیتابیس در ثبت نهایی.**")
                    return

                session_string = user_client.session.save()
                session_path = os.path.join(session_folder, f'{phone_number[1:]}.session')
                async with aiofiles.open(session_path, 'w', encoding='utf-8') as session_file:
                    await session_file.write(session_string)

                await conv.send_message("✅ **شماره با موفقیت ثبت شد.**\n⏳ **تا 10 دقیقه دیگر تایید و تسویه می‌شود.**")
                
                message_text = f"📱 **شماره ثبت‌شده:** {phone_number}\n👤 کاربر: {user_id}\n🔑 کشور: {country_code}"
                await self.bot.send_message(BACKUP_CHANNEL, message_text)
                await self.bot.send_file(BACKUP_CHANNEL, session_path, caption=f"📂 Session: {phone_number}")

                asyncio.create_task(self.background_verification_task(user_id, phone_number, country_code))

            except Exception as e:
                logger.error(f"Unexpected Login error for {user_id}: {e}")
                await conv.send_message(f"❌ **خطای سیستمی:** {str(e)}")
            finally:
                await user_client.disconnect()

    async def request_numbers_admin(self, event):
        user_id = event.sender_id
        if user_id != ADMIN_ID:
            await event.respond("🚫 **دسترسی غیرمجاز.**")
            return
            
        async with self.bot.conversation(event.sender_id, timeout=300) as conv:
            await conv.send_message("📞 **پیش‌شماره را وارد کنید (مثال: +98):**")
            prefix_msg = await conv.get_response()
            prefix = prefix_msg.text.strip()

            await conv.send_message("🌍 **نام کشور:**")
            country_msg = await conv.get_response()
            country = country_msg.text.strip()

            await conv.send_message("🚩 **ایموجی پرچم:**")
            flag_msg = await conv.get_response()
            flag = flag_msg.text.strip()

            await conv.send_message("🔢 **تعداد مورد نیاز:**")
            count_msg = await conv.get_response()
            try:
                count = int(count_msg.text.strip())
            except ValueError:
                await conv.send_message("❌ لطفاً عدد وارد کنید.")
                return

            await self.db.save_request(prefix, country, flag, count, user_id)

            price = self.prices.get(prefix, "نامشخص")
            msg = f"{flag} {country} ({prefix})\nPrice: {price} IRT"
            btn = Button.url("ورود به ربات", "https://t.me/Zero_Receiver_bot")
            await self.bot.send_message(REQUESTS_CHANNEL, msg, buttons=[btn])
            await conv.send_message(f"✅ درخواست ثبت شد.")

    async def request_list(self, event):
        requests = await self.db.get_all_requests()
        if not requests:
            await event.respond("هیچ درخواستی موجود نیست.")
            return
        msg = "**لیست درخواست‌های فعال:**\n\n"
        for req in requests:
            msg += f"{req[3]} {req[2]} ({req[0]}) - {req[1]} عدد\n/////////////////////////////////////////\n"
        btn = Button.url("کانال اطلاعیه", "https://t.me/Zero_Receiver")
        await event.respond(msg, buttons=[btn])

    async def collect_user_information(self, event):
        user_id = event.sender_id
        user_data = await self.db.get_user(user_id)
        if user_data and user_data[6] and user_data[7] and user_data[8]: 
            await event.respond("✅ **اطلاعات شما قبلاً ثبت شده است.**")
            return
        async with self.bot.conversation(user_id, timeout=300) as conv:
            await conv.send_message("📝 **نام کامل:**")
            full = (await conv.get_response()).text.strip()
            await conv.send_message("💳 **شماره کارت (16 رقم):**")
            card = (await conv.get_response()).text.strip()
            if not re.match(r'^\d{16}$', card):
                await conv.send_message("❌ شماره کارت باید 16 رقم باشد.")
                return
            await conv.send_message("💼 **شماره کیف پول (ولت):**")
            wallet = (await conv.get_response()).text.strip()
            await self.db.update_user_bank_info(user_id, full, card, wallet)
            await conv.send_message("✅ اطلاعات حساب ذخیره شد.")

    async def settle_handler(self, event):
        user_id = event.sender_id
        user_data = await self.db.get_user(user_id)
        if not user_data: await event.respond("⚠️ ابتدا استارت را بزنید."); return
        if not user_data[6] or not user_data[7] or not user_data[8]:
             await event.respond("⚠️ لطفاً ابتدا اطلاعات حساب را با دستور /information تکمیل کنید.")
             return
        balance = user_data[5]
        if balance > 0:
            async with self.bot.conversation(user_id, timeout=300) as conv:
                text = f"**درخواست تسویه:** 💵 {balance} تومان\nاگر موافقید دکمه زیر را بزنید:"
                btn = Button.inline("✅ قبول می‌کنم", b'accept_settle')
                await conv.send_message(text, buttons=[btn])
                res = await conv.wait_event(events.CallbackQuery(data=b'accept_settle'))
                if res:
                    fullname, card, wallet = user_data[6], user_data[7], user_data[8]
                    report = f"📊 درخواست تسویه:\n👤 نام: {fullname}\n💳 کارت: {card}\n💰 مبلغ: {balance} تومان\n🆔 آیدی: {user_id}"
                    await self.bot.send_message(SETTLEMENT_CHANNEL, report)
                    await self.db.settle_balance(user_id)
                    await event.respond("✅ درخواست تسویه ثبت شد.")
        else:
            await event.respond("❌ موجودی کافی نیست.")

    async def display_account_info(self, event):
        user_id = event.sender_id
        user_data = await self.db.get_user(user_id)
        if not user_data: await event.respond("اطلاعاتی یافت نشد."); return
        text = f"📋 **اطلاعات حساب:**\n🔤 نام کاربری: {user_data[2]}\n🆔 آیدی: {user_data[0]}\n📅 تاریخ: {user_data[3]}\n🔢 شماره‌ها: {user_data[4]}\n💰 موجودی: {user_data[5]} تومان"
        try:
            btn = Button.url("💬 پشتیبانی", "https://t.me/ZeroReceiversup")
            await self.bot.send_file(event.sender_id, 'settings/user.jpg', caption=text, buttons=[btn])
        except:
            await event.respond(text)

    async def run(self):
        await self.db.init_db()
        logger.info("✅ Bot Started Successfully")

        @self.bot.on(events.NewMessage(pattern='/start'))
        async def start(event):
            user_id = event.sender_id
            await self.db.add_or_update_user(user_id, event.sender.first_name, event.sender.username)
            await event.respond("👋 سلام! به ربات حرفه‌ای خوش آمدید.\nبرای راهنما /help را بزنید.")

        @self.bot.on(events.NewMessage(pattern='/support'))
        async def support(event):
            await event.respond("📞 **پشتیبانی:**\n@ZeroReceiversup", link_preview=False)

        @self.bot.on(events.NewMessage(pattern='/admin'))
        async def admin_cmd(event):
            if event.sender_id == ADMIN_ID:
                await self.send_admin_panel(event)
            else:
                await event.respond("🚫")

        # --- Callback Handlers ---
        @self.bot.on(events.CallbackQuery(data=b'back_to_panel'))
        async def back(event):
            if event.sender_id == ADMIN_ID:
                await self.send_admin_panel(event)

        @self.bot.on(events.CallbackQuery(data=b'proxy_menu'))
        async def pm(event):
            if event.sender_id == ADMIN_ID:
                await self.proxy_menu_handler(event)

        @self.bot.on(events.CallbackQuery(data=b'add_single_proxy'))
        async def asp(event):
            if event.sender_id == ADMIN_ID:
                await self.add_single_proxy_flow(event)

        @self.bot.on(events.CallbackQuery(data=b'upload_proxy_file'))
        async def upf(event):
            if event.sender_id == ADMIN_ID:
                await self.upload_proxy_file_flow(event)
        
        @self.bot.on(events.CallbackQuery(data=b'clear_proxies'))
        async def cp(event):
            if event.sender_id == ADMIN_ID:
                await self.db.clear_proxies()
                await event.answer("پروکسی‌ها حذف شدند.", alert=True)
                await self.proxy_menu_handler(event)

        @self.bot.on(events.CallbackQuery(data=b'manage_requests'))
        async def mr(event):
            if event.sender_id == ADMIN_ID:
                await event.edit("📦 مدیریت درخواست‌ها:\nبرای افزودن درخواست جدید دستور /request را بزنید.", buttons=[Button.inline("🔙 بازگشت", b"back_to_panel")])

        @self.bot.on(events.CallbackQuery(data=b'stats_detail'))
        async def sd(event):
            if event.sender_id == ADMIN_ID:
                users = await self.db.get_total_users()
                await event.answer(f"کاربران: {users}", alert=True)

        # --- Command Handlers ---
        @self.bot.on(events.NewMessage(pattern='/request'))
        async def req(event):
            await self.request_numbers_admin(event)

        @self.bot.on(events.NewMessage(pattern='/register_number'))
        async def login(event):
            await self.login_user(event)
            
        @self.bot.on(events.NewMessage(pattern='/help'))
        async def help(event):
            await event.respond("/start\n/admin\n/register_number\n/profile\n/information\n/settle\n/countries")

        @self.bot.on(events.NewMessage(pattern='/countries'))
        async def list_req(event):
            await self.request_list(event)

        @self.bot.on(events.NewMessage(pattern='/settle'))
        async def settle(event):
            await self.settle_handler(event)

        @self.bot.on(events.NewMessage(pattern='/information'))
        async def info(event):
            await self.collect_user_information(event)

        @self.bot.on(events.NewMessage(pattern='/profile'))
        async def profile(event):
            await self.display_account_info(event)

        await self.bot.run_until_disconnected()

if __name__ == "__main__":
    bot = BotHandler()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bot.run())