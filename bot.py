#!/usr/bin/env python3
"""
===============================================================================
PROJECT NAME: COLOMBIA TELEGRAM SMSBOWER BOT (OPTIMIZED FAST VERSION)
FRAMEWORK   : aiogram 3.x
PYTHON      : 3.10+
FILE        : bot.py
===============================================================================
"""

import asyncio
import html
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
import aiosqlite
from colorama import Fore, Back, Style, init
from flask import Flask

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

init(autoreset=True)

# =============================================================================
# LOGGING SYSTEM
# =============================================================================
class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED + Style.BRIGHT,
        logging.CRITICAL: Fore.WHITE + Back.RED + Style.BRIGHT,
    }

    def format(self, record: logging.LogRecord) -> str:
        log_color = self.COLORS.get(record.levelno, Fore.WHITE)
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = f"[{record.levelname:^8}]"
        message = super().format(record)
        return f"{Fore.BLACK + Style.BRIGHT}[{timestamp}]{Style.RESET_ALL} {log_color}{level}{Style.RESET_ALL} {message}"


logger = logging.getLogger("ColombiaTGBot")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColoredFormatter("%(message)s"))
logger.addHandler(handler)

# =============================================================================
# CONFIGURATION & TEXT CONSTANTS
# =============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8812644088:AAF9qv7kEfUG0WZmOdaJZbCr4_GhQrPJFYs")
ADMIN_ID = 7266067201
ADMIN_NAME = "MAHIN404"

SMSBOWER_BASE_URL = "https://smsbower.page/stubs/handler_api.php"
DB_FILE = "smsbower_bot.db"

TARGET_COUNTRY_ID = "33"
TARGET_COUNTRY_NAME = "Colombia"
TARGET_COUNTRY_FLAG = "🇨🇴"

TARGET_SERVICE_CODE = "tg"
TARGET_SERVICE_NAME = "Telegram"

# Direct Auto-Range Price Parameters (0.11$ to 0.13$)
AUTO_BUY_PRICES = [0.11, 0.12, 0.13]

active_search_sessions: Dict[int, Dict[str, Any]] = {}

