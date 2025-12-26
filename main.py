import asyncio
import logging
import sqlite3
import random
from datetime import datetime
from typing import List, Dict, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "7939238322:AAEAN-l0srLH7YmNRCbWBDRWzwd-fwN025w"
CHANNEL_USERNAME = "@k1lossez"
GROUP_ID = -5197819981
ADMIN_IDS = [7546928092]
MAX_TEAMS = 16
TEAM_SIZE = 5

# ========== НАСТРОЙКА ЛОГГИРОВАНИЯ ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('tournament.db', check_same_thread=False, isolation_level=None)
cursor = conn.cursor()

# Удаляем старые таблицы и создаем новые с правильной структурой
cursor.execute('DROP TABLE IF EXISTS applications')
cursor.execute('''
CREATE TABLE applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    full_name TEXT,
    team_name TEXT,
    team_members TEXT,
    contact TEXT,
    status TEXT DEFAULT 'pending',
    tournament_group INTEGER DEFAULT NULL,
    tournament_position INTEGER DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

cursor.execute('DROP TABLE IF EXISTS tournament_settings')
cursor.execute('''
CREATE TABLE tournament_settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    max_teams INTEGER DEFAULT 16,
    team_size INTEGER DEFAULT 5,
    channel_username TEXT DEFAULT '@ваш_канал',
    tournament_started BOOLEAN DEFAULT 0,
    tournament_stage TEXT DEFAULT 'registration'
)
''')

cursor.execute('DROP TABLE IF EXISTS admins')
cursor.execute('''
CREATE TABLE admins (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Настройки по умолчанию
cursor.execute("INSERT OR IGNORE INTO tournament_settings (id, max_teams, team_size, channel_username) VALUES (1, ?, ?, ?)", 
               (MAX_TEAMS, TEAM_SIZE, CHANNEL_USERNAME))

# Добавляем администраторов по умолчанию
for admin_id in ADMIN_IDS:
    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (admin_id,))

conn.commit()

# ========== УТИЛИТЫ ==========
def get_settings():
    """Получение текущих настроек турнира"""
    return cursor.execute("SELECT max_teams, team_size, channel_username, tournament_started, tournament_stage FROM tournament_settings WHERE id=1").fetchone()

def get_stats():
    """Получение статистики заявок"""
    stats = cursor.execute('''
        SELECT status, COUNT(*) FROM applications 
        GROUP BY status
    ''').fetchall()
    return dict(stats)

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь администратором"""
    admin = cursor.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone()
    return admin is not None

def is_main_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь главным администратором"""
    return user_id == ADMIN_IDS[0]

