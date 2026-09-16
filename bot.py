import asyncio
import json
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, WebAppInfo
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# ============ WEB-ИНТЕРФЕЙС (FLASK) ============
try:
    from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for

    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("⚠️ Flask не установлен. Веб-интерфейс будет отключён.")
    print("   Установите: pip install flask")

# ============ КОНФИГУРАЦИЯ ============

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN and os.path.exists("token.txt"):
    BOT_TOKEN = Path("token.txt").read_text(encoding="utf-8").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Добавьте его в Environment на Render.")

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "7114829971").replace(" ", "").split(",") if x]
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()

# ============ НАСТРОЙКИ ============

SELLER_INFO = {
    'telegram': '@ckot_23',
    'telegram_link': 'https://t.me/ckot_23',
    'channel': 'ArtMoto23',
    'channel_link': 'https://t.me/ArtMoto23',
    'phone': '',
    'email': 'andreydragon22813@gmail.com',
    'work_hours': 'Заказы принимаются через Telegram'
}

# ============ ПУТИ И ПАПКИ ============

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
ORDERS_FILE = str(DATA_DIR / 'orders.json')
COMPLETED_ORDERS_FILE = str(DATA_DIR / 'completed_orders.json')
DESIGNS_FILE = str(DATA_DIR / 'designs_examples.json')
CART_FILE = str(DATA_DIR / 'cart.json')
DESIGNS_FOLDER = str(DATA_DIR / 'designs')
EXAMPLES_FOLDER = str(DATA_DIR / 'designs_examples')

for folder in [DESIGNS_FOLDER, EXAMPLES_FOLDER]:
    os.makedirs(folder, exist_ok=True)

STICKER_SIZES = {
    'small': {'name': 'Маленькая (5x5 см)', 'price': 300},
    'medium': {'name': 'Средняя (10x10 см)', 'price': 500},
    'large': {'name': 'Большая (15x15 см)', 'price': 1125},
    'xl': {'name': 'Огромная (20x20 см)', 'price': 2000}
}

FILMS = {
    'matte': ('Матовая', 5.0), 'gloss': ('Глянцевая', 5.5),
    'transparent': ('Прозрачная', 6.0), 'metallic': ('Металлик', 7.0),
    'reflective': ('Светоотражающая', 8.0),
}
DESIGNS = {
    'own': ('Свой файл', 1.0, 0), 'text': ('Надпись', 1.1, 0),
    'logo': ('Макет под ключ', 1.25, 1500), 'catalog': ('Из каталога', 1.0, 0),
}
COLORS = {
    'black':'Чёрный','white':'Белый','red':'Красный','blue':'Синий','green':'Зелёный',
    'yellow':'Жёлтый','orange':'Оранжевый','silver':'Серебро','gold':'Золото',
    'fullcolor':'Полноцветная печать',
}
COLOR_MULT = {'fullcolor':1.3, 'gold':1.15, 'silver':1.15}
TIERS = ((100,.60),(50,.68),(25,.75),(10,.82),(5,.90),(1,1.0))
MIN_ORDER_PRICE = 300

def round_five(value: float) -> int:
    return int(round(value / 5.0) * 5)

def calculate_webapp(data: Dict[str, Any]) -> Dict[str, Any]:
    film=str(data.get('film','')); design=str(data.get('design','')); color=str(data.get('color',''))
    if film not in FILMS or design not in DESIGNS or color not in COLORS:
        raise ValueError('Некорректный материал, дизайн или цвет')
    w=float(data.get('w',0)); h=float(data.get('h',0)); qty=int(data.get('qty',0))
    if not (1 <= w <= 500 and 1 <= h <= 500): raise ValueError('Размер должен быть от 1 до 500 см')
    if not (1 <= qty <= 10000): raise ValueError('Тираж должен быть от 1 до 10000')
    _, rate=FILMS[film]; _, mult, setup=DESIGNS[design]
    unit=round_five(w*h*rate*mult*COLOR_MULT.get(color,1.0))
    discount=next(multiplier for threshold,multiplier in TIERS if qty >= threshold)
    total=max(MIN_ORDER_PRICE, round_five(unit*qty*discount)+setup)
    return {'film':film,'design':design,'color':color,'w':w,'h':h,'qty':qty,'unit':unit,'setup':setup,'total':total,'discount':round((1-discount)*100)}

STATUS_EMOJI = {
    'новый': '🆕', 'в обработке': '🔄', 'готов к отправке': '📦',
    'отправлен': '🚚', 'доставлен': '✅', 'выполнен': '🎉'
}


# ============ FSM СОСТОЯНИЯ ============

class OrderStates(StatesGroup):
    size = State()
    quantity = State()
    comment = State()
    design = State()


class AdminStates(StatesGroup):
    example_name = State()
    example_description = State()
    example_file = State()


# ============ РАБОТА С ДАННЫМИ ============