# =============================================================================
# DATABASE ENGINE
# =============================================================================
class Database:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    api_key TEXT DEFAULT '',
                    is_banned INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    phone_number TEXT,
                    service TEXT DEFAULT 'Telegram',
                    country TEXT DEFAULT 'Colombia',
                    operator TEXT DEFAULT 'any',
                    price REAL,
                    status TEXT DEFAULT 'WAITING',
                    sms_code TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', 'off');"
            )
            await db.commit()

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def create_or_update_user(
        self, user_id: int, username: Optional[str], full_name: str, api_key: str = ""
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username, full_name, api_key)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name,
                    api_key = CASE WHEN excluded.api_key != '' THEN excluded.api_key ELSE users.api_key END;
                """,
                (user_id, username, full_name, api_key),
            )
            await db.commit()

    async def set_user_api_key(self, user_id: int, api_key: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET api_key = ? WHERE user_id = ?", (api_key, user_id))
            await db.commit()

    async def set_user_ban(self, user_id: int, is_banned: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (is_banned, user_id))
            await db.commit()

    async def add_order(
        self,
        order_id: str,
        user_id: int,
        phone_number: str,
        price: float,
        service: str = TARGET_SERVICE_NAME,
        country: str = TARGET_COUNTRY_NAME,
        operator: str = "any",
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO orders (order_id, user_id, phone_number, service, country, operator, price, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'WAITING')
                """,
                (order_id, user_id, phone_number, service, country, operator, price),
            )
            await db.commit()

    async def update_order_status(self, order_id: str, status: str, sms_code: str = ""):
        async with aiosqlite.connect(self.db_path) as db:
            if sms_code:
                await db.execute(
                    "UPDATE orders SET status = ?, sms_code = ? WHERE order_id = ?",
                    (status, sms_code, order_id),
                )
            else:
                await db.execute(
                    "UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id)
                )
            await db.commit()

    async def get_active_orders(self, user_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT *, strftime('%s', created_at) AS created_timestamp FROM orders WHERE user_id = ? AND status IN ('WAITING', 'RESEND', 'RECEIVED') ORDER BY created_at DESC",
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_user_orders(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT *, strftime('%s', created_at) AS created_timestamp FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_24h_otp_orders(self, user_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT *, strftime('%s', created_at) AS created_timestamp FROM orders WHERE user_id = ? AND status IN ('RECEIVED', 'COMPLETED') AND created_at >= datetime('now', '-1 day') ORDER BY created_at DESC",
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT *, strftime('%s', created_at) AS created_timestamp FROM orders WHERE order_id = ?", (order_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_stats(self) -> Dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as c1:
                total_users = (await c1.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1") as c2:
                banned_users = (await c2.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM orders") as c3:
                total_orders = (await c3.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM orders WHERE status = 'COMPLETED'") as c4:
                completed_orders = (await c4.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM orders WHERE status = 'CANCELLED'") as c5:
                cancelled_orders = (await c5.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM orders WHERE status IN ('WAITING', 'RESEND', 'RECEIVED')"
            ) as c6:
                active_orders = (await c6.fetchone())[0]

            return {
                "total_users": total_users,
                "banned_users": banned_users,
                "total_orders": total_orders,
                "completed_orders": completed_orders,
                "cancelled_orders": cancelled_orders,
                "active_orders": active_orders,
            }

    async def get_all_user_ids(self) -> List[int]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT user_id FROM users WHERE is_banned = 0") as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    async def set_setting(self, key: str, value: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            await db.commit()

    async def get_setting(self, key: str, default: str = ""):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else default


db = Database()

# =============================================================================
# API CLIENT (FAST ASYNC HTTP WITH OPTIMIZED RATE LIMITING)
# =============================================================================
class SMSBowerClient:
    def __init__(self, base_url: str = SMSBOWER_BASE_URL):
        self.base_url = base_url

    async def _request(self, params: Dict[str, Any]) -> str:
        for attempt in range(2):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.base_url, params=params, timeout=6) as resp:
                        text = await resp.text()
                        return text.strip()
            except Exception as e:
                logger.warning(f"SMSBower API Attempt {attempt+1} failed: {e}")
                await asyncio.sleep(0.3)
        raise Exception("API Connection Failed")

    async def get_balance(self, api_key: str) -> float:
        if not api_key:
            raise ValueError("API Key is missing.")
        res = await self._request({"action": "getBalance", "api_key": api_key})
        if res.startswith("ACCESS_BALANCE:"):
            return float(res.split(":")[1])
        elif res == "BAD_KEY":
            raise ValueError("Invalid API Key.")
        else:
            raise ValueError(f"Balance Error: {res}")

    async def get_number(
        self,
        api_key: str,
        service: str = TARGET_SERVICE_CODE,
        country: str = TARGET_COUNTRY_ID,
        operator: str = "any",
        max_price: Optional[float] = 0.13,
    ) -> Tuple[str, str]:
        params = {
            "action": "getNumber",
            "api_key": api_key,
            "service": service,
            "country": country,
            "operator": operator,
        }
        if max_price is not None:
            params["maxPrice"] = f"{max_price:.2f}"

        res = await self._request(params)
        if res.startswith("ACCESS_NUMBER:"):
            parts = res.split(":")
            return parts[1], parts[2]
        elif res == "NO_NUMBERS":
            raise ValueError("NO_NUMBERS")
        elif res == "NO_BALANCE":
            raise ValueError("NO_BALANCE")
        elif res == "BAD_KEY":
            raise ValueError("Invalid API Key.")
        else:
            raise ValueError(f"Order failed: {res}")

    async def get_status(self, api_key: str, order_id: str) -> Tuple[str, Optional[str]]:
        res = await self._request({"action": "getStatus", "api_key": api_key, "id": order_id})
        if res.startswith("STATUS_OK:"):
            return "STATUS_OK", res.split(":")[1]
        elif res == "STATUS_WAIT_CODE":
            return "STATUS_WAIT_CODE", None
        elif res in ("STATUS_WAIT_RETRY", "STATUS_WAIT_RESEND"):
            return "STATUS_WAIT_RESEND", None
        elif res == "STATUS_CANCEL":
            return "STATUS_CANCEL", None
        else:
            return res, None

    async def set_status(self, api_key: str, order_id: str, status_code: int) -> str:
        return await self._request(
            {
                "action": "setStatus",
                "api_key": api_key,
                "id": order_id,
                "status": str(status_code),
            }
        )


api_client = SMSBowerClient()

# =============================================================================
# STATES & FSM
# =============================================================================
class AuthStates(StatesGroup):
    waiting_for_api_key = State()


class UserStates(StatesGroup):
    waiting_for_api_key_update = State()


class BuyStates(StatesGroup):
    selecting_quantity = State()
    waiting_for_custom_quantity = State()


class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_user_search = State()
    waiting_for_ban_id = State()
    waiting_for_unban_id = State()


# =============================================================================
# KEYBOARDS
# =============================================================================
def get_main_reply_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="BUY NUMBER"), KeyboardButton(text="ACTIVE NUMBER")],
        [KeyboardButton(text="MENU ☰")],
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="🛠️ Admin Panel")])

    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_dashboard_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="BUY NUMBER", callback_data="buy_start"),
                InlineKeyboardButton(text="ACTIVE NUMBER", callback_data="view_active"),
            ],
            [
                InlineKeyboardButton(text="MENU ☰", callback_data="open_full_menu"),
            ],
        ]
    )


def get_full_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="PROFILE", callback_data="view_profile"),
                InlineKeyboardButton(text="📊 24H OTP HISTORY", callback_data="view_24h_otp"),
            ],
            [
                InlineKeyboardButton(text="« BACK", callback_data="refresh_dash"),
            ],
        ]
    )