async def check_subscription(user_id: int) -> bool:
    """Проверка подписки пользователя на канал"""
    try:
        settings = get_settings()
        channel_username = settings[2]
        
        if not channel_username or channel_username == '@ваш_канал':
            return True
        
        chat = await bot.get_chat(channel_username)
        member = await bot.get_chat_member(chat_id=chat.id, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        return True

def get_all_admins():
    """Получение списка всех администраторов"""
    return cursor.execute("SELECT user_id, username FROM admins ORDER BY added_at").fetchall()

def get_all_users():
    """Получение всех пользователей с заявками"""
    return cursor.execute("SELECT DISTINCT user_id FROM applications").fetchall()

def get_approved_teams():
    """Получение всех одобренных команд"""
    return cursor.execute(
        "SELECT id, team_name, full_name, contact, user_id FROM applications WHERE status='approved' ORDER BY id"
    ).fetchall()

def start_tournament():
    """Запуск турнира"""
    cursor.execute("UPDATE tournament_settings SET tournament_started=1, tournament_stage='group_stage' WHERE id=1")
    conn.commit()

def reset_tournament():
    """Сброс турнира"""
    cursor.execute("UPDATE applications SET tournament_group=NULL, tournament_position=NULL")
    cursor.execute("UPDATE tournament_settings SET tournament_started=0, tournament_stage='registration' WHERE id=1")
    conn.commit()

def create_tournament_bracket():
    """Создание турнирной сетки"""
    teams = get_approved_teams()
    
    if len(teams) < 2:
        return None
    
    # Рандомизируем порядок команд
    random.shuffle(teams)
    
    # Определяем количество групп (по 4 команды в группе)
    num_groups = (len(teams) + 3) // 4
    
    # Распределяем команды по группам
    groups = {}
    for i, team in enumerate(teams):
        group_num = (i % num_groups) + 1
        position = (i // num_groups) + 1
        
        cursor.execute(
            "UPDATE applications SET tournament_group=?, tournament_position=? WHERE id=?",
            (group_num, position, team[0])
        )
        
        if group_num not in groups:
            groups[group_num] = []
        groups[group_num].append(team)
    
    conn.commit()
    return groups

def get_tournament_bracket():
    """Получение турнирной сетки"""
    teams = cursor.execute(
        "SELECT tournament_group, tournament_position, team_name, full_name FROM applications WHERE status='approved' AND tournament_group IS NOT NULL ORDER BY tournament_group, tournament_position"
    ).fetchall()
    
    groups = {}
    for team in teams:
        group_num = team[0]
        if group_num not in groups:
            groups[group_num] = []
        groups[group_num].append(team)
    
    return groups

# ========== СОСТОЯНИЯ (FSM) ==========
class RegistrationStates(StatesGroup):
    waiting_full_name = State()
    waiting_team_name = State()
    waiting_team_members = State()
    waiting_contact = State()

class AdminStates(StatesGroup):
    waiting_max_teams = State()
    waiting_team_size = State()
    waiting_channel_username = State()
    waiting_admin_id = State()
    waiting_broadcast_message = State()
    waiting_broadcast_filter = State()

# ========== КОМАНДЫ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========
@dp.message(Command("start"))
async def start_command(message: types.Message):
    """Обработка команды /start"""
    user_id = message.from_user.id
    
    settings = get_settings()
    channel_username = settings[2]
    
    # Проверка подписки
    if channel_username and channel_username != '@ваш_канал':
        if not await check_subscription(user_id):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://t.me/{channel_username[1:]}")],
                [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription")]
            ])
            
            await message.answer(
                f"📢 Для регистрации на турнир необходимо подписаться на наш канал: {channel_username}\n\n"
                "После подписки нажмите кнопку 'Я подписался'.",
                reply_markup=keyboard
            )
            return
    
    # Пользователь подписан или проверка отключена
    stats = get_stats()
    approved = stats.get('approved', 0)
    settings = get_settings()
    
    # Проверяем, начался ли турнир
    if settings[3]:  # tournament_started
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📊 Статус заявки")],
                [KeyboardButton(text="🏆 Турнирная сетка")],
                [KeyboardButton(text="📋 Моя группа")]
            ],
            resize_keyboard=True
        )
        
        # Получаем информацию о группе пользователя
        user_team = cursor.execute(
            "SELECT tournament_group, tournament_position, team_name FROM applications WHERE user_id=? AND status='approved'",
            (user_id,)
        ).fetchone()
        
        if user_team:
            group_info = f"\n\nВаша команда '{user_team[2]}' находится в Группе {user_team[0]}, позиция {user_team[1]}"
        else:
            group_info = ""
        
        await message.answer(
            f"🏆 Турнир начался!\n\n"
            f"📊 Статистика:\n"
            f"• Зарегистрировано команд: {approved}/{settings[0]}\n"
            f"• Стадия турнира: {settings[4]}\n"
            f"{group_info}",
            reply_markup=keyboard
        )
    else:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📝 Подать заявку")],
                [KeyboardButton(text="📊 Статус заявки")]
            ],
            resize_keyboard=True
        )
        
        await message.answer(
            f"🏆 Добро пожаловать в регистрацию на турнир!\n\n"
            f"📊 Статистика:\n"
            f"• Зарегистрировано команд: {approved}/{settings[0]}\n"
            f"• Игроков в команде: {settings[1]}\n\n"
            f"Для подачи заявки нажмите кнопку ниже.",
            reply_markup=keyboard
        )

@dp.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery):
    """Обработка нажатия кнопки проверки подписки"""
    user_id = callback.from_user.id
    
    if await check_subscription(user_id):
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📝 Подать заявку")]],
            resize_keyboard=True
        )
        
        await callback.message.delete()
        await bot.send_message(
            chat_id=user_id,
            text="✅ Отлично! Теперь вы можете подать заявку.",
            reply_markup=keyboard
        )
    else:
        await callback.answer(
            "Вы ещё не подписались на канал!",
            show_alert=True
        )

