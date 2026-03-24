from __future__ import annotations

from typing import Final

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from src.core.user_service import (
    get_or_create_user,
    get_user_preference,
    update_user_preference,
)


WELCOME_TEXT: Final[str] = (
    "Hello! 👋\n\n"
    "I am the *Local News Summarization Bot*.\n"
    "I fetch and summarize local Sarawak news for you.\n\n"
    "*Available commands:*\n"
    "• /start – show this welcome message\n"
    "• /help – show available commands\n"
    "• /latest – get the latest news with AI summaries\n"
    "• /settings – configure your preferences (categories, frequency)\n\n"
    "Use /settings to customize which news you want to see!"
)


HELP_TEXT: Final[str] = (
    "*Available commands:*\n"
    "• /start – introduction and project description\n"
    "• /help – this help message\n"
    "• /latest – get the latest news with AI summaries\n"
    "• /settings – configure your preferences\n\n"
    "Use /settings to choose categories (e.g., Sarawak-only, sports, politics) "
    "and delivery frequency (every 1h/3h/6h/12h)."
)

def _format_frequency(value: str | None) -> str:
    mapping = {
        "every_15m": "Every 15 mins",
        "every_30m": "Every 30 mins",
        "every_1h": "Every 1 hour",
        "every_3h": "Every 3 hours",
        "every_6h": "Every 6 hours",
        "every_12h": "Every 12 hours",
        # Backward compatibility for older stored values:
        "instant": "Every 1 hour",
        "daily": "Every 12 hours",
    }
    key = (value or "").strip().lower()
    return mapping.get(key, "Every 1 hour")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command. Register user if new."""
    if not update.message:
        return

    telegram_id = update.message.from_user.id
    username = update.message.from_user.username

    # Register or get user
    get_or_create_user(telegram_id, username)

    await update.message.reply_text(WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def latest_demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler for /latest.
    Fetches real headlines from configured RSS feeds with AI summaries,
    filtered by user preferences if set.
    """
    if not update.message:
        return

    telegram_id = update.message.from_user.id
    # Register user if new
    get_or_create_user(telegram_id, update.message.from_user.username)

    # Local import to avoid circular dependency at import time
    from src.core.services import get_latest_news_text_for_user

    text = get_latest_news_text_for_user(telegram_id)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def setareas_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /setareas <comma separated keywords>
    Example:
      /setareas Jalan Wawasan, Taman Desa, Kampung Tabuan

    Stores free-text area keywords used to match more specific local notices
    (e.g., water supply interruption affecting a particular road/area).
    """
    if not update.message:
        return

    telegram_id = update.message.from_user.id
    get_or_create_user(telegram_id, update.message.from_user.username)

    raw = update.message.text or ""
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "Usage:\n"
            "`/setareas Jalan Wawasan, Taman Desa`\n\n"
            "Tip: use commas to add multiple keywords.\n"
            "Send `/setareas` with an empty value to clear.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    value = parts[1].strip()
    if value.lower() in {"clear", "none", "off"}:
        value = ""

    # Normalize: store as comma-separated, lowercased for matching (display can be title-cased later)
    keywords = [k.strip() for k in value.split(",") if k.strip()]
    normalized = ",".join([k.lower() for k in keywords])

    update_user_preference(telegram_id, area_keywords=normalized)

    if normalized:
        display = ", ".join([k.strip() for k in value.split(",") if k.strip()])
        await update.message.reply_text(
            f"✅ Area keywords updated:\n{display}\n\nNow /latest will prioritize news that mentions these areas.",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text(
            "✅ Area keywords cleared. You will no longer filter by specific roads/areas.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings command. Show current preferences and options."""
    if not update.message:
        return

    telegram_id = update.message.from_user.id
    get_or_create_user(telegram_id, update.message.from_user.username)

    preference = get_user_preference(telegram_id)

    if not preference:
        await update.message.reply_text(
            "Error: Could not load your preferences. Please try again.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Format current settings
    categories_display = preference.categories if preference.categories else "All categories"
    
    # Format locations nicely
    if preference.locations:
        location_list = [loc.strip().title() for loc in preference.locations.split(",") if loc.strip()]
        locations_display = ", ".join(location_list) if location_list else "All Sarawak"
    else:
        locations_display = "All Sarawak"
    
    frequency_display = _format_frequency(preference.frequency)
    urgent_display = "Yes" if preference.wants_urgent_alerts else "No"
    area_keywords_display = (
        ", ".join([k.strip().title() for k in (preference.area_keywords or "").split(",") if k.strip()])
        if (preference.area_keywords or "").strip()
        else "None"
    )

    settings_text = (
        "*Your Current Settings:*\n\n"
        f"📂 *Categories:* {categories_display}\n"
        f"📍 *Locations:* {locations_display}\n"
        f"🗺️ *Area Keywords:* {area_keywords_display}\n"
        f"⏰ *Frequency:* {frequency_display}\n"
        f"🚨 *Urgent Alerts:* {urgent_display}\n\n"
        "Use the buttons below to change your preferences:"
    )

    # Create inline keyboard
    keyboard = [
        [
            InlineKeyboardButton("📂 Categories", callback_data="settings_categories"),
            InlineKeyboardButton("📍 Locations", callback_data="settings_locations"),
        ],
        [
            InlineKeyboardButton("🗺️ Area Keywords", callback_data="settings_area_keywords"),
        ],
        [
            InlineKeyboardButton("⏰ Frequency", callback_data="settings_frequency"),
        ],
        [
            InlineKeyboardButton(
                "🚨 Urgent Alerts: " + ("ON" if preference.wants_urgent_alerts else "OFF"),
                callback_data="settings_toggle_urgent",
            ),
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        settings_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from settings inline buttons."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()

    telegram_id = query.from_user.id
    preference = get_user_preference(telegram_id)

    if not preference:
        await query.edit_message_text("Error: Could not load your preferences.")
        return

    data = query.data

    if data == "settings_categories":
        # Show category selection
        keyboard = [
            [
                InlineKeyboardButton("All Categories", callback_data="cat_all"),
                InlineKeyboardButton("Sarawak Only", callback_data="cat_sarawak"),
            ],
            [
                InlineKeyboardButton("Sports", callback_data="cat_sports"),
                InlineKeyboardButton("Politics", callback_data="cat_politics"),
            ],
            [
                InlineKeyboardButton("Custom (comma-separated)", callback_data="cat_custom"),
            ],
            [InlineKeyboardButton("◀️ Back", callback_data="settings_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "*Select Categories:*\n\n"
            "Choose which news categories you want to see. "
            "You can select multiple by choosing 'Custom'.\n\n"
            "Current: " + (preference.categories if preference.categories else "All"),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

    elif data == "settings_area_keywords":
        current = (
            ", ".join([k.strip().title() for k in (preference.area_keywords or "").split(",") if k.strip()])
            if (preference.area_keywords or "").strip()
            else "None"
        )
        await query.edit_message_text(
            "*Area Keywords (specific roads/areas):*\n\n"
            "This is for very specific matching like:\n"
            "- Jalan Wawasan\n"
            "- Taman Desa\n"
            "- Kampung Tabuan\n\n"
            "Set it using:\n"
            "`/setareas Jalan Wawasan, Taman Desa`\n\n"
            f"Current: {current}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="settings_back")]]),
        )
        return

    elif data == "settings_locations":

        # Show location selection with checkmarks
        current_locations = [loc.strip().lower() for loc in (preference.locations or "").split(",") if loc.strip()]
        
        def is_selected(loc_key: str) -> str:
            location_map = {
                "loc_kuching": "kuching",
                "loc_kota_samarahan": "kota samarahan",
                "loc_miri": "miri",
                "loc_sibu": "sibu",
                "loc_bintulu": "bintulu",
                "loc_serian": "serian",
                "loc_sarikei": "sarikei",
            }
            return " ✓" if location_map.get(loc_key, "").lower() in current_locations else ""
        
        keyboard = [
            [
                InlineKeyboardButton(
                    "All Sarawak" + (" ✓" if not current_locations else ""),
                    callback_data="loc_all",
                ),
            ],
            [
                InlineKeyboardButton("Kuching" + is_selected("loc_kuching"), callback_data="loc_kuching"),
                InlineKeyboardButton("Kota Samarahan" + is_selected("loc_kota_samarahan"), callback_data="loc_kota_samarahan"),
            ],
            [
                InlineKeyboardButton("Miri" + is_selected("loc_miri"), callback_data="loc_miri"),
                InlineKeyboardButton("Sibu" + is_selected("loc_sibu"), callback_data="loc_sibu"),
            ],
            [
                InlineKeyboardButton("Bintulu" + is_selected("loc_bintulu"), callback_data="loc_bintulu"),
                InlineKeyboardButton("Serian" + is_selected("loc_serian"), callback_data="loc_serian"),
            ],
            [
                InlineKeyboardButton("Sarikei" + is_selected("loc_sarikei"), callback_data="loc_sarikei"),
                InlineKeyboardButton("More Cities...", callback_data="loc_more"),
            ],
            [InlineKeyboardButton("◀️ Back", callback_data="settings_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        current_display = ", ".join([loc.title() for loc in current_locations]) if current_locations else "All Sarawak"
        await query.edit_message_text(
            "*Select Locations:*\n\n"
            "Choose which Sarawak cities you want news from. "
            "You can select multiple cities.\n\n"
            f"Current: {current_display}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

    elif data == "loc_more":
        # Show more cities with checkmarks
        current_locations = [loc.strip().lower() for loc in (preference.locations or "").split(",") if loc.strip()]
        
        def is_selected_more(loc_key: str) -> str:
            location_map = {
                "loc_sri_aman": "sri aman",
                "loc_mukah": "mukah",
                "loc_limbang": "limbang",
                "loc_lawas": "lawas",
                "loc_betong": "betong",
                "loc_saratok": "saratok",
                "loc_kapit": "kapit",
                "loc_marudi": "marudi",
                "loc_belaga": "belaga",
            }
            return " ✓" if location_map.get(loc_key, "").lower() in current_locations else ""
        
        keyboard = [
            [
                InlineKeyboardButton("Sri Aman" + is_selected_more("loc_sri_aman"), callback_data="loc_sri_aman"),
                InlineKeyboardButton("Mukah" + is_selected_more("loc_mukah"), callback_data="loc_mukah"),
            ],
            [
                InlineKeyboardButton("Limbang" + is_selected_more("loc_limbang"), callback_data="loc_limbang"),
                InlineKeyboardButton("Lawas" + is_selected_more("loc_lawas"), callback_data="loc_lawas"),
            ],
            [
                InlineKeyboardButton("Betong" + is_selected_more("loc_betong"), callback_data="loc_betong"),
                InlineKeyboardButton("Saratok" + is_selected_more("loc_saratok"), callback_data="loc_saratok"),
            ],
            [
                InlineKeyboardButton("Kapit" + is_selected_more("loc_kapit"), callback_data="loc_kapit"),
                InlineKeyboardButton("Marudi" + is_selected_more("loc_marudi"), callback_data="loc_marudi"),
            ],
            [
                InlineKeyboardButton("Belaga" + is_selected_more("loc_belaga"), callback_data="loc_belaga"),
            ],
            [InlineKeyboardButton("◀️ Back to Locations", callback_data="settings_locations")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        current_display = ", ".join([loc.title() for loc in current_locations]) if current_locations else "All Sarawak"
        await query.edit_message_text(
            "*More Sarawak Cities:*\n\n"
            "Select additional cities for news filtering.\n\n"
            f"Current: {current_display}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

    elif data.startswith("loc_"):
        # Location selection
        location_map = {
            "loc_all": "",
            "loc_kuching": "kuching",
            "loc_kota_samarahan": "kota samarahan",
            "loc_miri": "miri",
            "loc_sibu": "sibu",
            "loc_bintulu": "bintulu",
            "loc_serian": "serian",
            "loc_sarikei": "sarikei",
            "loc_sri_aman": "sri aman",
            "loc_mukah": "mukah",
            "loc_limbang": "limbang",
            "loc_lawas": "lawas",
            "loc_betong": "betong",
            "loc_saratok": "saratok",
            "loc_kapit": "kapit",
            "loc_marudi": "marudi",
            "loc_belaga": "belaga",
        }

        if data == "loc_all":
            update_user_preference(telegram_id, locations="")
            await query.answer("Locations set to: All Sarawak")
        else:
            selected_location = location_map.get(data, "")
            if selected_location:
                # Get current locations and add/remove the selected one
                current_locations = preference.locations or ""
                location_list = [loc.strip().lower() for loc in current_locations.split(",") if loc.strip()]

                if selected_location.lower() in location_list:
                    # Remove if already selected
                    location_list.remove(selected_location.lower())
                    await query.answer(f"{selected_location.title()} removed")
                else:
                    # Add if not selected
                    location_list.append(selected_location.lower())
                    await query.answer(f"{selected_location.title()} added")

                new_locations = ",".join(sorted(location_list))
                update_user_preference(telegram_id, locations=new_locations)

        await settings_callback_refresh(query, telegram_id)

    elif data == "settings_frequency":
        # Show frequency selection
        current_freq = (preference.frequency or "").strip().lower()
        if current_freq == "instant":
            current_freq = "every_1h"
        elif current_freq == "daily":
            current_freq = "every_12h"

        keyboard = [
            [
                InlineKeyboardButton(
                    "⏱️ Every 15 mins" + (" ✓" if current_freq == "every_15m" else ""),
                    callback_data="freq_every_15m",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏱️ Every 30 mins" + (" ✓" if current_freq == "every_30m" else ""),
                    callback_data="freq_every_30m",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏱️ Every 1 hour" + (" ✓" if current_freq == "every_1h" else ""),
                    callback_data="freq_every_1h",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏱️ Every 3 hours" + (" ✓" if current_freq == "every_3h" else ""),
                    callback_data="freq_every_3h",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏱️ Every 6 hours" + (" ✓" if current_freq == "every_6h" else ""),
                    callback_data="freq_every_6h",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏱️ Every 12 hours" + (" ✓" if current_freq == "every_12h" else ""),
                    callback_data="freq_every_12h",
                ),
            ],
            [InlineKeyboardButton("◀️ Back", callback_data="settings_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "*Select Frequency:*\n\n"
            "Choose how often you want push notifications while the bot is running.\n\n"
            f"Current: {_format_frequency(preference.frequency)}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )

    elif data == "settings_toggle_urgent":
        # Toggle urgent alerts
        new_value = not preference.wants_urgent_alerts
        update_user_preference(telegram_id, wants_urgent_alerts=new_value)
        await query.answer(f"Urgent alerts {'enabled' if new_value else 'disabled'}!")
        # Refresh settings view
        await settings_callback_refresh(query, telegram_id)

    elif data.startswith("cat_"):
        # Category selection
        if data == "cat_all":
            update_user_preference(telegram_id, categories="")
            await query.answer("Categories set to: All")
        elif data == "cat_sarawak":
            update_user_preference(telegram_id, categories="sarawak")
            await query.answer("Categories set to: Sarawak only")
        elif data == "cat_sports":
            update_user_preference(telegram_id, categories="sports")
            await query.answer("Categories set to: Sports")
        elif data == "cat_politics":
            update_user_preference(telegram_id, categories="politics")
            await query.answer("Categories set to: Politics")
        elif data == "cat_custom":
            await query.edit_message_text(
                "To set custom categories, send me a message like:\n"
                "`/setcategories sarawak,sports,politics`\n\n"
                "Or use single words separated by commas.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        await settings_callback_refresh(query, telegram_id)

    elif data.startswith("freq_"):
        # Frequency selection
        if data == "freq_every_15m":
            update_user_preference(telegram_id, frequency="every_15m")
            await query.answer("Frequency set to: Every 15 mins")
        elif data == "freq_every_30m":
            update_user_preference(telegram_id, frequency="every_30m")
            await query.answer("Frequency set to: Every 30 mins")
        elif data == "freq_every_1h":
            update_user_preference(telegram_id, frequency="every_1h")
            await query.answer("Frequency set to: Every 1 hour")
        elif data == "freq_every_3h":
            update_user_preference(telegram_id, frequency="every_3h")
            await query.answer("Frequency set to: Every 3 hours")
        elif data == "freq_every_6h":
            update_user_preference(telegram_id, frequency="every_6h")
            await query.answer("Frequency set to: Every 6 hours")
        elif data == "freq_every_12h":
            update_user_preference(telegram_id, frequency="every_12h")
            await query.answer("Frequency set to: Every 12 hours")
        await settings_callback_refresh(query, telegram_id)

    elif data == "settings_back":
        # Return to main settings
        await settings_callback_refresh(query, telegram_id)


async def settings_callback_refresh(query, telegram_id: int) -> None:
    """Helper to refresh the settings view after a change."""
    preference = get_user_preference(telegram_id)
    if not preference:
        return

    categories_display = preference.categories if preference.categories else "All categories"
    
    # Format locations nicely
    if preference.locations:
        location_list = [loc.strip().title() for loc in preference.locations.split(",") if loc.strip()]
        locations_display = ", ".join(location_list) if location_list else "All Sarawak"
    else:
        locations_display = "All Sarawak"
    
    frequency_display = _format_frequency(preference.frequency)
    urgent_display = "Yes" if preference.wants_urgent_alerts else "No"
    area_keywords_display = (
        ", ".join([k.strip().title() for k in (preference.area_keywords or "").split(",") if k.strip()])
        if (preference.area_keywords or "").strip()
        else "None"
    )

    settings_text = (
        "*Your Current Settings:*\n\n"
        f"📂 *Categories:* {categories_display}\n"
        f"📍 *Locations:* {locations_display}\n"
        f"🗺️ *Area Keywords:* {area_keywords_display}\n"
        f"⏰ *Frequency:* {frequency_display}\n"
        f"🚨 *Urgent Alerts:* {urgent_display}\n\n"
        "Use the buttons below to change your preferences:"
    )

    keyboard = [
        [
            InlineKeyboardButton("📂 Categories", callback_data="settings_categories"),
            InlineKeyboardButton("📍 Locations", callback_data="settings_locations"),
        ],
        [
            InlineKeyboardButton("🗺️ Area Keywords", callback_data="settings_area_keywords"),
        ],
        [
            InlineKeyboardButton("⏰ Frequency", callback_data="settings_frequency"),
        ],
        [
            InlineKeyboardButton(
                "🚨 Urgent Alerts: " + ("ON" if preference.wants_urgent_alerts else "OFF"),
                callback_data="settings_toggle_urgent",
            ),
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        settings_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
    )