def get_quantity_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="1️⃣ Single (1)", callback_data="select_qty:1"),
            InlineKeyboardButton(text="2️⃣ Numbers (2)", callback_data="select_qty:2"),
        ],
        [
            InlineKeyboardButton(text="3️⃣ Numbers (3)", callback_data="select_qty:3"),
            InlineKeyboardButton(text="5️⃣ Numbers (5)", callback_data="select_qty:5"),
        ],
        [
            InlineKeyboardButton(text="🔟 Numbers (10)", callback_data="select_qty:10"),
            InlineKeyboardButton(text="2️⃣0️⃣ Numbers (20)", callback_data="select_qty:20"),
        ],
        [InlineKeyboardButton(text="✏️ Custom Quantity", callback_data="select_qty:custom")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_keyboard(maint_status: str = "off") -> InlineKeyboardMarkup:
    maint_btn_text = "🟢 Turn Maintenance ON" if maint_status == "off" else "🔴 Turn Maintenance OFF"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📢 Broadcast Message", callback_data="admin_broadcast"),
                InlineKeyboardButton(text="📊 Live Statistics", callback_data="admin_stats"),
            ],
            [
                InlineKeyboardButton(text="🔍 Search User Info", callback_data="admin_search_user"),
                InlineKeyboardButton(text=maint_btn_text, callback_data="admin_maint"),
            ],
            [
                InlineKeyboardButton(text="🚫 Ban User", callback_data="admin_ban"),
                InlineKeyboardButton(text="✅ Unban User", callback_data="admin_unban"),
            ],
            [
                InlineKeyboardButton(text="📋 Quick DB Summary", callback_data="admin_db_summary"),
                InlineKeyboardButton(text="❌ Close Panel", callback_data="cancel_action"),
            ],
        ]
    )


# =============================================================================
# ROUTER & MIDDLEWARE
# =============================================================================
router = Router()


async def check_maintenance_and_ban(event: Union[Message, CallbackQuery]) -> bool:
    user_id = event.from_user.id
    user = await db.get_user(user_id)

    if user and user.get("is_banned"):
        if isinstance(event, CallbackQuery):
            await event.answer("❌ You are banned from using this bot.", show_alert=True)
        else:
            await event.answer("❌ <b>You are banned from using this bot.</b>", parse_mode=ParseMode.HTML)
        return False

    if user_id != ADMIN_ID:
        maint = await db.get_setting("maintenance", "off")
        if maint == "on":
            msg = "🛠️ <b>System Maintenance Mode is Active.</b>\nPlease try again later."
            if isinstance(event, CallbackQuery):
                await event.answer("🛠️ Maintenance Mode Active. Access Restricted.", show_alert=True)
            else:
                await event.answer(msg, parse_mode=ParseMode.HTML)
            return False

    return True