@dp.message(F.text == "📝 Подать заявку")
async def start_registration(message: types.Message, state: FSMContext):
    """Начало процесса регистрации"""
    user_id = message.from_user.id
    settings = get_settings()
    
    # Проверяем, не начался ли уже турнир
    if settings[3]:  # tournament_started
        await message.answer("❌ Регистрация закрыта! Турнир уже начался.")
        return
    
    # Проверка подписки
    if settings[2] and settings[2] != '@ваш_канал':
        if not await check_subscription(user_id):
            await message.answer(f"❌ Сначала подпишитесь на канал: {settings[2]}")
            return
    
    # Проверка существующей заявки
    existing = cursor.execute(
        "SELECT status FROM applications WHERE user_id=?", 
        (user_id,)
    ).fetchone()
    
    if existing:
        status = existing[0]
        if status == 'pending':
            await message.answer("⏳ Ваша заявка уже на рассмотрении!")
            return
        elif status == 'approved':
            await message.answer("✅ Ваша заявка уже одобрена!")
            return
        elif status == 'rejected':
            cursor.execute("DELETE FROM applications WHERE user_id=?", (user_id,))
            conn.commit()
    
    # Проверка лимита команд
    stats = get_stats()
    approved = stats.get('approved', 0)
    
    if approved >= settings[0]:
        await message.answer("❌ Регистрация закрыта! Достигнут лимит команд.")
        return
    
    # Начинаем регистрацию
    await message.answer(
        "📋 Начнём регистрацию!\n\n"
        "Скажите как к вам обращаться?:",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(RegistrationStates.waiting_full_name)

@dp.message(RegistrationStates.waiting_full_name)
async def process_full_name(message: types.Message, state: FSMContext):
    """Обработка ФИО"""
    await state.update_data(full_name=message.text)
    await message.answer("🏷️ Введите название команды:")
    await state.set_state(RegistrationStates.waiting_team_name)

@dp.message(RegistrationStates.waiting_team_name)
async def process_team_name(message: types.Message, state: FSMContext):
    """Обработка названия команды"""
    await state.update_data(team_name=message.text)
    
    settings = get_settings()
    await message.answer(
        f"👥 Введите состав команды ({settings[1]} игроков):\n"
        f"Формат: ИГРОК 1, ИГРОК 2, ...\n"
        f"Пример: ИГРОК 1, ИГРОК 2, ИГРОК 3"
    )
    await state.set_state(RegistrationStates.waiting_team_members)

@dp.message(RegistrationStates.waiting_team_members)
async def process_team_members(message: types.Message, state: FSMContext):
    """Обработка состава команды"""
    settings = get_settings()
    required_size = settings[1]
    
    members = [m.strip() for m in message.text.split(',')]
    
    if len(members) != required_size:
        await message.answer(f"❌ Неверное количество игроков! Нужно {required_size} человек.")
        return
    
    await state.update_data(team_members=message.text)
    await message.answer("📞 Введите ваш контакт для связи (Telegram @ник или телефон):")
    await state.set_state(RegistrationStates.waiting_contact)

@dp.message(RegistrationStates.waiting_contact)
async def process_contact(message: types.Message, state: FSMContext):
    """Обработка контакта и завершение регистрации"""
    user_id = message.from_user.id
    user_data = await state.get_data()
    
    # Сохраняем заявку в БД
    cursor.execute(
        '''INSERT INTO applications 
        (user_id, username, full_name, team_name, team_members, contact) 
        VALUES (?, ?, ?, ?, ?, ?)''',
        (
            user_id,
            message.from_user.username,
            user_data['full_name'],
            user_data['team_name'],
            user_data['team_members'],
            message.text
        )
    )
    conn.commit()
    app_id = cursor.lastrowid
    
    # Отправляем заявку в группу для модерации
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{app_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{app_id}")
        ]
    ])
    
    try:
        await bot.send_message(
            chat_id=GROUP_ID,
            text=f"📨 НОВАЯ ЗАЯВКА #{app_id}\n\n"
                 f"👤 Игрок: {user_data['full_name']}\n"
                 f"📱 Контакт: {message.text}\n"
                 f"👤 Telegram: @{message.from_user.username or 'нет'}\n"
                 f"🏷️ Команда: {user_data['team_name']}\n"
                 f"👥 Состав:\n{user_data['team_members']}\n\n"
                 f"🆔 User ID: {user_id}",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка отправки в группу: {e}")
    
    # Сообщаем пользователю
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📊 Статус заявки")]],
        resize_keyboard=True
    )
    
    await message.answer(
        "✅ Заявка отправлена на модерацию!\n"
        "Ожидайте решения в течение 24 часов.",
        reply_markup=keyboard
    )
    
    await state.clear()

@dp.message(F.text == "📊 Статус заявки")
async def check_status(message: types.Message):
    """Проверка статуса заявки"""
    user_id = message.from_user.id
    
    app = cursor.execute(
        "SELECT status, team_name, tournament_group, tournament_position FROM applications WHERE user_id=?", 
        (user_id,)
    ).fetchone()
    
    if not app:
        await message.answer("❌ У вас нет активных заявок.")
        return
    
    status_text = {
        'pending': '⏳ На рассмотрении',
        'approved': '✅ Одобрена',
        'rejected': '❌ Отклонена'
    }
    
    response = f"📋 Ваша заявка:\nКоманда: {app[1]}\nСтатус: {status_text.get(app[0], app[0])}"
    
    if app[2] and app[3]:  # Если есть группа и позиция
        response += f"\n\n🏆 Турнирное положение:\nГруппа: {app[2]}\nПозиция в группе: {app[3]}"
    
    await message.answer(response)

@dp.message(F.text == "🏆 Турнирная сетка")
async def show_bracket(message: types.Message):
    """Показать турнирную сетку"""
    settings = get_settings()
    
    if not settings[3]:  # Если турнир не начат
        await message.answer("Турнир ещё не начался.")
        return
    
    groups = get_tournament_bracket()
    
    if not groups:
        await message.answer("Турнирная сетка ещё не сформирована.")
        return
    
    text = "🏆 ТУРНИРНАЯ СЕТКА 🏆\n\n"
    
    for group_num in sorted(groups.keys()):
        text += f"════════════════════\n"
        text += f"📊 ГРУППА {group_num}:\n"
        text += f"════════════════════\n"
        
        for team in groups[group_num]:
            text += f"{team[1]}. {team[2]} ({team[3]})\n"
        
        text += "\n"
    
    await message.answer(text)