def load_json(file: str, default: list = None) -> list:
    if default is None:
        default = []
    if not os.path.exists(file):
        return default
    with open(file, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(file: str, data: list) -> None:
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_orders() -> list:
    return load_json(ORDERS_FILE)


def save_orders(orders: list) -> None:
    save_json(ORDERS_FILE, orders)


def load_completed() -> list:
    return load_json(COMPLETED_ORDERS_FILE)


def save_completed(orders: list) -> None:
    save_json(COMPLETED_ORDERS_FILE, orders)


def load_examples() -> list:
    return load_json(DESIGNS_FILE)


def save_examples(examples: list) -> None:
    save_json(DESIGNS_FILE, examples)


def load_cart() -> list:
    return load_json(CART_FILE)


def save_cart(cart: list) -> None:
    save_json(CART_FILE, cart)


# ============ КОРЗИНА ============

def get_cart(user_id: int) -> List[Dict]:
    carts = load_cart()
    for cart in carts:
        if cart['user_id'] == user_id:
            return cart['items']
    return []


def add_to_cart(user_id: int, size: str, quantity: int, design_file: str = None, example: str = None) -> None:
    carts = load_cart()

    user_cart = None
    for cart in carts:
        if cart['user_id'] == user_id:
            user_cart = cart
            break

    if not user_cart:
        user_cart = {'user_id': user_id, 'items': []}
        carts.append(user_cart)

    for item in user_cart['items']:
        if item['size'] == size:
            item['quantity'] += quantity
            save_cart(carts)
            return

    item = {
        'size': size,
        'size_name': STICKER_SIZES[size]['name'],
        'price': STICKER_SIZES[size]['price'],
        'quantity': quantity,
        'design_file': design_file or '',
        'example': example or '',
        'added_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    user_cart['items'].append(item)
    save_cart(carts)


def remove_from_cart(user_id: int, index: int) -> bool:
    carts = load_cart()
    for cart in carts:
        if cart['user_id'] == user_id:
            if 0 <= index < len(cart['items']):
                del cart['items'][index]
                save_cart(carts)
                return True
    return False


def clear_cart(user_id: int) -> None:
    carts = load_cart()
    for cart in carts:
        if cart['user_id'] == user_id:
            cart['items'] = []
            save_cart(carts)
            return


def get_cart_total(user_id: int) -> int:
    items = get_cart(user_id)
    return sum(item['price'] * item['quantity'] for item in items)


# ============ ИСТОРИЯ ЗАКАЗОВ ============

def get_user_orders(user_id: int, status_filter: str = None) -> List[Dict]:
    all_orders = load_orders() + load_completed()
    user_orders = [o for o in all_orders if o['user_id'] == user_id]

    if status_filter:
        user_orders = [o for o in user_orders if o['status'] == status_filter]

    return sorted(user_orders, key=lambda x: x['order_id'], reverse=True)


def get_order_by_id(order_id: int) -> Optional[Dict]:
    for order in load_orders():
        if order['order_id'] == order_id:
            return order
    return None


def complete_order(order_id: int) -> tuple[bool, Optional[Dict]]:
    orders = load_orders()
    for i, order in enumerate(orders):
        if order['order_id'] == order_id:
            order['completed_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            order['status'] = 'выполнен'
            completed = load_completed()
            completed.append(order)
            save_completed(completed)
            del orders[i]
            save_orders(orders)
            return True, order
    return False, None


def save_new_order(user_id: int, username: str, data: Dict) -> Dict:
    orders = load_orders()
    order = {
        'order_id': len(orders) + 1 if orders else 1,
        'user_id': user_id,
        'username': username,
        'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'size': data['size'],
        'size_name': STICKER_SIZES[data['size']]['name'],
        'price': STICKER_SIZES[data['size']]['price'],
        'quantity': data['quantity'],
        'total_price': STICKER_SIZES[data['size']]['price'] * data['quantity'],
        'comment': data.get('comment', ''),
        'design_file': data.get('design_file', ''),
        'selected_example': data.get('selected_example', ''),
        'status': 'новый'
    }
    orders.append(order)
    save_orders(orders)
    return order


# ============ КЛАВИАТУРЫ ============

def main_kb(user_id: int = None) -> ReplyKeyboardMarkup:
    buttons = []
    if WEBAPP_URL.startswith('https://'):
        buttons.append([KeyboardButton(text='🎨 Открыть конструктор', web_app=WebAppInfo(url=WEBAPP_URL))])
    else:
        buttons.append([KeyboardButton(text='🛍️ Сделать заказ')])
    buttons += [
        [KeyboardButton(text='🛒 Корзина')],
        [KeyboardButton(text='🖼 Примеры дизайнов')],
        [KeyboardButton(text='📋 Мои заказы')],
        [KeyboardButton(text='📞 Контакты')]
    ]
    if user_id and user_id in ADMIN_IDS:
        buttons.append([KeyboardButton(text='👨‍💼 Админ панель')])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def inline_kb(buttons: list, row_width: int = 1) -> InlineKeyboardMarkup:
    """Универсальная inline-клавиатура (только callback)"""
    keyboard = []
    for row in buttons:
        keyboard.append([InlineKeyboardButton(text=row[0], callback_data=row[1])])
    return InlineKeyboardMarkup(inline_keyboard=keyboard, row_width=row_width)


def contacts_kb() -> InlineKeyboardMarkup:
    """Клавиатура контактов с URL-ссылками"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='💬 Написать', url=SELLER_INFO['telegram_link']),
            InlineKeyboardButton(text='📢 Подписаться', url=SELLER_INFO['channel_link'])
        ]
    ])
    return keyboard


def size_kb() -> InlineKeyboardMarkup:
    buttons = []
    for key, data in STICKER_SIZES.items():
        buttons.append([f"{data['name']} - {data['price']} ₽", f"size_{key}"])
    buttons.extend([
        ['🖼 Посмотреть примеры', 'show_examples'],
        ['🎨 Загрузить дизайн', 'upload_design'],
        ['💬 Добавить комментарий', 'add_comment'],
        ['🔙 Назад', 'back_to_menu']
    ])
    return inline_kb(buttons, 2)


def cart_kb(user_id: int) -> InlineKeyboardMarkup:
    items = get_cart(user_id)
    buttons = []

    for i, item in enumerate(items):
        buttons.append([
            f"❌ {item['size_name']} x{item['quantity']} - {item['price'] * item['quantity']} ₽",
            f"remove_cart_{i}"
        ])

    if items:
        total = get_cart_total(user_id)
        buttons.append([f"💳 Оформить заказ ({total} ₽)", 'checkout_cart'])
        buttons.append(['🗑 Очистить корзину', 'clear_cart'])

    buttons.append(['🔙 Назад', 'back_to_menu'])
    return inline_kb(buttons)


def admin_kb() -> InlineKeyboardMarkup:
    buttons = [
        ['🆕 Новые заказы', 'admin_new'],
        ['📋 Все заказы', 'admin_all'],
        ['📦 Выполненные', 'admin_completed'],
        ['📊 Статистика', 'admin_stats'],
        ['📋 Команды', 'admin_list'],
        ['🌐 Веб-панель', 'admin_web']
    ]
    return inline_kb(buttons, 2)


# ============ БОТ ============

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

def format_order(order: Dict, completed: bool = False) -> str:
    emoji = STATUS_EMOJI.get(order['status'], '❓')
    text = (
        f"{emoji} Заказ #{order['order_id']}\n"
        f"  👤 @{order['username']}\n"
        f"  📏 {order['size_name']}\n"
        f"  🔢 {order['quantity']} шт.\n"
        f"  💰 {order['total_price']} ₽\n"
        f"  📅 {order['date']}\n"
    )
    if completed:
        text += f"  ✅ Выполнен: {order.get('completed_date', 'Н/Д')}\n"
    else:
        text += f"  📁 Дизайн: {'✅' if order.get('design_file') else '❌'}\n"
        text += f"  📝 Коммент: {order.get('comment', 'нет')[:30]}\n"
    return text + '─' * 20 + '\n'


async def notify_admins(order: Dict) -> None:
    for admin_id in ADMIN_IDS:
        try:
            text = (
                f"🔔 НОВЫЙ ЗАКАЗ!\n\n"
                f"Заказ #{order['order_id']}\n"
                f"Пользователь: @{order['username']}\n"
                f"Размер: {order['size_name']}\n"
                f"Количество: {order['quantity']} шт.\n"
                f"Сумма: {order['total_price']} ₽\n"
                f"Дата: {order['date']}\n"
                f"Дизайн: {'загружен' if order.get('design_file') else 'не загружен'}\n"
                f"Комментарий: {order.get('comment', 'нет')}"
            )
            await bot.send_message(admin_id, text)

            if order.get('design_file') and os.path.exists(order['design_file']):
                await bot.send_photo(admin_id, FSInputFile(order['design_file']),
                                     caption=f"Дизайн для заказа #{order['order_id']}")
        except Exception as e:
            print(f"Ошибка уведомления админа {admin_id}: {e}")


async def show_order_summary(message: Message, state: FSMContext) -> None:
    data = await state.get_data()

    if 'size' not in data or 'quantity' not in data:
        await message.answer("⚠️ Заказ не найден. Начните заново.")
        return

    size = data['size']
    quantity = data['quantity']
    total = STICKER_SIZES[size]['price'] * quantity

    text = (
        f"📋 Ваш заказ:\n\n"
        f"📏 Размер: {STICKER_SIZES[size]['name']}\n"
        f"🔢 Количество: {quantity} шт.\n"
        f"💳 Итог: {total} ₽\n"
    )
    text += f"\n🎨 Дизайн: {'✅ загружен' if data.get('design_file') else '⚠️ не загружен'}"
    text += f"\n📝 Комментарий: {data.get('comment', 'нет')}"
    text += f"\n🖼 Пример: {data.get('selected_example', 'нет')}\n\n"
    text += "Проверьте данные и подтвердите заказ:"

    await message.answer(
        text,
        reply_markup=inline_kb([
            ['✅ Подтвердить заказ', 'confirm_order'],
            ['🔄 Изменить', 'change_order'],
            ['🎨 Загрузить дизайн', 'upload_design'],
            ['🖼 Выбрать из примеров', 'show_examples'],
            ['💬 Добавить комментарий', 'add_comment'],
            ['🔢 Изменить количество', 'change_quantity']
        ], 2)
    )


# ============ КОМАНДЫ ============

@dp.message(Command('start'))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"Я бот для заказа наклеек с индивидуальным дизайном.\n\n"
        f"Используйте кнопки ниже:",
        reply_markup=main_kb(message.from_user.id)
    )


@dp.message(Command('list', 'help'))
async def cmd_list(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав!")
        return

    await message.answer(
        "📋 <b>АДМИН-КОМАНДЫ</b>\n\n"
        "/orders - Все заказы\n"
        "/order [ID] - Просмотр заказа\n"
        "/status [ID] [статус] - Изменить статус\n"
        "/complete [ID] - Подтвердить выполнение\n"
        "/delete [ID] - Удалить заказ\n"
        "/stats - Статистика\n"
        "/add_example - Добавить пример\n"
        "/delete_example - Удалить пример\n"
        "/examples - Просмотр примеров",
        reply_markup=admin_kb()
    )


@dp.message(Command('contacts'))
async def cmd_contacts(message: Message):
    await message.answer(
        f"📞 КОНТАКТЫ\n\n"
        f"💬 Telegram: {SELLER_INFO['telegram']}\n"
        f"📢 Канал: {SELLER_INFO['channel']}\n"
        f"📧 Email: {SELLER_INFO['email']}\n"
        f"🕐 Часы работы:\n{SELLER_INFO['work_hours']}",
        reply_markup=contacts_kb(),
        disable_web_page_preview=True
    )


# ============ АДМИН-КОМАНДЫ ============

@dp.message(Command('orders'))
async def cmd_orders(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ У вас нет прав!")

    orders = load_orders()
    await message.answer(
        f"👨‍💼 ПАНЕЛЬ АДМИНИСТРАТОРА\n\n"
        f"Активных заказов: {len(orders)}\n\n"
        f"Выберите действие:",
        reply_markup=admin_kb()
    )


@dp.message(Command('order'))
async def cmd_order(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ У вас нет прав!")

    try:
        _, order_id = message.text.split()
        order = get_order_by_id(int(order_id))
        if not order:
            return await message.answer(f"❌ Заказ #{order_id} не найден.")

        text = f"📋 ИНФОРМАЦИЯ О ЗАКАЗЕ #{order['order_id']}\n\n"
        text += f"Статус: {STATUS_EMOJI.get(order['status'], '❓')} {order['status']}\n"
        text += f"Пользователь: @{order['username']}\n"
        text += f"Размер: {order['size_name']}\n"
        text += f"Количество: {order['quantity']} шт.\n"
        text += f"Итого: {order['total_price']} ₽\n"
        text += f"Дата: {order['date']}\n"
        text += f"Дизайн: {'✅ Загружен' if order.get('design_file') else '❌ Не загружен'}\n"
        text += f"Комментарий: {order.get('comment', 'Нет')}"

        await message.answer(text)

        if order.get('design_file') and os.path.exists(order['design_file']):
            await message.answer_photo(FSInputFile(order['design_file']),
                                       caption=f"🎨 Дизайн для заказа #{order['order_id']}")
    except:
        await message.answer("❌ Используйте: /order [номер]")


@dp.message(Command('status'))
async def cmd_status(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ У вас нет прав!")

    try:
        _, order_id, status = message.text.split()
        if status not in ['новый', 'в обработке', 'готов к отправке', 'отправлен', 'доставлен']:
            return await message.answer("❌ Неверный статус")

        orders = load_orders()
        for order in orders:
            if order['order_id'] == int(order_id):
                order['status'] = status
                save_orders(orders)
                return await message.answer(f"✅ Статус заказа #{order_id} изменен на '{status}'")
        await message.answer(f"❌ Заказ #{order_id} не найден.")
    except:
        await message.answer("❌ Используйте: /status [ID] [статус]")


@dp.message(Command('complete'))
async def cmd_complete(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ У вас нет прав!")

    try:
        _, order_id = message.text.split()
        success, order = complete_order(int(order_id))
        if success:
            await message.answer(f"✅ Заказ #{order_id} выполнен!")
            await bot.send_message(order['user_id'],
                                   f"🎉 Ваш заказ #{order_id} выполнен! Спасибо! ❤️")
        else:
            await message.answer(f"❌ Заказ #{order_id} не найден.")
    except:
        await message.answer("❌ Используйте: /complete [ID]")


@dp.message(Command('delete'))
async def cmd_delete(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ У вас нет прав!")

    try:
        _, order_id = message.text.split()
        completed = load_completed()
        for i, order in enumerate(completed):
            if order['order_id'] == int(order_id):
                if order.get('design_file') and os.path.exists(order['design_file']):
                    os.remove(order['design_file'])
                del completed[i]
                save_completed(completed)
                return await message.answer(f"✅ Заказ #{order_id} удален!")
        await message.answer(f"❌ Заказ #{order_id} не найден в архиве.")
    except:
        await message.answer("❌ Используйте: /delete [ID]")


@dp.message(Command('stats'))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ У вас нет прав!")

    orders, completed = load_orders(), load_completed()
    total_orders, total_revenue = len(orders), sum(o['total_price'] for o in orders)
    total_completed, completed_revenue = len(completed), sum(o['total_price'] for o in completed)

    text = f"📊 СТАТИСТИКА\n\n"
    text += f"📦 Активных: {total_orders} (💰 {total_revenue} ₽)\n"
    text += f"📦 Выполнено: {total_completed} (💰 {completed_revenue} ₽)\n"
    text += f"📈 Всего: {total_orders + total_completed}\n"
    text += f"💰 Общая выручка: {total_revenue + completed_revenue} ₽\n\n"
    text += "📊 ПО СТАТУСАМ:\n"

    status_counts = {}
    for order in orders:
        status_counts[order['status']] = status_counts.get(order['status'], 0) + 1
    for status, count in status_counts.items():
        text += f"  {STATUS_EMOJI.get(status, '❓')} {status}: {count} шт.\n"

    await message.answer(text)


# ============ УПРАВЛЕНИЕ ПРИМЕРАМИ ============

@dp.message(Command('add_example'))
async def add_example_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ У вас нет прав!")
    await state.set_state(AdminStates.example_name)
    await message.answer("🖼 Введите название для примера дизайна:")


@dp.message(AdminStates.example_name)
async def add_example_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminStates.example_description)
    await message.answer("✏️ Введите описание:")


@dp.message(AdminStates.example_description)
async def add_example_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(AdminStates.example_file)
    await message.answer("📸 Отправьте изображение для примера:")


@dp.message(AdminStates.example_file, F.photo)
async def add_example_file(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        file = await bot.get_file(message.photo[-1].file_id)
        file_data = await bot.download_file(file.file_path)

        filename = f"{EXAMPLES_FOLDER}/example_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        with open(filename, 'wb') as f:
            f.write(file_data.getvalue())

        examples = load_examples()
        examples.append({
            'id': max([ex['id'] for ex in examples], default=0) + 1,
            'name': data['name'],
            'description': data['description'],
            'file_path': filename,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        save_examples(examples)
        await state.clear()
        await message.answer(f"✅ Пример добавлен: {data['name']}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command('delete_example'))
async def delete_example_start(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ У вас нет прав!")

    examples = load_examples()
    if not examples:
        return await message.answer("📭 Нет примеров для удаления.")

    buttons = [[f"🗑 {ex['name']}", f"del_ex_{ex['id']}"] for ex in examples]
    buttons.append(['🔙 Отмена', 'cancel_del'])
    await message.answer("🗑 Выберите пример для удаления:", reply_markup=inline_kb(buttons))


@dp.message(Command('examples'))
async def cmd_examples(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ У вас нет прав!")

    examples = load_examples()
    if not examples:
        return await message.answer("📭 Примеры не добавлены.")

    text = "🖼 СПИСОК ПРИМЕРОВ:\n\n"
    for ex in examples:
        text += f"ID: {ex['id']} | {ex['name']}\n{ex['description']}\n{'─' * 20}\n"
    await message.answer(text)


# ============ КНОПКИ ГЛАВНОГО МЕНЮ ============

@dp.message(F.text == '📞 Контакты')
async def btn_contacts(message: Message):
    await cmd_contacts(message)


@dp.message(F.text == '👨‍💼 Админ панель')
async def btn_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ У вас нет прав!")
    await cmd_orders(message)


@dp.message(F.text == '📋 Мои заказы')
async def btn_my_orders(message: Message):
    """Показывает все заказы пользователя"""
    user_id = message.from_user.id

    all_orders = load_orders() + load_completed()
    user_orders = [o for o in all_orders if o['user_id'] == user_id]

    if not user_orders:
        await message.answer(
            "📭 У вас пока нет заказов.\n\n"
            "Сделайте первый заказ через кнопку '🛍️ Сделать заказ'!"
        )
        return

    user_orders = sorted(user_orders, key=lambda x: x['order_id'], reverse=True)

    text = "📋 МОИ ЗАКАЗЫ\n\n"

    for order in user_orders[:10]:
        status_emoji = STATUS_EMOJI.get(order['status'], '❓')
        text += f"{status_emoji} Заказ #{order['order_id']}\n"
        text += f"  📏 {order['size_name']}\n"
        text += f"  🔢 {order['quantity']} шт.\n"
        text += f"  💰 {order['total_price']} ₽\n"
        text += f"  📅 {order['date'][:16]}\n"
        text += f"  Статус: {order['status']}\n"
        text += f"{'─' * 20}\n"

    if len(user_orders) > 10:
        text += f"\n... и ещё {len(user_orders) - 10} заказов"

    buttons = [
        ['📋 Все заказы', f'history_all_{user_id}'],
        ['🆕 Новые', f'history_new_{user_id}'],
        ['✅ Выполненные', f'history_completed_{user_id}']
    ]

    if user_orders:
        buttons.append(['🔄 Заказать ещё', f'repeat_order_{user_orders[0]["order_id"]}'])

    await message.answer(text, reply_markup=inline_kb(buttons))


@dp.message(F.text == '🛒 Корзина')
async def btn_cart(message: Message):
    user_id = message.from_user.id
    items = get_cart(user_id)

    if not items:
        await message.answer("🛒 Ваша корзина пуста.")
        return

    text = "🛒 ВАША КОРЗИНА:\n\n"
    total = 0
    for i, item in enumerate(items):
        subtotal = item['price'] * item['quantity']
        total += subtotal
        text += f"{i + 1}. {item['size_name']}\n"
        text += f"   x{item['quantity']} шт. = {subtotal} ₽\n"
        if item.get('example'):
            text += f"   🖼 Дизайн: {item['example']}\n"
        text += "\n"

    text += f"💰 ИТОГО: {total} ₽"

    await message.answer(text, reply_markup=cart_kb(user_id))


@dp.message(F.text == '🛍️ Сделать заказ')
async def btn_make_order(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(OrderStates.size)
    await message.answer(
        "📏 Выберите размер наклейки:\n\n"
        "💡 Вы можете загрузить свой дизайн или выбрать из примеров",
        reply_markup=size_kb()
    )


@dp.message(F.text == '🖼 Примеры дизайнов')
async def btn_examples(message: Message):
    examples = load_examples()
    if not examples:
        return await message.answer("📭 Примеры пока не добавлены.")
    await message.answer(
        "🖼 ГАЛЕРЕЯ ДИЗАЙНОВ\n\nВыберите пример:",
        reply_markup=inline_kb(
            [[f"📸 {ex['name']}", f"view_{ex['id']}"] for ex in examples] + [['🔙 Назад', 'back_to_menu']])
    )


# ============ ИСТОРИЯ ЗАКАЗОВ (ОБРАБОТЧИКИ) ============

@dp.callback_query(F.data.startswith('history_'))
async def cb_history(callback: CallbackQuery):
    """Обработчик для кнопок истории заказов"""
    user_id = callback.from_user.id
    parts = callback.data.split('_')

    if len(parts) >= 3:
        target_user_id = int(parts[2])
        if target_user_id != user_id:
            await callback.answer("⛔ Это не ваша история!")
            return

    filter_type = parts[1] if len(parts) >= 2 else 'all'

    status_map = {
        'all': None,
        'new': 'новый',
        'processing': 'в обработке',
        'ready': 'готов к отправке',
        'shipped': 'отправлен',
        'delivered': 'доставлен',
        'completed': 'выполнен'
    }

    status_filter = status_map.get(filter_type)

    all_orders = load_orders() + load_completed()
    user_orders = [o for o in all_orders if o['user_id'] == user_id]

    if status_filter:
        user_orders = [o for o in user_orders if o['status'] == status_filter]

    user_orders = sorted(user_orders, key=lambda x: x['order_id'], reverse=True)

    if not user_orders:
        text = "📭 Заказов не найдено."
        if status_filter:
            text = f"📭 Заказов со статусом '{status_filter}' не найдено."

        await callback.message.edit_text(
            text,
            reply_markup=inline_kb([['🔙 Назад', f'back_to_orders_{user_id}']])
        )
        await callback.answer()
        return

    text = "📋 ИСТОРИЯ ЗАКАЗОВ"
    if status_filter:
        text += f" ({status_filter})"
    text += ":\n\n"

    for order in user_orders[:20]:
        status_emoji = STATUS_EMOJI.get(order['status'], '❓')
        text += f"{status_emoji} Заказ #{order['order_id']}\n"
        text += f"  📏 {order['size_name']}\n"
        text += f"  🔢 {order['quantity']} шт.\n"
        text += f"  💰 {order['total_price']} ₽\n"
        text += f"  📅 {order['date'][:16]}\n"
        text += f"  Статус: {order['status']}\n"
        text += f"{'─' * 20}\n"

    if len(user_orders) > 20:
        text += f"\n... и ещё {len(user_orders) - 20} заказов"

    buttons = [
        ['📋 Все заказы', f'history_all_{user_id}'],
        ['🆕 Новые', f'history_new_{user_id}'],
        ['✅ Выполненные', f'history_completed_{user_id}']
    ]

    if user_orders:
        buttons.append(['🔄 Заказать ещё', f'repeat_order_{user_orders[0]["order_id"]}'])

    buttons.append(['🔙 Назад', f'back_to_orders_{user_id}'])

    await callback.message.edit_text(
        text,
        reply_markup=inline_kb(buttons)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith('back_to_orders_'))
async def cb_back_to_orders(callback: CallbackQuery):
    """Возврат к списку заказов"""
    user_id = int(callback.data.split('_')[3])

    if user_id != callback.from_user.id:
        await callback.answer("⛔ Это не ваша история!")
        return

    await btn_my_orders(callback.message)
    await callback.answer()


@dp.callback_query(F.data.startswith('repeat_order_'))
async def cb_repeat_order(callback: CallbackQuery):
    """Повтор заказа из истории"""
    order_id = int(callback.data.split('_')[2])
    user_id = callback.from_user.id

    order = None
    all_orders = load_orders() + load_completed()
    for o in all_orders:
        if o['order_id'] == order_id and o['user_id'] == user_id:
            order = o
            break

    if not order:
        await callback.answer("❌ Заказ не найден")
        return

    add_to_cart(
        user_id,
        order['size'],
        order['quantity'],
        order.get('design_file'),
        order.get('selected_example')
    )

    await callback.answer(f"✅ Заказ #{order_id} добавлен в корзину!")
    await callback.message.answer(
        f"✅ Товар добавлен в корзину!\n"
        f"Перейдите в 🛒 Корзину для оформления."
    )


# ============ ОБРАБОТЧИКИ ЗАКАЗОВ ============

@dp.callback_query(F.data == 'back_to_menu')
async def cb_back_to_menu(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()


@dp.callback_query(F.data == 'back_to_order')
async def cb_back_to_order(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OrderStates.size)
    await callback.message.edit_text(
        "📏 Выберите размер наклейки:\n\n"
        "💡 Вы можете загрузить свой дизайн или выбрать из примеров",
        reply_markup=size_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == 'show_examples')
async def cb_show_examples(callback: CallbackQuery):
    examples = load_examples()
    if not examples:
        await callback.message.answer("📭 Примеры пока не добавлены.")
        await callback.answer()
        return

    await callback.message.edit_text(
        "🖼 ГАЛЕРЕЯ ДИЗАЙНОВ\n\nВыберите пример:",
        reply_markup=inline_kb(
            [[f"📸 {ex['name']}", f"view_{ex['id']}"] for ex in examples] + [['🔙 Назад', 'back_to_order']])
    )
    await callback.answer()


@dp.callback_query(F.data.startswith('size_'))
async def cb_select_size(callback: CallbackQuery, state: FSMContext):
    size_key = callback.data.split('_')[1]

    if size_key not in STICKER_SIZES:
        await callback.answer("❌ Неверный размер")
        return

    await state.update_data(size=size_key)
    await state.set_state(OrderStates.quantity)

    await callback.message.edit_text(
        f"✅ Вы выбрали: {STICKER_SIZES[size_key]['name']}\n"
        f"💰 Цена: {STICKER_SIZES[size_key]['price']} ₽/шт.\n\n"
        "🔢 Введите количество (числом):"
    )
    await callback.answer()


@dp.callback_query(F.data == 'upload_design')
async def cb_upload_design(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OrderStates.design)
    await callback.message.edit_text(
        "🎨 Отправьте изображение с дизайном.\n\n"
        "Поддерживаются: JPG, PNG, GIF\nМаксимум: 5 МБ"
    )
    await callback.answer()


@dp.callback_query(F.data == 'add_comment')
async def cb_add_comment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if 'size' not in data:
        await callback.answer("⚠️ Сначала выберите размер!")
        return

    await state.set_state(OrderStates.comment)
    await callback.message.edit_text(
        "💬 Введите комментарий к заказу:\n\n"
        "Опишите особые пожелания по дизайну, цветам, тексту..."
    )
    await callback.answer()


@dp.callback_query(F.data.startswith('view_'))
async def cb_view_example(callback: CallbackQuery):
    example_id = int(callback.data.split('_')[1])
    examples = load_examples()

    example = next((ex for ex in examples if ex['id'] == example_id), None)
    if not example or not os.path.exists(example['file_path']):
        await callback.answer("❌ Пример не найден")
        return

    await callback.message.answer_photo(
        FSInputFile(example['file_path']),
        caption=f"🖼 {example['name']}\n\n📝 {example['description']}",
        reply_markup=inline_kb([
            ['✅ Выбрать этот дизайн', f"select_{example_id}"],
            ['🔙 Назад', 'show_examples']
        ])
    )
    await callback.answer()


@dp.callback_query(F.data.startswith('select_'))
async def cb_select_example(callback: CallbackQuery, state: FSMContext):
    example_id = int(callback.data.split('_')[1])
    examples = load_examples()

    example = next((ex for ex in examples if ex['id'] == example_id), None)
    if not example:
        await callback.answer("❌ Пример не найден")
        return

    await state.update_data(
        selected_example=example['name'],
        selected_example_id=example['id']
    )

    if os.path.exists(example['file_path']):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        dest = f"{DESIGNS_FOLDER}/{callback.from_user.id}_example_{timestamp}.jpg"
        shutil.copy2(example['file_path'], dest)
        await state.update_data(design_file=dest)

    await callback.answer(f"✅ Выбран дизайн: {example['name']}")
    await callback.message.answer(f"✅ Выбран дизайн: {example['name']}")

    data = await state.get_data()
    if 'size' in data and 'quantity' in data:
        await show_order_summary(callback.message, state)
    else:
        await state.set_state(OrderStates.size)
        await callback.message.answer(
            "📏 Теперь выберите размер наклейки:",
            reply_markup=size_kb()
        )


@dp.message(OrderStates.quantity)
async def handle_quantity(message: Message, state: FSMContext):
    try:
        quantity = int(message.text.strip())
        if quantity < 1 or quantity > 100:
            await message.answer("❌ Введите число от 1 до 100.")
            return

        await state.update_data(quantity=quantity)

        data = await state.get_data()
        if data.get('design_file'):
            await show_order_summary(message, state)
        else:
            await message.answer(
                "📏 Размер выбран!\n\n"
                "Теперь вы можете:\n"
                "• Загрузить свой дизайн (кнопка ниже)\n"
                "• Выбрать из примеров\n"
                "• Продолжить без дизайна",
                reply_markup=inline_kb([
                    ['🎨 Загрузить дизайн', 'upload_design'],
                    ['🖼 Выбрать из примеров', 'show_examples'],
                    ['➡️ Продолжить без дизайна', 'continue_no_design']
                ])
            )
    except ValueError:
        await message.answer("❌ Введите число (например: 5)")


@dp.callback_query(F.data == 'continue_no_design')
async def cb_continue_no_design(callback: CallbackQuery, state: FSMContext):
    await show_order_summary(callback.message, state)
    await callback.answer()


@dp.message(OrderStates.comment)
async def handle_comment(message: Message, state: FSMContext):
    if not message.text.strip():
        await message.answer("❌ Комментарий не может быть пустым.")
        return

    await state.update_data(comment=message.text.strip())
    await message.answer("✅ Комментарий сохранен.")
    await show_order_summary(message, state)


@dp.message(OrderStates.design, F.photo)
async def handle_design(message: Message, state: FSMContext):
    try:
        file = await bot.get_file(message.photo[-1].file_id)
        file_data = await bot.download_file(file.file_path)

        filename = f"{DESIGNS_FOLDER}/{message.from_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        with open(filename, 'wb') as f:
            f.write(file_data.getvalue())

        await state.update_data(design_file=filename)
        await message.answer("✅ Дизайн загружен!")

        data = await state.get_data()
        if 'size' in data and 'quantity' in data:
            await show_order_summary(message, state)
        else:
            await state.set_state(OrderStates.size)
            await message.answer("Теперь выберите размер:", reply_markup=size_kb())

    except Exception as e:
        await message.answer(f"❌ Ошибка при загрузке: {e}")


@dp.message(OrderStates.design)
async def handle_design_invalid(message: Message):
    await message.answer("❌ Пожалуйста, отправьте изображение (фото).")


@dp.callback_query(F.data == 'change_quantity')
async def cb_change_quantity(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OrderStates.quantity)
    await callback.message.edit_text("🔢 Введите новое количество (числом):")
    await callback.answer()


@dp.callback_query(F.data == 'change_order')
async def cb_change_order(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    design_file = data.get('design_file')

    await state.clear()
    if design_file:
        await state.update_data(design_file=design_file)

    await state.set_state(OrderStates.size)
    await callback.message.edit_text(
        "📏 Выберите размер наклейки:",
        reply_markup=size_kb()
    )
    await callback.answer()


@dp.callback_query(F.data == 'confirm_order')
async def cb_confirm_order(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    if 'size' not in data or 'quantity' not in data:
        await callback.answer("❌ Заказ неполный. Начните заново.")
        return

    user_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.first_name

    order = save_new_order(user_id, username, data)

    await callback.message.edit_text(
        f"✅ ЗАКАЗ #{order['order_id']} СОЗДАН!\n\n"
        f"Размер: {order['size_name']}\n"
        f"Количество: {order['quantity']} шт.\n"
        f"Сумма: {order['total_price']} ₽\n"
        f"Дизайн: {'✅ загружен' if order.get('design_file') else '⚠️ не загружен'}\n"
        f"Комментарий: {order.get('comment', 'нет')}\n\n"
        f"Спасибо за заказ! Мы свяжемся с вами."
    )

    await notify_admins(order)
    await state.clear()
    await callback.answer("✅ Заказ оформлен!")


# ============ КОРЗИНА ============

@dp.callback_query(F.data.startswith('remove_cart_'))
async def cb_remove_from_cart(callback: CallbackQuery):
    user_id = callback.from_user.id
    index = int(callback.data.split('_')[2])

    if remove_from_cart(user_id, index):
        await callback.answer("✅ Товар удалён")
        await btn_cart(callback.message)
    else:
        await callback.answer("❌ Ошибка")


@dp.callback_query(F.data == 'clear_cart')
async def cb_clear_cart(callback: CallbackQuery):
    user_id = callback.from_user.id
    clear_cart(user_id)
    await callback.answer("🗑 Корзина очищена")
    await btn_cart(callback.message)


@dp.callback_query(F.data == 'checkout_cart')
async def cb_checkout_cart(callback: CallbackQuery):
    user_id = callback.from_user.id
    items = get_cart(user_id)

    if not items:
        await callback.answer("❌ Корзина пуста")
        return

    total = get_cart_total(user_id)

    text = f"💳 ОФОРМЛЕНИЕ ЗАКАЗА\n\n"
    text += f"Товары в корзине:\n"
    for i, item in enumerate(items):
        text += f"  {i + 1}. {item['size_name']} x{item['quantity']} шт. = {item['price'] * item['quantity']} ₽\n"
    text += f"\n💰 Итог: {total} ₽"

    buttons = [
        ['✅ Подтвердить заказ', 'confirm_cart'],
        ['🔙 Назад', 'back_to_cart']
    ]

    await callback.message.edit_text(text, reply_markup=inline_kb(buttons, 2))
    await callback.answer()


@dp.callback_query(F.data == 'confirm_cart')
async def cb_confirm_cart(callback: CallbackQuery):
    user_id = callback.from_user.id
    items = get_cart(user_id)

    if not items:
        await callback.answer("❌ Корзина пуста")
        return

    username = callback.from_user.username or callback.from_user.first_name

    total = get_cart_total(user_id)

    orders = load_orders()
    order = {
        'order_id': len(orders) + 1 if orders else 1,
        'user_id': user_id,
        'username': username,
        'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'items': items,
        'total_price': total,
        'status': 'новый'
    }

    orders.append(order)
    save_orders(orders)

    clear_cart(user_id)

    await callback.message.edit_text(
        f"✅ ЗАКАЗ #{order['order_id']} СОЗДАН!\n\n"
        f"Товаров: {len(items)} шт.\n"
        f"Сумма: {total} ₽\n\n"
        f"Спасибо за заказ! Мы свяжемся с вами."
    )

    await notify_admins(order)
    await callback.answer("✅ Заказ оформлен!")


@dp.callback_query(F.data == 'back_to_cart')
async def cb_back_to_cart(callback: CallbackQuery):
    await btn_cart(callback.message)
    await callback.answer()


# ============ АДМИНСКИЕ CALLBACK ============

@dp.callback_query(F.data.startswith('admin_'))
async def cb_admin(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ У вас нет прав!")

    action = callback.data.split('_')[1]
    orders, completed = load_orders(), load_completed()

    if action == 'new':
        new_orders = [o for o in orders if o['status'] == 'новый']
        if new_orders:
            text = "🆕 НОВЫЕ ЗАКАЗЫ:\n\n"
            for order in new_orders:
                text += format_order(order)
        else:
            text = "✅ Новых заказов нет."

    elif action == 'all':
        text = "📋 ВСЕ ЗАКАЗЫ:\n\n"
        if orders:
            for order in orders:
                text += format_order(order)
        else:
            text = "📭 Активных заказов нет."

    elif action == 'completed':
        text = "📦 ВЫПОЛНЕННЫЕ:\n\n"
        if completed:
            for order in reversed(completed[-10:]):
                text += format_order(order, True)
        else:
            text = "📭 Нет выполненных."

    elif action == 'stats':
        total_orders, total_revenue = len(orders), sum(o['total_price'] for o in orders)
        total_completed, completed_revenue = len(completed), sum(o['total_price'] for o in completed)
        text = f"📊 СТАТИСТИКА\n\n"
        text += f"📦 Активных: {total_orders} (💰 {total_revenue} ₽)\n"
        text += f"📦 Выполнено: {total_completed} (💰 {completed_revenue} ₽)\n"
        text += f"📈 Всего: {total_orders + total_completed}\n"
        text += f"💰 Общая выручка: {total_revenue + completed_revenue} ₽"

    elif action == 'list':
        text = "📋 /list - список команд"

    elif action == 'web':
        if FLASK_AVAILABLE:
            text = (
                "🌐 ВЕБ-ИНТЕРФЕЙС\n\n"
                f"Откройте в браузере: http://localhost:5000\n\n"
                f"Пароль: {ADMIN_PASSWORD}\n\n"
                "⚠️ Для доступа из интернета используйте ngrok или подобные сервисы."
            )
        else:
            text = (
                "❌ Веб-интерфейс недоступен!\n\n"
                "Установите Flask:\n"
                "pip install flask"
            )
        await callback.message.edit_text(text)
        await callback.answer()
        return

    await callback.message.edit_text(text)
    await callback.answer()


@dp.callback_query(F.data.startswith('del_ex_'))
async def cb_delete_example(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("⛔ У вас нет прав!")

    example_id = int(callback.data.split('_')[2])
    examples = load_examples()

    for i, example in enumerate(examples):
        if example['id'] == example_id:
            if os.path.exists(example['file_path']):
                os.remove(example['file_path'])
            del examples[i]
            save_examples(examples)
            await callback.answer("✅ Пример удален!")
            await callback.message.edit_text(f"✅ Пример #{example_id} удален!")
            return
    await callback.answer("❌ Пример не найден")


@dp.callback_query(F.data == 'cancel_del')
async def cb_cancel_delete(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("Отменено")



# ============ ЗАКАЗЫ ИЗ TELEGRAM MINI APP ============
def next_order_id() -> int:
    ids = [int(o.get('order_id', 0)) for o in load_orders() + load_completed()]
    return max(ids, default=0) + 1

async def notify_webapp_order(order: Dict[str, Any]) -> None:
    text = (
        f"🔔 <b>НОВЫЙ ЗАКАЗ #{order['order_id']}</b>\n\n"
        f"Клиент: {order['contact']}\n"
        f"Плёнка: {order['film_title']}\n"
        f"Дизайн: {order['design_title']}\n"
        f"Цвет: {order['color_title']}\n"
        f"Размер: {order['w']} × {order['h']} см\n"
        f"Тираж: {order['quantity']} шт.\n"
        f"Цена за шт.: {order['price']} ₽\n"
        f"Итого: <b>{order['total_price']} ₽</b>\n"
        f"Комментарий: {order.get('comment') or 'нет'}"
    )
    for admin_id in ADMIN_IDS:
        try: await bot.send_message(admin_id, text)
        except Exception as exc: print(f"Ошибка уведомления админа {admin_id}: {exc}")

@dp.message(F.web_app_data)
async def webapp_order(message: Message):
    try:
        payload=json.loads(message.web_app_data.data)
        quote=calculate_webapp(payload)
        contact=str(payload.get('contact','')).strip()
        if not re.fullmatch(r'@?[A-Za-z0-9_]{5,32}', contact):
            raise ValueError('Укажите корректный Telegram username')
        if not contact.startswith('@'): contact='@'+contact
        order={
            'order_id':next_order_id(),'user_id':message.from_user.id,
            'username':contact.lstrip('@'),'contact':contact,
            'date':datetime.now().strftime('%Y-%m-%d %H:%M:%S'),'source':'webapp',
            'film':quote['film'],'film_title':FILMS[quote['film']][0],
            'design':quote['design'],'design_title':DESIGNS[quote['design']][0],
            'color':quote['color'],'color_title':COLORS[quote['color']],
            'w':quote['w'],'h':quote['h'],'size':'custom',
            'size_name':f"{quote['w']}×{quote['h']} см · {FILMS[quote['film']][0]}",
            'price':quote['unit'],'quantity':quote['qty'],'total_price':quote['total'],
            'comment':str(payload.get('comment','')).strip()[:600],
            'design_file':'','selected_example':'','status':'новый'
        }
        orders=load_orders(); orders.append(order); save_orders(orders)
        await notify_webapp_order(order)
        await message.answer(
            f"✅ <b>Заказ #{order['order_id']} принят</b>\n\n"
            f"{order['size_name']} · {order['quantity']} шт.\n"
            f"Итого: <b>{order['total_price']} ₽</b>\n\n"
            "Менеджер напишет вам в Telegram.", reply_markup=main_kb(message.from_user.id)
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        await message.answer(f"⚠️ Не удалось принять заказ: {exc}")


# ============ НЕИЗВЕСТНЫЕ СООБЩЕНИЯ ============

@dp.message()
async def unknown(message: Message):
    await message.answer(
        "❓ Я не понимаю эту команду.\nИспользуйте кнопки меню.",
        reply_markup=main_kb(message.from_user.id)
    )


# ============ WEB-ИНТЕРФЕЙС (FLASK) С ЧЁРНЫМ ФОНОМ ============

if FLASK_AVAILABLE:
    WEB_TEMPLATE = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Админ-панель - Заказы наклеек</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                background: #0a0a0a; 
                color: #ffffff;
                padding: 20px; 
            }
            .container { max-width: 1400px; margin: 0 auto; }
            h1 { 
                color: #00ff88; 
                margin-bottom: 20px;
                text-shadow: 0 0 20px rgba(0, 255, 136, 0.3);
            }
            .stats { 
                display: grid; 
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); 
                gap: 15px; 
                margin-bottom: 30px; 
            }
            .stat-card { 
                background: #1a1a2e; 
                padding: 20px; 
                border-radius: 10px; 
                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.5);
                border: 1px solid #2a2a4e;
                transition: all 0.3s ease;
            }
            .stat-card:hover {
                transform: translateY(-5px);
                box-shadow: 0 8px 25px rgba(0, 255, 136, 0.1);
                border-color: #00ff88;
            }
            .stat-card h3 { 
                color: #8888aa; 
                font-size: 14px; 
                text-transform: uppercase; 
                letter-spacing: 1px;
            }
            .stat-card .value { 
                font-size: 28px; 
                font-weight: bold; 
                color: #00ff88;
                text-shadow: 0 0 10px rgba(0, 255, 136, 0.2);
            }
            .stat-card .value.revenue {
                color: #ffd700;
                text-shadow: 0 0 10px rgba(255, 215, 0, 0.2);
            }
            .filters { 
                margin-bottom: 20px; 
                display: flex; 
                gap: 10px; 
                flex-wrap: wrap; 
            }
            .filters button { 
                padding: 10px 20px; 
                border: 2px solid #2a2a4e; 
                background: #1a1a2e; 
                color: #ffffff;
                border-radius: 8px; 
                cursor: pointer; 
                transition: all 0.3s ease;
                font-size: 14px;
            }
            .filters button:hover {
                border-color: #00ff88;
                background: #2a2a4e;
            }
            .filters button.active { 
                background: #00ff88; 
                color: #0a0a0a; 
                border-color: #00ff88;
                font-weight: bold;
            }
            table { 
                width: 100%; 
                background: #1a1a2e; 
                border-radius: 10px; 
                overflow: hidden; 
                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.5);
                border: 1px solid #2a2a4e;
            }
            th { 
                background: #2a2a4e; 
                padding: 15px; 
                text-align: left; 
                color: #8888aa;
                font-weight: 600;
                text-transform: uppercase;
                font-size: 12px;
                letter-spacing: 1px;
            }
            td { 
                padding: 12px 15px; 
                border-top: 1px solid #2a2a4e; 
                color: #ccccdd;
            }
            tr:hover {
                background: #222244;
            }
            .status { 
                padding: 4px 14px; 
                border-radius: 20px; 
                font-size: 12px; 
                font-weight: bold; 
                display: inline-block;
            }
            .status-новый { background: #ffc107; color: #0a0a0a; }
            .status-в-обработке { background: #17a2b8; color: #ffffff; }
            .status-готов-к-отправке { background: #28a745; color: #ffffff; }
            .status-отправлен { background: #6f42c1; color: #ffffff; }
            .status-доставлен { background: #007bff; color: #ffffff; }
            .status-выполнен { background: #00ff88; color: #0a0a0a; }

            .btn { 
                padding: 4px 12px; 
                border: none; 
                border-radius: 6px; 
                cursor: pointer; 
                margin: 2px;
                font-size: 13px;
                transition: all 0.3s ease;
            }
            .btn-success { 
                background: #28a745; 
                color: white;
                padding: 6px 14px;
            }
            .btn-success:hover {
                background: #34ce57;
                transform: scale(1.05);
            }
            .btn-sm { font-size: 12px; padding: 2px 8px; }

            .login { 
                max-width: 400px; 
                margin: 100px auto; 
                background: #1a1a2e; 
                padding: 40px; 
                border-radius: 15px; 
                box-shadow: 0 4px 30px rgba(0, 0, 0, 0.8);
                border: 1px solid #2a2a4e;
            }
            .login input { 
                width: 100%; 
                padding: 12px; 
                margin: 10px 0; 
                border: 2px solid #2a2a4e; 
                border-radius: 8px; 
                background: #0a0a0a;
                color: #ffffff;
                font-size: 16px;
                transition: border-color 0.3s ease;
            }
            .login input:focus {
                border-color: #00ff88;
                outline: none;
            }
            .login button { 
                width: 100%; 
                padding: 12px; 
                background: #00ff88; 
                color: #0a0a0a; 
                border: none; 
                border-radius: 8px; 
                font-size: 16px; 
                cursor: pointer;
                font-weight: bold;
                transition: all 0.3s ease;
            }
            .login button:hover {
                transform: scale(1.02);
                box-shadow: 0 0 30px rgba(0, 255, 136, 0.3);
            }
            .login h2 { 
                text-align: center; 
                margin-bottom: 20px; 
                color: #00ff88;
            }

            .info-box { 
                margin-top: 20px; 
                padding: 15px; 
                background: #1a1a2e; 
                border-radius: 10px; 
                border: 1px solid #2a2a4e;
            }
            .info-box p {
                color: #8888aa;
                margin-bottom: 10px;
            }
            .code { 
                background: #0a0a0a; 
                color: #00ff88; 
                padding: 12px; 
                border-radius: 8px; 
                font-family: 'Courier New', monospace;
                border: 1px solid #2a2a4e;
                line-height: 1.8;
            }
            select {
                background: #0a0a0a;
                color: #ffffff;
                border: 1px solid #2a2a4e;
                padding: 6px 10px;
                border-radius: 6px;
                cursor: pointer;
            }
            select:hover {
                border-color: #00ff88;
            }
            select option {
                background: #1a1a2e;
                color: #ffffff;
            }
            @media (max-width: 768px) {
                .stats { grid-template-columns: 1fr; }
                .filters button { padding: 8px 12px; font-size: 12px; }
                td { font-size: 12px; padding: 8px; }
                th { font-size: 10px; padding: 8px; }
            }
        </style>
    </head>
    <body>
        {% if not logged_in %}
        <div class="login">
            <h2>🔐 Вход в админ-панель</h2>
            <form method="POST">
                <input type="password" name="password" placeholder="Введите пароль" required>
                <button type="submit">Войти</button>
            </form>
        </div>
        {% else %}
        <div class="container">
            <h1>📊 Панель управления заказами</h1>

            <div class="stats">
                <div class="stat-card">
                    <h3>Всего заказов</h3>
                    <div class="value">{{ stats.total }}</div>
                </div>
                <div class="stat-card">
                    <h3>Активных</h3>
                    <div class="value">{{ stats.active }}</div>
                </div>
                <div class="stat-card">
                    <h3>Выполнено</h3>
                    <div class="value">{{ stats.completed }}</div>
                </div>
                <div class="stat-card">
                    <h3>Выручка</h3>
                    <div class="value revenue">{{ stats.revenue }} ₽</div>
                </div>
            </div>

            <div class="filters">
                <button onclick="filter('all')" class="active">Все</button>
                <button onclick="filter('новый')">🆕 Новые</button>
                <button onclick="filter('в обработке')">🔄 В обработке</button>
                <button onclick="filter('готов к отправке')">📦 Готовы</button>
                <button onclick="filter('отправлен')">🚚 Отправлены</button>
                <button onclick="filter('доставлен')">✅ Доставлены</button>
                <button onclick="filter('выполнен')">🎉 Выполнены</button>
            </div>

            <div style="overflow-x: auto;">
                <table id="ordersTable">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Пользователь</th>
                            <th>Размер</th>
                            <th>Кол-во</th>
                            <th>Сумма</th>
                            <th>Дата</th>
                            <th>Статус</th>
                            <th>Действия</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for order in orders %}
                        <tr>
                            <td><strong>#{{ order.order_id }}</strong></td>
                            <td>@{{ order.username }}</td>
                            <td>{{ order.size_name }}</td>
                            <td>{{ order.quantity }}</td>
                            <td><strong>{{ order.total_price }} ₽</strong></td>
                            <td>{{ order.date[:16] }}</td>
                            <td>
                                <span class="status status-{{ order.status|replace(' ', '-') }}">
                                    {{ order.status }}
                                </span>
                            </td>
                            <td>
                                <select onchange="changeStatus({{ order.order_id }}, this.value)" style="padding: 4px; border-radius: 4px; border: 1px solid #2a2a4e;">
                                    <option value="">Изменить</option>
                                    <option value="новый">🆕 Новый</option>
                                    <option value="в обработке">🔄 В обработке</option>
                                    <option value="готов к отправке">📦 Готов</option>
                                    <option value="отправлен">🚚 Отправлен</option>
                                    <option value="доставлен">✅ Доставлен</option>
                                </select>
                                <button class="btn btn-success btn-sm" onclick="completeOrder({{ order.order_id }})">✅</button>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <div class="info-box">
                <p>📱 Команды для работы с заказами в Telegram:</p>
                <div class="code">
                    /order [ID] - Просмотр заказа<br>
                    /status [ID] [статус] - Изменить статус<br>
                    /complete [ID] - Подтвердить выполнение<br>
                    /delete [ID] - Удалить заказ
                </div>
            </div>
        </div>

        <script>
            function filter(status) {
                const rows = document.querySelectorAll('#ordersTable tbody tr');
                const buttons = document.querySelectorAll('.filters button');
                buttons.forEach(b => b.classList.remove('active'));
                event.target.classList.add('active');

                rows.forEach(row => {
                    if (status === 'all') {
                        row.style.display = '';
                    } else {
                        const statusCell = row.querySelector('td:nth-child(7) span');
                        const statusText = statusCell ? statusCell.textContent.trim() : '';
                        row.style.display = statusText === status ? '' : 'none';
                    }
                });
            }

            function changeStatus(orderId, status) {
                if (!status) return;
                if (confirm(`Изменить статус заказа #${orderId} на "${status}"?`)) {
                    fetch('/api/status', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({order_id: orderId, status: status})
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            alert('✅ Статус изменён!');
                            location.reload();
                        } else {
                            alert('❌ ' + data.error);
                        }
                    });
                }
            }

            function completeOrder(orderId) {
                if (confirm(`Подтвердить выполнение заказа #${orderId}?`)) {
                    fetch('/api/complete', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({order_id: orderId})
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            alert('✅ Заказ выполнен!');
                            location.reload();
                        } else {
                            alert('❌ ' + data.error);
                        }
                    });
                }
            }
        </script>
        {% endif %}
    </body>
    </html>
    """

    flask_app = Flask(__name__)
    flask_app.secret_key = 'your-secret-key-change-it'


    @flask_app.route('/', methods=['GET', 'POST'])
    def web_admin():
        if request.method == 'POST':
            if request.form.get('password') == ADMIN_PASSWORD:
                session['logged_in'] = True
                return redirect(url_for('web_admin'))

        if not session.get('logged_in'):
            return render_template_string(WEB_TEMPLATE, logged_in=False)

        orders = load_orders()
        completed = load_completed()
        all_orders = orders + completed

        stats = {
            'total': len(all_orders),
            'active': len(orders),
            'completed': len(completed),
            'revenue': sum(o['total_price'] for o in all_orders)
        }

        return render_template_string(
            WEB_TEMPLATE,
            logged_in=True,
            orders=all_orders,
            stats=stats
        )


    @flask_app.route('/api/status', methods=['POST'])
    def api_change_status():
        if not session.get('logged_in'):
            return jsonify({'success': False, 'error': 'Не авторизован'})

        data = request.json
        order_id = data.get('order_id')
        status = data.get('status')

        orders = load_orders()
        for order in orders:
            if order['order_id'] == order_id:
                order['status'] = status
                save_orders(orders)
                return jsonify({'success': True})

        return jsonify({'success': False, 'error': 'Заказ не найден'})


    @flask_app.route('/api/complete', methods=['POST'])
    def api_complete_order():
        if not session.get('logged_in'):
            return jsonify({'success': False, 'error': 'Не авторизован'})

        data = request.json
        order_id = data.get('order_id')

        success, _ = complete_order(order_id)
        return jsonify({'success': success, 'error': None if success else 'Заказ не найден'})


    def run_flask():
        flask_app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)


# ============ ЗАПУСК ============

async def main():
    print("=" * 50)
    print("🤖 БОТ ДЛЯ ЗАКАЗА НАКЛЕЕК")
    print("=" * 50)
    print(f"👨‍💼 Администраторы: {ADMIN_IDS}")
    print(f"🌐 Mini App: {WEBAPP_URL or 'не задан'}")

    if FLASK_AVAILABLE:
        print(f"🌐 Веб-интерфейс: http://localhost:5000")
        print(f"🔑 Пароль: {ADMIN_PASSWORD}")
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print("✅ Веб-интерфейс запущен!")
    else:
        print("⚠️ Веб-интерфейс отключён (Flask не установлен)")
        print("   Установите: pip install flask")

    print("=" * 50)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка: {e}")