async def edit_or_reply(
    event: Union[Message, CallbackQuery], text: str, reply_markup=None
) -> Message:
    if isinstance(event, CallbackQuery):
        try:
            return await event.message.edit_text(
                text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
            )
        except Exception:
            return await event.message.answer(
                text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
            )
    else:
        return await event.answer(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def send_dashboard(event: Union[Message, CallbackQuery], user_id: int):
    user = await db.get_user(user_id)
    if not user:
        return

    maint = await db.get_setting("maintenance", "off")
    maint_notice = "\n⚠️ <b>MAINTENANCE MODE IS CURRENTLY ACTIVE</b>\n" if maint == "on" else ""

    orders = await db.get_user_orders(user_id, limit=100)
    active_orders = [o for o in orders if o["status"] in ("WAITING", "RESEND", "RECEIVED")]
    completed_orders = [o for o in orders if o["status"] == "COMPLETED"]
    cancelled_orders = [o for o in orders if o["status"] == "CANCELLED"]

    api_key = user.get("api_key", "")
    balance_str = "Not Set"
    if api_key:
        try:
            balance = await api_client.get_balance(api_key)
            balance_str = f"${balance:.2f}"
        except Exception:
            balance_str = "Invalid API Key"

    dash_text = (
        f"<b>🇨🇴 COLOMBIA TELEGRAM SMS BOT 📱</b>\n"
        f"{maint_notice}"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>👋 Welcome:</b> <b>{html.escape(user.get('full_name', 'User'))}</b>\n"
        f"<b>🆔 User ID:</b> <code>{user_id}</code>\n"
        f"<b>🌍 Target Country:</b> {TARGET_COUNTRY_FLAG} <b>{TARGET_COUNTRY_NAME}</b>\n"
        f"<b>📱 Target Service:</b> <b>{TARGET_SERVICE_NAME}</b>\n"
        f"<b>💰 API Balance:</b> <code>{balance_str}</code>\n"
        f"<b>⚡ Active Numbers:</b> <code>{len(active_orders)}</code>\n"
        f"<b>✅ Completed:</b> <code>{len(completed_orders)}</code> | <b>❌ Cancelled:</b> <code>{len(cancelled_orders)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Select an option below:</b>"
    )

    is_admin = user_id == ADMIN_ID
    if isinstance(event, CallbackQuery):
        await edit_or_reply(event, dash_text, reply_markup=get_dashboard_inline_keyboard())
    else:
        await event.answer(
            dash_text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_reply_keyboard(is_admin=is_admin),
        )


# =============================================================================
# START & ONBOARDING
# =============================================================================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if not await check_maintenance_and_ban(message):
        return

    user_id = message.from_user.id
    user = await db.get_user(user_id)

    if user and user.get("api_key"):
        await state.clear()
        await send_dashboard(message, user_id)
        return

    await state.set_state(AuthStates.waiting_for_api_key)
    await message.answer(
        "<b>🔑 WELCOME TO COLOMBIA TELEGRAM SMS BOT</b>\n\n"
        "<b>Please send your personal SMSBower API Key to start buying numbers:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(AuthStates.waiting_for_api_key)
async def process_onboarding_api_key(message: Message, state: FSMContext):
    api_key_input = message.text.strip() if message.text else ""
    user_id = message.from_user.id

    try:
        balance = await api_client.get_balance(api_key_input)
        await db.create_or_update_user(
            user_id=user_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            api_key=api_key_input,
        )
        await state.clear()
        await message.answer(
            f"<b>✅ ACCOUNT LINKED SUCCESSFULLY!</b>\n\n"
            f"<b>💰 Current Balance:</b> <code>${balance:.2f}</code>",
            parse_mode=ParseMode.HTML,
        )
        await send_dashboard(message, user_id)
    except Exception as e:
        await message.answer(
            f"<b>❌ INVALID SMSBOWER API KEY!</b>\n"
            f"<b>Reason:</b> {html.escape(str(e))}\n\n"
            f"<b>Please enter a valid API key from SMSBower:</b>",
            parse_mode=ParseMode.HTML,
        )


# =============================================================================
# MENU NAVIGATION HANDLERS
# =============================================================================
@router.message(F.text == "MENU ☰")
@router.callback_query(F.data == "open_full_menu")
async def handle_open_full_menu(event: Union[Message, CallbackQuery]):
    if not await check_maintenance_and_ban(event):
        return

    menu_text = "<b>Select an option from Menu:</b>"
    await edit_or_reply(event, menu_text, reply_markup=get_full_menu_keyboard())
    if isinstance(event, CallbackQuery):
        await event.answer()


# =============================================================================
# DIRECT AUTO-BUY WORKFLOW (AUTOMATIC 0.11$ - 0.13$ RANGE, LIVE UPDATE & SAFE SPEED)
# =============================================================================
@router.message(F.text.in_({"BUY NUMBER", "📱 Buy Telegram Number"}))
@router.callback_query(F.data == "buy_start")
async def start_buy_flow(event: Union[Message, CallbackQuery], state: FSMContext):
    if not await check_maintenance_and_ban(event):
        return

    user_id = event.from_user.id
    user = await db.get_user(user_id)
    if not user or not user.get("api_key"):
        await edit_or_reply(event, "<b>⚠️ API Key missing! Please enter your API key first.</b>")
        if isinstance(event, Message):
            await state.set_state(AuthStates.waiting_for_api_key)
        return

    await state.set_state(BuyStates.selecting_quantity)
    text = (
        f"<b>🔢 SELECT QUANTITY</b>\n\n"
        f"<b>Country:</b> {TARGET_COUNTRY_FLAG} <b>{TARGET_COUNTRY_NAME}</b>\n"
        f"<b>Price Range:</b> <code>$0.11 - $0.13 Auto Range</code>\n\n"
        f"<b>How many numbers do you want to buy?</b>"
    )
    await edit_or_reply(event, text, reply_markup=get_quantity_keyboard())


@router.callback_query(F.data.startswith("select_qty:"))
async def process_select_quantity(callback: CallbackQuery, state: FSMContext):
    if not await check_maintenance_and_ban(callback):
        return

    qty_val = callback.data.split(":")[1]
    if qty_val == "custom":
        await state.set_state(BuyStates.waiting_for_custom_quantity)
        await callback.message.edit_text(
            "<b>✏️ ENTER CUSTOM QUANTITY</b>\n\n<b>Please enter quantity (1 to 50):</b>",
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    qty = int(qty_val)
    await start_auto_purchasing(callback, state, qty)


@router.message(BuyStates.waiting_for_custom_quantity)
async def process_custom_quantity_input(message: Message, state: FSMContext):
    if not await check_maintenance_and_ban(message):
        return

    try:
        qty = int(message.text.strip())
        if qty < 1 or qty > 50:
            await message.answer("<b>❌ Quantity must be between 1 and 50. Try again:</b>", parse_mode=ParseMode.HTML)
            return
        await start_auto_purchasing(message, state, qty)
    except ValueError:
        await message.answer("<b>❌ Invalid number. Please enter a valid quantity:</b>", parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "stop_search_continue")
async def handle_stop_search_continue(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in active_search_sessions:
        active_search_sessions[user_id]["stop"] = True
        await callback.answer("⚡ Search stopped! Process complete for found numbers.", show_alert=True)
    else:
        await callback.answer("No active search session running.", show_alert=True)


async def fetch_single_number_safe(user_id: int, api_key: str) -> Optional[Tuple[str, str, int]]:
    for price in AUTO_BUY_PRICES:
        try:
            order_id, phone_number = await api_client.get_number(
                api_key=api_key,
                service=TARGET_SERVICE_CODE,
                country=TARGET_COUNTRY_ID,
                operator="any",
                max_price=price,
            )

            # PREFIX FILTER (+57319 / 57319 / 319 CANCEL & RE-SEARCH)
            clean_phone = phone_number.lstrip("+")
            if clean_phone.startswith("57319") or clean_phone.startswith("319"):
                logger.info(f"Filtered out number {phone_number} (319 prefix). Auto cancelling order {order_id}...")
                try:
                    await api_client.set_status(api_key, order_id, 8)
                except Exception as e:
                    logger.warning(f"Failed to cancel filtered order {order_id}: {e}")
                continue

            await api_client.set_status(api_key, order_id, 1)
            created_ts = int(time.time())
            await db.add_order(
                order_id=order_id,
                user_id=user_id,
                phone_number=phone_number,
                price=price,
            )
            return order_id, phone_number, created_ts
        except Exception as e:
            if str(e) == "NO_NUMBERS":
                continue
            elif str(e) == "NO_BALANCE":
                return None
            else:
                logger.warning(f"Fetch number error at price ${price}: {e}")
                continue
    return None


async def start_auto_purchasing(event: Union[Message, CallbackQuery], state: FSMContext, qty: int):
    user_id = event.from_user.id
    user = await db.get_user(user_id)
    api_key = user.get("api_key", "") if user else ""

    await state.clear()
    successful_orders: List[Tuple[str, str, int]] = []
    start_search_time = time.time()
    
    active_search_sessions[user_id] = {"stop": False}
    last_ui_update_time = 0.0

    while len(successful_orders) < qty:
        if active_search_sessions.get(user_id, {}).get("stop", False):
            break

        elapsed_sec = int(time.time() - start_search_time)
        mins, secs = divmod(elapsed_sec, 60)
        time_str = f"{mins:02d}:{secs:02d}"

        current_time = time.time()
        if current_time - last_ui_update_time >= 0.8 or len(successful_orders) == qty:
            last_ui_update_time = current_time

            live_list_str = ""
            current_now = int(time.time())
            for oid, pnum, cts in successful_orders:
                rem_seconds = max(0, 1200 - (current_now - cts))
                r_min, r_sec = divmod(rem_seconds, 60)
                live_list_str += f"<b>📱 <code>+{pnum}</code></b>  ⏱️ <i>({r_min:02d}:{r_sec:02d} min)</i>\n"

            search_status_text = (
                f"<b>⚡ নম্বর সংগ্রহের কাজ চলছে ($0.11 - $0.13 Range)...</b>\n\n"
                f"<b>📊 লাইভ অগ্রগতি:</b> <code>{len(successful_orders)}</code> / <code>{qty}</code> টি সম্পূর্ণ\n"
                f"<b>⏱️ সময়:</b> <code>{time_str}</code>\n"
                f"<b>🚫 (+57319 নম্বর ফিল্টার করা হচ্ছে)</b>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{live_list_str if live_list_str else '<i>প্রসেসিং হচ্ছে... নম্বর নেওয়া হচ্ছে...</i>\n'}"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
            )

            continue_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"▶️ Continue with {len(successful_orders)} Number(s)",
                            callback_data="stop_search_continue",
                        )
                    ]
                ]
            )

            try:
                await edit_or_reply(event, search_status_text, reply_markup=continue_kb)
            except Exception:
                pass

        res = await fetch_single_number_safe(user_id, api_key)
        if res:
            oid, pnum, cts = res
            successful_orders.append((oid, pnum, cts))

            asyncio.create_task(
                poll_otp_for_order(
                    bot=event.bot if isinstance(event, Message) else event.message.bot,
                    user_id=user_id,
                    order_id=oid,
                    api_key=api_key,
                )
            )
        else:
            await asyncio.sleep(0.4)

        if elapsed_sec > 180:
            break

    active_search_sessions.pop(user_id, None)

    if not successful_orders:
        out_of_stock_text = "<b>⚠️ স্টক খালি বা ব্যালেন্স পর্যাপ্ত নেই! ১-২ মিনিট পর আবার চেষ্টা করুন।</b>"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Try Again", callback_data="buy_start")],
                [InlineKeyboardButton(text="🏠 Main Dashboard", callback_data="refresh_dash")],
            ]
        )
        await edit_or_reply(event, out_of_stock_text, reply_markup=kb)
        return

    final_text_list = []
    current_now = int(time.time())
    for oid, pnum, cts in successful_orders:
        rem_seconds = max(0, 1200 - (current_now - cts))
        r_min, r_sec = divmod(rem_seconds, 60)
        final_text_list.append(f"<b>📱 <code>+{pnum}</code></b>  ⏱️ <i>({r_min:02d}:{r_sec:02d} min left)</i>")

    result_text = (
        f"<b>✅ সকল নম্বর সফলভাবে নেওয়া হয়েছে:</b>\n\n"
        + "\n".join(final_text_list) + "\n\n"
        f"<b><i>OTP আসা মাত্রই বটের স্ক্রিনে মেসেজ চলে আসবে।</i></b>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ View Active Numbers", callback_data="view_active")],
            [InlineKeyboardButton(text="🏠 Main Dashboard", callback_data="refresh_dash")],
        ]
    )
    await edit_or_reply(event, result_text, reply_markup=kb)