@dp.message(F.text == "📋 Моя группа")
async def show_my_group(message: types.Message):
    """Показать группу пользователя"""
    user_id = message.from_user.id
    
    team_info = cursor.execute(
        "SELECT tournament_group, tournament_position, team_name FROM applications WHERE user_id=? AND status='approved'",
        (user_id,)
    ).fetchone()
    
    if not team_info or not team_info[0]:
        await message.answer("❌ Вы не участвуете в турнире или группа ещё не определена.")
        return
    
    group_num = team_info[0]
    
    # Получаем все команды в этой группе
    teams_in_group = cursor.execute(
        "SELECT tournament_position, team_name, full_name FROM applications WHERE tournament_group=? AND status='approved' ORDER BY tournament_position",
        (group_num,)
    ).fetchall()
    
    text = f"📋 ВАША ГРУППА {group_num}:\n\n"
    
    for pos, team_name, captain in teams_in_group:
        if pos == team_info[1]:
            text += f"👉 {pos}. {team_name} (ваша команда)\n"
        else:
            text += f"   {pos}. {team_name}\n"
    
    text += f"\nКапитан вашей команды: {team_info[2]}"
    
    await message.answer(text)

# ========== АДМИН ПАНЕЛЬ ==========
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    """Панель администратора"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой команде.")
        return
    
    settings = get_settings()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="⚙️ Настройки турнира", callback_data="admin_settings")],
        [InlineKeyboardButton(text="👨‍💼 Управление админами", callback_data="admin_manage")],
        [InlineKeyboardButton(text="📋 Все заявки", callback_data="admin_applications")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🏆 Управление турниром", callback_data="admin_tournament")]
    ])
    
    status = "✅ Запущен" if settings[3] else "⏳ Регистрация"
    
    await message.answer(
        f"👨‍💼 Панель администратора\n\n"
        f"Статус турнира: {status}\n"
        f"Стадия: {settings[4]}",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("admin_"))
async def admin_actions(callback: types.CallbackQuery):
    """Обработка действий администратора"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    action = callback.data
    
    if action == "admin_stats":
        stats = get_stats()
        settings = get_settings()
        
        total = sum(stats.values())
        approved = stats.get('approved', 0)
        
        text = "📊 Статистика заявок:\n\n"
        text += f"✅ Одобрено: {stats.get('approved', 0)}\n"
        text += f"⏳ На рассмотрении: {stats.get('pending', 0)}\n"
        text += f"❌ Отклонено: {stats.get('rejected', 0)}\n"
        text += f"📈 Всего заявок: {total}\n\n"
        text += f"⚙️ Настройки турнира:\n"
        text += f"• Лимит команд: {approved}/{settings[0]}\n"
        text += f"• Игроков в команде: {settings[1]}\n"
        text += f"• Канал: {settings[2] or 'Не настроен'}\n"
        text += f"• Статус турнира: {'✅ Запущен' if settings[3] else '⏳ Регистрация'}\n"
        text += f"• Стадия: {settings[4]}"
        
        await callback.message.edit_text(text)
        
    elif action == "admin_settings":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Лимит команд", callback_data="set_max_teams")],
            [InlineKeyboardButton(text="👥 Размер команды", callback_data="set_team_size")],
            [InlineKeyboardButton(text="📢 Канал для подписки", callback_data="set_channel")],
            [InlineKeyboardButton(text="◀️ Назад в админ-панель", callback_data="back_to_admin_main")]
        ])
        await callback.message.edit_text("⚙️ Настройки турнира:", reply_markup=keyboard)
        
    elif action == "admin_manage":
        admins = get_all_admins()
        
        text = "👨‍💼 Список администраторов:\n\n"
        for admin_id, username in admins:
            if admin_id == ADMIN_IDS[0]:
                text += f"👑 ID: {admin_id} | @{username or 'нет'} (Главный админ)\n"
            else:
                text += f"• ID: {admin_id} | @{username or 'нет'}\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить админа", callback_data="add_admin")],
        ])
        
        # Только главный админ может удалять админов
        if is_main_admin(callback.from_user.id) and len(admins) > 1:
            keyboard.inline_keyboard.append([InlineKeyboardButton(text="➖ Удалить админа", callback_data="remove_admin")])
        
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="◀️ Назад в админ-панель", callback_data="back_to_admin_main")])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    elif action == "admin_applications":
        apps = cursor.execute(
            "SELECT id, team_name, status, full_name, created_at FROM applications ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        
        if not apps:
            await callback.message.edit_text("📭 Заявок пока нет.")
            return
        
        text = "📋 Последние заявки:\n\n"
        for app_id, team_name, status, full_name, created_at in apps:
            status_icon = "✅" if status == 'approved' else "⏳" if status == 'pending' else "❌"
            date_str = created_at[:10] if created_at else ""
            text += f"{status_icon} #{app_id} | {team_name[:15]} | {full_name[:10]} | {date_str}\n"
        
        await callback.message.edit_text(text)
        
    elif action == "admin_broadcast":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Всем пользователям", callback_data="broadcast_all")],
            [InlineKeyboardButton(text="✅ Только одобренным", callback_data="broadcast_approved")],
            [InlineKeyboardButton(text="⏳ Только ожидающим", callback_data="broadcast_pending")],
            [InlineKeyboardButton(text="◀️ Назад в админ-панель", callback_data="back_to_admin_main")]
        ])
        await callback.message.edit_text("📢 Выберите кому отправить рассылку:", reply_markup=keyboard)
        
    elif action == "admin_tournament":
        settings = get_settings()
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        
        if not settings[3]:  # Если турнир не начат
            keyboard.inline_keyboard.append([InlineKeyboardButton(text="▶️ Начать турнир", callback_data="start_tournament")])
        else:
            keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔄 Обновить сетку", callback_data="update_bracket")])
            keyboard.inline_keyboard.append([InlineKeyboardButton(text="📊 Показать сетку", callback_data="show_bracket_admin")])
            keyboard.inline_keyboard.append([InlineKeyboardButton(text="🛑 Завершить турнир", callback_data="end_tournament")])
        
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="◀️ Назад в админ-панель", callback_data="back_to_admin_main")])
        
        status_text = "✅ Турнир запущен" if settings[3] else "⏳ Ожидание запуска"
        
        await callback.message.edit_text(
            f"🏆 Управление турниром\n\n"
            f"Статус: {status_text}\n"
            f"Стадия: {settings[4]}\n\n"
            f"Выберите действие:",
            reply_markup=keyboard
        )
        
    elif action == "back_to_admin_main":
        # Возврат в главное меню админ-панели
        await admin_panel(callback.message)

# ========== ОБРАБОТЧИКИ КНОПОК ТУРНИРА ==========
@dp.callback_query(F.data == "start_tournament")
async def start_tournament_handler(callback: types.CallbackQuery):
    """Запуск турнира"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    settings = get_settings()
    stats = get_stats()
    approved = stats.get('approved', 0)
    
    if approved < 2:
        await callback.answer("❌ Нужно минимум 2 команды для начала турнира!", show_alert=True)
        return
    
    # Создаем турнирную сетку
    groups = create_tournament_bracket()
    
    if not groups:
        await callback.answer("❌ Ошибка создания турнирной сетки!", show_alert=True)
        return
    
    # Запускаем турнир
    start_tournament()
    
    # Формируем сообщение с сеткой
    text = "🎉 ТУРНИР НАЧАЛСЯ! 🎉\n\n"
    text += "══════════════════════════\n"
    text += "🏆 ТУРНИРНАЯ СЕТКА\n"
    text += "══════════════════════════\n\n"
    
    for group_num in sorted(groups.keys()):
        text += f"📊 ГРУППА {group_num}:\n"
        text += "----------------\n"
        
        for i, team in enumerate(groups[group_num], 1):
            text += f"{i}. {team[1]}\n"
        
        text += "\n"
    
    text += "Удачи всем участникам! 🍀"
    
    # Отправляем в группу админов
    await bot.send_message(
        chat_id=GROUP_ID,
        text=text,
        parse_mode='HTML'
    )
    
    # Уведомляем всех участников
    users = cursor.execute("SELECT DISTINCT user_id FROM applications WHERE status='approved'").fetchall()
    
    for user_id in users:
        try:
            await bot.send_message(
                chat_id=user_id[0],
                text="🎉 Турнир начался! Проверьте турнирную сетку в боте командой /start"
            )
        except:
            pass
    
    await callback.message.edit_text(
        f"✅ Турнир успешно запущен!\n\n"
        f"Создано групп: {len(groups)}\n"
        f"Всего команд: {approved}\n\n"
        f"Сетка отправлена в группу админов."
    )

@dp.callback_query(F.data == "update_bracket")
async def update_bracket_handler(callback: types.CallbackQuery):
    """Обновление турнирной сетки"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    groups = create_tournament_bracket()
    
    if not groups:
        await callback.answer("❌ Ошибка создания турнирной сетки!", show_alert=True)
        return
    
    text = "🔄 ТУРНИРНАЯ СЕТКА ОБНОВЛЕНА\n\n"
    text += "══════════════════════════\n"
    text += "🏆 НОВАЯ ТУРНИРНАЯ СЕТКА\n"
    text += "══════════════════════════\n\n"
    
    for group_num in sorted(groups.keys()):
        text += f"📊 ГРУППА {group_num}:\n"
        text += "----------------\n"
        
        for i, team in enumerate(groups[group_num], 1):
            text += f"{i}. {team[1]}\n"
        
        text += "\n"
    
    # Отправляем в группу админов
    await bot.send_message(
        chat_id=GROUP_ID,
        text=text,
        parse_mode='HTML'
    )
    
    await callback.message.edit_text("✅ Турнирная сетка обновлена и отправлена в группу!")

@dp.callback_query(F.data == "show_bracket_admin")
async def show_bracket_admin(callback: types.CallbackQuery):
    """Показать турнирную сетку админам"""
    groups = get_tournament_bracket()
    
    if not groups:
        await callback.answer("❌ Турнирная сетка ещё не создана!", show_alert=True)
        return
    
    text = "🏆 ТУРНИРНАЯ СЕТКА 🏆\n\n"
    
    for group_num in sorted(groups.keys()):
        text += f"════════════════════\n"
        text += f"📊 ГРУППА {group_num}:\n"
        text += f"════════════════════\n"
        
        for team in groups[group_num]:
            text += f"{team[1]}. {team[2]} (капитан: {team[3]})\n"
        
        text += "\n"
    
    await callback.message.edit_text(text)