# =============================================================================
# INSTANT OTP PRINT SYSTEM (UPDATED FORMAT WITH ONE-TAP COPY KEYBOARD)
# =============================================================================
async def poll_otp_for_order(bot: Bot, user_id: int, order_id: str, api_key: str):
    logger.info(f"OTP Poller active for Order: {order_id}")
    start_time_sec = time.time()
    timeout = 1200
    last_code = ""

    while (time.time() - start_time_sec) < timeout:
        try:
            order = await db.get_order_by_id(order_id)
            if not order or order["status"] in ("CANCELLED", "COMPLETED"):
                break

            status_res, code = await api_client.get_status(api_key, order_id)

            if status_res == "STATUS_OK" and code and code != last_code:
                last_code = code
                logger.info(f"[OTP RECEIVED] Order {order_id}: {code}")
                
                await db.update_order_status(order_id, "RECEIVED", code)

                # Formatted Output Specification:
                # ORDER ID : order_id (Bold)
                # NUMBER : +phone_number (Monospace)
                # OTP : code (Monospace) | TG (Bold)
                formatted_otp_message = (
                    f"<b>ORDER ID : {order_id}</b>\n\n"
                    f"<b>NUMBER :</b> <code>+{order['phone_number']}</code>\n"
                    f"<b>OTP :</b> <code>{code}</code> | <b>TG</b>"
                )

                # One-tap Copy Inline Keyboard
                otp_copy_kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Copy", copy_text={"text": code})]
                    ]
                )

                # Send direct message to Telegram chat
                await bot.send_message(
                    chat_id=user_id,
                    text=formatted_otp_message,
                    parse_mode=ParseMode.HTML,
                    reply_markup=otp_copy_kb,
                )

            elif status_res == "STATUS_CANCEL":
                await db.update_order_status(order_id, "CANCELLED")
                break

        except Exception as e:
            logger.warning(f"Error polling OTP for order {order_id}: {e}")

        await asyncio.sleep(0.5)