@dp.callback_query(F.data == "end_tournament")
async def end_tournament_handler(callback: types.CallbackQuery):
    """Завершение турнира"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Нет", callback_data="cancel_end"),
            InlineKeyboardButton(text="✅ Да, завершить", callback_data="confirm_end")
        ]
    ])
    
    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите завершить турнир?\n\n"
        "Это сбросит все турнирные данные (группы, позиции) "
        "и переведет турнир обратно в стадию регистрации.",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "confirm_end")
async def confirm_end_tournament(callback: types.CallbackQuery):
    """Подтверждение завершения турнира"""
    if not is_admin(callback.from_user.id):
        return
    
    reset_tournament()
    
    # Уведомляем в группу
    await bot.send_message(
        chat_id=GROUP_ID,
        text="🛑 ТУРНИР ЗАВЕРШЕН\n\n"
             "Все турнирные данные сброшены.\n"
             "Турнир переведен в стадию регистрации."
    )
    
    await callback.message.edit_text("✅ Турнир завершен! Все данные сброшены.")

@dp.callback_query(F.data == "cancel_end")
async def cancel_end_tournament(callback: types.CallbackQuery):
    """Отмена завершения турнира"""
    await callback.message.delete()
    await admin_panel(callback.message)

# ========== РАССЫЛКА СООБЩЕНИЙ ==========
@dp.callback_query(F.data.startswith("broadcast_"))
async def broadcast_select(callback: types.CallbackQuery, state: FSMContext):
    """Выбор типа рассылки"""
    if not is_admin(callback.from_user.id):
        return
    
    broadcast_type = callback.data.replace("broadcast_", "")
    
    await state.update_data(broadcast_type=broadcast_type)
    await callback.message.edit_text(
        "Введите сообщение для рассылки:\n\n"
        "Можно использовать HTML разметку:\n"
        "<b>жирный</b>\n"
        "<i>курсив</i>\n"
        "<u>подчеркнутый</u>\n"
        "<code>моноширинный</code>"
    )
    await state.set_state(AdminStates.waiting_broadcast_message)

@dp.message(AdminStates.waiting_broadcast_message)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    """Обработка сообщения для рассылки"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    data = await state.get_data()
    broadcast_type = data.get('broadcast_type', 'all')
    
    # Определяем кому отправлять
    if broadcast_type == 'approved':
        users = cursor.execute("SELECT DISTINCT user_id FROM applications WHERE status='approved'").fetchall()
    elif broadcast_type == 'pending':
        users = cursor.execute("SELECT DISTINCT user_id FROM applications WHERE status='pending'").fetchall()
    else:
        users = cursor.execute("SELECT DISTINCT user_id FROM applications").fetchall()
    
    users = [user[0] for user in users]
    
    if not users:
        await message.answer("❌ Нет пользователей для рассылки.")
        await state.clear()
        return
    
    await state.update_data(broadcast_message=message.text, broadcast_users=users)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_broadcast"),
            InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_broadcast")
        ]
    ])
    
    await message.answer(
        f"📢 Подтвердите рассылку:\n\n"
        f"Получателей: {len(users)}\n"
        f"Тип: {broadcast_type}\n\n"
        f"Сообщение:\n{message.text[:200]}...",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "confirm_broadcast")
async def confirm_broadcast(callback: types.CallbackQuery, state: FSMContext):
    """Подтверждение рассылки"""
    if not is_admin(callback.from_user.id):
        await state.clear()
        return
    
    data = await state.get_data()
    message_text = data.get('broadcast_message', '')
    users = data.get('broadcast_users', [])
    
    if not users or not message_text:
        await callback.answer("❌ Ошибка данных рассылки", show_alert=True)
        await state.clear()
        return
    
    # Отправляем сообщение
    success = 0
    failed = 0
    
    await callback.message.edit_text(f"📤 Отправка рассылки...\n0/{len(users)}")
    
    for i, user_id in enumerate(users):
        try:
            await bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode='HTML'
            )
            success += 1
        except Exception as e:
            failed += 1
            logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
        
        # Обновляем статус каждые 10 сообщений
        if i % 10 == 0:
            await callback.message.edit_text(
                f"📤 Отправка рассылки...\n{i+1}/{len(users)}\n"
                f"✅ Успешно: {success}\n"
                f"❌ Ошибок: {failed}"
            )
    
    await callback.message.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"📊 Статистика:\n"
        f"• Всего получателей: {len(users)}\n"
        f"• Успешно отправлено: {success}\n"
        f"• Ошибок: {failed}"
    )
    
    await state.clear()

@dp.callback_query(F.data == "cancel_broadcast")
async def cancel_broadcast(callback: types.CallbackQuery, state: FSMContext):
    """Отмена рассылки"""
    await callback.message.edit_text("❌ Рассылка отменена.")
    await state.clear()

# ========== НАСТРОЙКИ АДМИНА ==========
@dp.callback_query(F.data == "set_max_teams")
async def ask_max_teams(callback: types.CallbackQuery, state: FSMContext):
    """Запрос нового лимита команд"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.message.edit_text(
        "Введите новое максимальное количество команд (число):\n\n"
        "Пример: 16"
    )
    await state.set_state(AdminStates.waiting_max_teams)

@dp.message(AdminStates.waiting_max_teams)
async def set_max_teams_value(message: types.Message, state: FSMContext):
    """Установка нового лимита команд"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    try:
        max_teams = int(message.text)
        if max_teams < 2:
            await message.answer("❌ Минимум 2 команды для турнира!")
            return
        
        cursor.execute(
            "UPDATE tournament_settings SET max_teams=? WHERE id=1",
            (max_teams,)
        )
        conn.commit()
        
        await message.answer(f"✅ Лимит команд установлен: {max_teams}")
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Введите корректное число!")
        return

@dp.callback_query(F.data == "set_team_size")
async def ask_team_size(callback: types.CallbackQuery, state: FSMContext):
    """Запрос нового размера команды"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.message.edit_text(
        "Введите новое количество игроков в команде (число):\n\n"
        "Пример: 5"
    )
    await state.set_state(AdminStates.waiting_team_size)

@dp.message(AdminStates.waiting_team_size)
async def set_team_size_value(message: types.Message, state: FSMContext):
    """Установка нового размера команды"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    try:
        team_size = int(message.text)
        if team_size < 1:
            raise ValueError
        
        cursor.execute(
            "UPDATE tournament_settings SET team_size=? WHERE id=1",
            (team_size,)
        )
        conn.commit()
        
        await message.answer(f"✅ Размер команды установлен: {team_size}")
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Введите корректное положительное число!")
        return

@dp.callback_query(F.data == "set_channel")
async def ask_channel_username(callback: types.CallbackQuery, state: FSMContext):
    """Запрос юзернейма канала"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.message.edit_text(
        "Введите юзернейм канала для подписки:\n\n"
        "Пример: @my_channel\n"
        "Или отправьте '0' чтобы отключить проверку подписки"
    )
    await state.set_state(AdminStates.waiting_channel_username)

@dp.message(AdminStates.waiting_channel_username)
async def set_channel_username(message: types.Message, state: FSMContext):
    """Установка юзернейма канала"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    channel_username = message.text.strip()
    
    if channel_username == '0':
        channel_username = ''
        response = "✅ Проверка подписки отключена"
    elif not channel_username.startswith('@'):
        await message.answer("❌ Юзернейм должен начинаться с @")
        return
    else:
        response = f"✅ Канал установлен: {channel_username}"
    
    cursor.execute(
        "UPDATE tournament_settings SET channel_username=? WHERE id=1",
        (channel_username,)
    )
    conn.commit()
    
    await message.answer(response)
    await state.clear()

# ========== УПРАВЛЕНИЕ АДМИНАМИ ==========
@dp.callback_query(F.data == "add_admin")
async def ask_admin_id(callback: types.CallbackQuery, state: FSMContext):
    """Запрос ID нового администратора"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.message.edit_text(
        "Введите ID пользователя, которого хотите сделать администратором:\n\n"
        "ID можно узнать у @getmyid_bot"
    )
    await state.set_state(AdminStates.waiting_admin_id)

@dp.message(AdminStates.waiting_admin_id)
async def add_admin_id(message: types.Message, state: FSMContext):
    """Добавление нового администратора"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    try:
        new_admin_id = int(message.text)
        
        # Проверяем, не является ли уже админом
        if is_admin(new_admin_id):
            await message.answer("❌ Этот пользователь уже администратор!")
            return
        
        # Добавляем админа
        cursor.execute(
            "INSERT OR REPLACE INTO admins (user_id, username) VALUES (?, ?)",
            (new_admin_id, message.from_user.username)
        )
        conn.commit()
        
        await message.answer(f"✅ Пользователь {new_admin_id} добавлен в администраторы")
        
        # Уведомляем нового админа
        try:
            await bot.send_message(
                chat_id=new_admin_id,
                text="🎉 Вас добавили в администраторы бота!\n\n"
                     "Используйте команду /admin для доступа к панели управления."
            )
        except:
            pass
        
    except ValueError:
        await message.answer("❌ Введите корректный ID (число)!")
        return
    
    await state.clear()