# =============================================================================
# ACTIVE NUMBERS & LIVE COUNTDOWN TIMER
# =============================================================================
@router.message(F.text.in_({"ACTIVE NUMBER", "⚡ Active Numbers"}))
@router.callback_query(F.data == "view_active")
async def show_active_numbers(event: Union[Message, CallbackQuery]):
    if not await check_maintenance_and_ban(event):
        return

    user_id = event.from_user.id
    active_orders = await db.get_active_orders(user_id)

    if not active_orders:
        text = "<b>⚡ ACTIVE NUMBERS</b>\n\n<b>You currently have no active orders.</b>"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Buy Colombia Telegram", callback_data="buy_start")],
                [InlineKeyboardButton(text="🏠 Dashboard", callback_data="refresh_dash")],
            ]
        )
        await edit_or_reply(event, text, reply_markup=kb)
        return

    now_ts = int(time.time())
    text = f"<b>⚡ ACTIVE NUMBERS ({len(active_orders)})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for order in active_orders:
        created_ts = int(order.get("created_timestamp") or now_ts)
        elapsed = max(0, now_ts - created_ts)
        remaining = max(0, 1200 - elapsed)
        
        e_mins, e_secs = divmod(elapsed, 60)
        r_mins, r_secs = divmod(remaining, 60)
        otp_str = f" | <b>OTP: <code>{order['sms_code']}</code> | TG</b>" if order.get('sms_code') else ""

        text += (
            f"<b>📞 <code>+{order['phone_number']}</code></b>{otp_str}\n"
            f"<b>🆔 ORDER ID: <code>{order['order_id']}</code></b>\n"
            f"<b>⏳ Held Time: {e_mins:02d}m {e_secs:02d}s | Remaining: {r_mins:02d}m {r_secs:02d}s ⏱️</b>\n"
            f"--------------------------------------\n"
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"❌ Cancel All Active Numbers ({len(active_orders)})",
                    callback_data="cancel_all_active",
                )
            ],
            [
                InlineKeyboardButton(text="🔄 Refresh Live Timers", callback_data="view_active"),
                InlineKeyboardButton(text="🏠 Main Dashboard", callback_data="refresh_dash"),
            ],
        ]
    )
    await edit_or_reply(event, text, reply_markup=kb)


@router.callback_query(F.data == "cancel_all_active")
async def cancel_all_active_numbers_handler(callback: CallbackQuery):
    if not await check_maintenance_and_ban(callback):
        return

    user_id = callback.from_user.id
    user = await db.get_user(user_id)
    api_key = user.get("api_key", "") if user else ""

    active_orders = await db.get_active_orders(user_id)
    if not active_orders:
        await callback.answer("No active numbers to cancel.", show_alert=True)
        return

    await callback.answer("Cancelling all active numbers...", show_alert=True)
    cancelled_count = 0

    for order in active_orders:
        try:
            await api_client.set_status(api_key, order["order_id"], 8)
            await db.update_order_status(order["order_id"], "CANCELLED")
            cancelled_count += 1
        except Exception as e:
            logger.warning(f"Failed to cancel order {order['order_id']}: {e}")

    await callback.message.edit_text(
        f"<b>✅ SUCCESSFULLY RETURNED {cancelled_count} NUMBER(S)!</b>\n\n"
        f"<b>All your active numbers have been cancelled and refunded.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏠 Main Dashboard", callback_data="refresh_dash")]]
        ),
    )


# =============================================================================
# PROFILE, 24H OTP STATS & DASHBOARD
# =============================================================================
@router.message(F.text == "24H OTP HISTORY")
@router.callback_query(F.data == "view_24h_otp")
async def show_24h_otp_history(event: Union[Message, CallbackQuery]):
    if not await check_maintenance_and_ban(event):
        return

    user_id = event.from_user.id
    orders_24h = await db.get_24h_otp_orders(user_id)

    if not orders_24h:
        text = "<b>📊 LAST 24 HOURS OTP HISTORY</b>\n\n<b>গত ২৪ ঘণ্টায় কোনো নম্বরে OTP রিসিভ হয়নি।</b>"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« BACK", callback_data="open_full_menu")]]
        )
        await edit_or_reply(event, text, reply_markup=kb)
        return

    text = (
        f"<b>📊 LAST 24 HOURS RECEIVED OTP LIST</b>\n"
        f"<b>🎯 Total OTP Received:</b> <code>{len(orders_24h)}</code> <b>Numbers</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    for index, o in enumerate(orders_24h, 1):
        text += (
            f"<b>{index}. 📱 <code>+{o['phone_number']}</code></b>\n"
            f"<b>🔑 OTP: <code>{o['sms_code']}</code> | TG</b>\n"
            f"<b>🆔 Order ID: <code>{o['order_id']}</code></b>\n"
            f"--------------------------------------\n"
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="« BACK", callback_data="open_full_menu")]]
    )
    await edit_or_reply(event, text, reply_markup=kb)