@dp.callback_query(F.data == "remove_admin")
async def ask_remove_admin(callback: types.CallbackQuery):
    """Запрос на удаление администратора"""
    if not is_main_admin(callback.from_user.id):
        await callback.answer("❌ Только главный админ может удалять админов!", show_alert=True)
        return
    
    admins = get_all_admins()
    
    if len(admins) <= 1:
        await callback.answer("❌ Нельзя удалить последнего администратора!", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for admin_id, username in admins:
        if admin_id != callback.from_user.id:  # Нельзя удалить себя
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"ID: {admin_id} | @{username or 'нет'}",
                    callback_data=f"remove_admin_{admin_id}"
                )
            ])
    
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="◀️ Назад в админ-панель", callback_data="back_to_admin_main")])
    
    await callback.message.edit_text(
        "Выберите администратора для удаления:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("remove_admin_"))
async def remove_admin(callback: types.CallbackQuery):
    """Удаление администратора"""
    if not is_main_admin(callback.from_user.id):
        return
    
    admin_id = int(callback.data.split("_")[2])
    
    # Нельзя удалить главного админа
    if admin_id == ADMIN_IDS[0]:
        await callback.answer("❌ Нельзя удалить главного администратора!", show_alert=True)
        return
    
    cursor.execute("DELETE FROM admins WHERE user_id=?", (admin_id,))
    conn.commit()
    
    await callback.message.edit_text(f"✅ Администратор {admin_id} удалён")
    await callback.answer()

# ========== МОДЕРАЦИЯ ЗАЯВОК В ГРУППЕ ==========
@dp.callback_query(F.data.startswith(("approve_", "reject_")))
async def moderate_application(callback: types.CallbackQuery):
    """Одобрение или отклонение заявки"""
    # Проверяем, что это сообщение из группы модерации
    if callback.message.chat.id != GROUP_ID:
        return
    
    # Разбираем callback data
    action, app_id = callback.data.split('_')
    app_id = int(app_id)
    
    # Получаем заявку
    app = cursor.execute(
        "SELECT user_id, team_name, status, full_name, contact FROM applications WHERE id=?", 
        (app_id,)
    ).fetchone()
    
    if not app:
        await callback.answer("Заявка не найдена!", show_alert=True)
        return
    
    if app[2] != 'pending':
        await callback.answer("Заявка уже обработана!", show_alert=True)
        return
    
    settings = get_settings()
    
    if action == 'approve':
        # Проверяем лимит команд
        stats = get_stats()
        approved = stats.get('approved', 0)
        
        if approved >= settings[0]:
            await callback.answer("❌ Достигнут лимит команд!", show_alert=True)
            return
        
        # Одобряем заявку
        cursor.execute(
            "UPDATE applications SET status='approved' WHERE id=?", 
            (app_id,)
        )
        conn.commit()
        
        # Проверяем, достигнут ли лимит команд для автоматического старта турнира
        stats = get_stats()
        approved = stats.get('approved', 0)
        
        if approved >= settings[0] and not settings[3]:
            # Автоматически начинаем турнир при заполнении лимита
            groups = create_tournament_bracket()
            start_tournament()
            
            # Отправляем уведомление в группу
            await bot.send_message(
                chat_id=GROUP_ID,
                text=f"🎉 ЛИМИТ КОМАНД ДОСТИГНУТ!\n\n"
                     f"✅ Одобрено команд: {approved}/{settings[0]}\n"
                     f"🏆 Турнир автоматически запущен!\n\n"
                     f"Турнирная сетка сформирована."
            )
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                chat_id=app[0],
                text=f"🎉 Поздравляем! Ваша заявка одобрена!\n\n"
                     f"🏷️ Команда: {app[1]}\n"
                     f"👤 Капитан: {app[3]}\n\n"
                     f"Ожидайте дальнейших инструкций."
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя: {e}")
        
        # Обновляем сообщение в группе
        await callback.message.edit_text(
            f"✅ ЗАЯВКА ОДОБРЕНА #{app_id}\n\n"
            f"👤 Игрок: {app[3]}\n"
            f"📱 Контакт: {app[4]}\n"
            f"🏷️ Команда: {app[1]}\n\n"
            f"Статус: ✅ Одобрено\n"
            f"Всего одобрено: {stats.get('approved', 0) + 1}/{settings[0]}"
        )
        
        await callback.answer("✅ Заявка одобрена!")
        
    else:  # reject
        # Отклоняем заявку
        cursor.execute(
            "UPDATE applications SET status='rejected' WHERE id=?", 
            (app_id,)
        )
        conn.commit()
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                chat_id=app[0],
                text=f"❌ К сожалению, ваша заявка отклонена.\n\n"
                     f"🏷️ Команда: {app[1]}\n\n"
                     f"Вы можете подать новую заявку."
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя: {e}")
        
        # Обновляем сообщение в группе
        await callback.message.edit_text(
            f"❌ ЗАЯВКА ОТКЛОНЕНА #{app_id}\n\n"
            f"👤 Игрок: {app[3]}\n"
            f"🏷️ Команда: {app[1]}\n\n"
            f"Статус: ❌ Отклонено"
        )
        
        await callback.answer("❌ Заявка отклонена!")

# ========== ЗАПУСК БОТА ==========
async def main():
    """Главная функция запуска бота"""
    print("=" * 50)
    print("🤖 БОТ ДЛЯ РЕГИСТРАЦИИ НА ТУРНИР")
    print("=" * 50)
    
    # Проверка настроек
    settings = get_settings()
    
    print(f"Токен: {'✅ Установлен' if BOT_TOKEN else '❌ НЕ УСТАНОВЛЕН'}")
    print(f"Главный админ: {ADMIN_IDS[0]}")
    print(f"Канал для подписки: {settings[2] or 'Не настроен'}")
    print(f"Группа для модерации: {GROUP_ID}")
    print(f"Лимит команд: {settings[0]}")
    print(f"Размер команды: {settings[1]}")
    print(f"Статус турнира: {'✅ Запущен' if settings[3] else '⏳ Регистрация'}")
    print(f"Стадия турнира: {settings[4]}")
    print("=" * 50)
    print("Бот запущен...")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