@router.message(F.text == "PROFILE")
@router.callback_query(F.data == "view_profile")
async def show_profile(event: Union[Message, CallbackQuery]):
    if not await check_maintenance_and_ban(event):
        return

    user_id = event.from_user.id
    user = await db.get_user(user_id) or {}
    orders = await db.get_user_orders(user_id, limit=500)

    text = (
        f"<b>👤 USER PROFILE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>👤 Name:</b> {html.escape(event.from_user.full_name)}\n"
        f"<b>🏷️ Username:</b> @{user.get('username') or 'None'}\n"
        f"<b>🆔 Telegram ID:</b> <code>{user_id}</code>\n"
        f"<b>📊 Total Orders:</b> <code>{len(orders)}</code>\n"
        f"<b>🔑 API Key:</b> <code>{mask_key(user.get('api_key', ''))}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Update API Key", callback_data="update_api_key")],
            [InlineKeyboardButton(text="« BACK", callback_data="open_full_menu")],
        ]
    )
    await edit_or_reply(event, text, reply_markup=kb)


@router.callback_query(F.data == "update_api_key")
async def prompt_api_key_update(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_for_api_key_update)
    await callback.message.edit_text(
        "<b>🔑 UPDATE SMSBOWER API KEY</b>\n\n<b>Please send your new SMSBower API key in chat:</b>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(UserStates.waiting_for_api_key_update)
async def process_api_key_update(message: Message, state: FSMContext):
    new_key = message.text.strip() if message.text else ""
    user_id = message.from_user.id

    try:
        balance = await api_client.get_balance(new_key)
        await db.set_user_api_key(user_id, new_key)
        await state.clear()
        await message.answer(
            f"<b>✅ API KEY UPDATED!</b>\n<b>Your balance:</b> <code>${balance:.2f}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await message.answer(
            f"<b>❌ INVALID API KEY!</b>\n<b>Reason:</b> {html.escape(str(e))}\n<b>Please send a valid API key:</b>",
            parse_mode=ParseMode.HTML,
        )


@router.message(F.text == "🔄 Refresh")
@router.callback_query(F.data == "refresh_dash")
async def refresh_dashboard_handler(event: Union[Message, CallbackQuery]):
    if not await check_maintenance_and_ban(event):
        return
    user_id = event.from_user.id
    await send_dashboard(event, user_id)
    if isinstance(event, CallbackQuery):
        await event.answer("Refreshed!")


@router.callback_query(F.data == "cancel_action")
async def cancel_action_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await send_dashboard(callback, callback.from_user.id)
    await callback.answer("Cancelled.")


# =============================================================================
# ADMIN PANEL
# =============================================================================
@router.message(Command("admin"))
@router.message(F.text == "🛠️ Admin Panel")
async def show_admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("<b>❌ Access Denied!</b>", parse_mode=ParseMode.HTML)
        return

    maint = await db.get_setting("maintenance", "off")
    admin_text = (
        f"<b>🛠️ ENHANCED ADMIN CONTROL PANEL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Welcome {ADMIN_NAME}!</b>\n\n"
        f"<b>⚙️ Maintenance Mode:</b> <code>{maint.upper()}</code>\n"
        f"<b>Select an action below:</b>"
    )
    await message.answer(
        admin_text, parse_mode=ParseMode.HTML, reply_markup=get_admin_keyboard(maint)
    )


@router.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    stats = await db.get_stats()
    maint = await db.get_setting("maintenance", "off")
    text = (
        f"<b>📊 BOT SYSTEM STATISTICS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>👥 Total Users:</b> <code>{stats['total_users']}</code>\n"
        f"<b>🚫 Banned Users:</b> <code>{stats['banned_users']}</code>\n"
        f"<b>🛒 Total Orders:</b> <code>{stats['total_orders']}</code>\n"
        f"<b>⚡ Active Orders:</b> <code>{stats['active_orders']}</code>\n"
        f"<b>✅ Completed Orders:</b> <code>{stats['completed_orders']}</code>\n"
        f"<b>❌ Cancelled Orders:</b> <code>{stats['cancelled_orders']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    await callback.message.edit_text(
        text, parse_mode=ParseMode.HTML, reply_markup=get_admin_keyboard(maint)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_search_user")
async def admin_search_user_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return

    await state.set_state(AdminStates.waiting_for_user_search)
    await callback.message.edit_text(
        "<b>🔍 SEARCH USER</b>\n\n<b>Enter the Telegram User ID to inspect:</b>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_user_search)
async def process_admin_user_search(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        target_id = int(message.text.strip())
        target_user = await db.get_user(target_id)
        if not target_user:
            await message.answer("<b>❌ User not found.</b>", parse_mode=ParseMode.HTML)
            return

        orders = await db.get_user_orders(target_id, limit=500)
        api_key = target_user.get("api_key", "Not Set")
        maint = await db.get_setting("maintenance", "off")

        info_text = (
            f"<b>👤 USER DETAILS (ADMIN)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>🆔 Telegram ID:</b> <code>{target_id}</code>\n"
            f"<b>👤 Name:</b> {html.escape(target_user.get('full_name', ''))}\n"
            f"<b>🏷️ Username:</b> @{target_user.get('username') or 'None'}\n"
            f"<b>🔑 API Key:</b> <code>{api_key}</code>\n"
            f"<b>🚫 Banned:</b> {'YES' if target_user.get('is_banned') else 'NO'}\n"
            f"<b>📊 Total Orders:</b> <code>{len(orders)}</code>"
        )
        await state.clear()
        await message.answer(info_text, parse_mode=ParseMode.HTML, reply_markup=get_admin_keyboard(maint))

    except ValueError:
        await message.answer("<b>❌ Invalid User ID. Enter numbers only:</b>", parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return

    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.message.edit_text(
        "<b>📢 ADMIN BROADCAST</b>\n\n<b>Send the broadcast message to all users:</b>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_broadcast)
async def process_admin_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    user_ids = await db.get_all_user_ids()
    await message.answer(f"<b>🚀 Broadcasting to {len(user_ids)} users...</b>", parse_mode=ParseMode.HTML)

    success, failed = 0, 0
    for uid in user_ids:
        try:
            await message.copy_to(chat_id=uid)
            success += 1
            await asyncio.sleep(0.02)
        except Exception:
            failed += 1

    await state.clear()
    await message.answer(
        f"<b>✅ BROADCAST COMPLETED</b>\n\n<b>🟢 Success:</b> <code>{success}</code> | <b>🔴 Failed:</b> <code>{failed}</code>",
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "admin_ban")
async def admin_ban_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return

    await state.set_state(AdminStates.waiting_for_ban_id)
    await callback.message.edit_text("<b>🚫 Enter User ID to ban:</b>", parse_mode=ParseMode.HTML)
    await callback.answer()


@router.message(AdminStates.waiting_for_ban_id)
async def process_admin_ban(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        target_id = int(message.text.strip())
        await db.set_user_ban(target_id, 1)
        await state.clear()
        await message.answer(f"<b>✅ User <code>{target_id}</code> banned.</b>", parse_mode=ParseMode.HTML)
    except ValueError:
        await message.answer("<b>❌ Invalid User ID.</b>", parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "admin_unban")
async def admin_unban_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return

    await state.set_state(AdminStates.waiting_for_unban_id)
    await callback.message.edit_text("<b>✅ Enter User ID to unban:</b>", parse_mode=ParseMode.HTML)
    await callback.answer()


@router.message(AdminStates.waiting_for_unban_id)
async def process_admin_unban(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        target_id = int(message.text.strip())
        await db.set_user_ban(target_id, 0)
        await state.clear()
        await message.answer(f"<b>✅ User <code>{target_id}</code> unbanned.</b>", parse_mode=ParseMode.HTML)
    except ValueError:
        await message.answer("<b>❌ Invalid User ID.</b>", parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "admin_maint")
async def toggle_maintenance(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    current = await db.get_setting("maintenance", "off")
    new_mode = "on" if current == "off" else "off"
    await db.set_setting("maintenance", new_mode)

    await callback.answer(f"Maintenance Mode: {new_mode.upper()}", show_alert=True)
    await callback.message.edit_text(
        f"<b>⚙️ Maintenance Mode:</b> <code>{new_mode.upper()}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_keyboard(new_mode),
    )


@router.callback_query(F.data == "admin_db_summary")
async def admin_db_summary(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    stats = await db.get_stats()
    maint = await db.get_setting("maintenance", "off")
    text = (
        f"<b>📋 DATABASE SUMMARY</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>• DB File:</b> <code>{DB_FILE}</code>\n"
        f"<b>• Country:</b> <code>{TARGET_COUNTRY_NAME} ({TARGET_COUNTRY_ID})</code>\n"
        f"<b>• Service:</b> <code>{TARGET_SERVICE_NAME} ({TARGET_SERVICE_CODE})</code>\n"
        f"<b>• Total Users:</b> <code>{stats['total_users']}</code>\n"
        f"<b>• Total Orders:</b> <code>{stats['total_orders']}</code>"
    )
    await callback.message.edit_text(
        text, parse_mode=ParseMode.HTML, reply_markup=get_admin_keyboard(maint)
    )
    await callback.answer()


# =============================================================================
# HELPER UTILS & KEEP ALIVE SERVER
# =============================================================================
def mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "Not Configured"
    return f"{key[:4]}...{key[-4:]}"


flask_app = Flask(__name__)


@flask_app.route("/")
@flask_app.route("/health")
def health_check():
    return "Bot Online", 200


def run_flask_server():
    port = int(os.environ.get("PORT", 8080))
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    flask_app.run(host="0.0.0.0", port=port)


def start_keep_alive():
    t = threading.Thread(target=run_flask_server)
    t.daemon = True
    t.start()


# =============================================================================
# MAIN RUNNER
# =============================================================================
async def main():
    start_keep_alive()
    logger.info("Initializing Bot...")
    await db.init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info(f"Bot Active as @{(await bot.get_me()).username}")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot Stopped.")
