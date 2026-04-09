## Intelligent Local News Summarization and Alerting Bot on Telegram (Using Local LLM)

### 1. Project Overview

**Project Title**: Intelligent Local News Summarization and Alerting Bot on Telegram Using Local LLM  
**Short Description**:  
A backend-driven automated system that scrapes local news from defined local sources, processes it using a locally hosted LLM to generate short summaries, and pushes scheduled updates directly to users via a Telegram bot. It aims to bridge the information gap for younger generations and busy professionals by delivering relevant community news without information overload.

### 2. Objectives

- **Deliver concise local news**: Provide users with short, readable summaries of local news, alerts, and community updates.  
- **Centralize fragmented sources**: Aggregate information from multiple local sources (RSS feeds, websites) into a single Telegram channel/bot.  
- **Support community awareness (SDG 11)**: Increase engagement and awareness around safety, infrastructure, and local events in Sarawak.  
- **Preserve privacy and reduce cost**: Use a locally hosted LLM (Ollama / Llama 3.1) instead of paid third‑party AI APIs.

---

## System Overview & Architecture

### 3. Current Problems & Motivation

- **Fragmented Sources**: Local news is often trapped in closed social platforms (e.g. Facebook, WhatsApp), inaccessible to those without accounts or group membership.  
- **Information Overload**: Mainstream apps prioritize national/global headlines and often ignore hyper-local information such as utility cuts, minor accidents, or neighborhood events.  
- **Inefficient Consumption**: Local reports are long, unstructured, and mixed with noise or spam, making it hard for busy users to quickly extract key points.

### 4. Value Brought by the Project

- **Enhanced Community Awareness**: Curated local updates delivered in a single Telegram chat, aligned with SDG 11 (Sustainable Cities and Communities).  
- **Time Efficiency**: AI-generated summaries (~30 words or bullet points) allow users to scan multiple stories quickly.  
- **Privacy & Cost-Effectiveness**: Processing is done on a local LLM instance, minimizing external dependencies and recurring costs.

### 5. Target Users & Scope

- **Target Users**:  
  - Young adults and students.  
  - Busy working professionals.  
  - Local community residents in Sarawak.

- **Platform**:  
  - Telegram Messaging App (Telegram bot).

- **Technologies**:  
  - **Bot & Integration**: Telegram Bot API (configured via BotFather).  
  - **Backend**: Python.  
  - **AI**: Ollama running Llama 3.1 (local LLM) for summarization and simple classification.  
  - **Data Sources**: RSS feeds and web scrapers for local news websites and other public sources.

- **Limitations**:  
  - The bot backend and Ollama instance run on a host machine (e.g. laptop) that must stay online to process and push messages.  
  - No dedicated graphical user interface (GUI); users interact entirely through Telegram.

---

## Software Architecture & Project Structure

### 6. High-Level Architecture

- **Data Ingestion Layer**  
  - Fetches content from RSS feeds and scrapes HTML from configured local news sites.  
  - Normalizes all items into a common `NewsItem` format.

- **AI Processing Layer**  
  - Sends article body text to the local LLM (Ollama / Llama 3.1).  
  - Generates concise summaries (brief text or bullet points).  
  - Optionally classifies items into categories (e.g. safety, event, traffic) and urgency levels.

- **Core Business Logic**  
  - Filters and deduplicates news items.  
  - Applies user preferences (categories, locations, delivery frequency).  
  - Decides whether an item should be part of a digest or sent as an urgent alert.

- **Notification & Delivery Layer**  
  - Telegram bot entrypoint and command handlers.  
  - Schedules periodic digests and pushes urgent notifications instantly.  
  - Formats content into user-friendly Telegram messages.

- **Storage Layer**  
  - Persists users, preferences, cached news items, summaries, and delivery logs.  
  - Likely implemented with SQLite during development; can be upgraded to another DB later.

### 7. Suggested Project Structure

```text
MVP/
  README.md
  requirements.txt
  src/
    bot/
      bot_main.py          # Telegram bot entrypoint (polling or webhook)
      handlers.py          # /start, /help, /settings, /latest, etc.
      message_formatter.py # Utility for nicely formatted Telegram messages

    scrapers/
      rss_reader.py        # Generic RSS reader
      site_scraper_x.py    # Custom scraper for local source X
      site_scraper_y.py    # Custom scraper for local source Y
      normalizer.py        # Convert raw content to NewsItem objects

    ai/
      summarizer.py        # Interface to Ollama (Llama 3.1) for summarization
      classifier.py        # Optional: urgency/category classification

    core/
      models.py            # Domain models: User, UserPreference, NewsItem, etc.
      services.py          # Main orchestration / business logic
      scheduler.py         # Periodic jobs (scraping, digest sending)
      config.py            # Central configuration and constants

    storage/
      database.py          # DB connection & helpers (e.g. SQLite)
      migrations/          # Schema definitions or migration scripts

    tests/
      test_scrapers.py
      test_summarizer.py
      test_handlers.py
```

You can adjust folder names as the project grows, but this structure separates concerns clearly (bot, scraping, AI, core logic, storage, tests).

---

## Software Requirements Specification

### 8. Functional Requirements

- **FR1 – User Registration & Onboarding**  
  - The system shall allow users to start interaction with `/start`.  
  - The bot shall send an introduction and list available commands.

- **FR2 – News Source Aggregation**  
  - The system shall periodically fetch news from configured RSS feeds.  
  - The system shall scrape raw HTML from configured public local news sites that lack RSS.  
  - The system shall store fetched items in a normalized internal format.

- **FR3 – Summarization**  
  - The system shall generate short summaries (~30 words) or bullet points for each news article using a local LLM.  
  - The system shall handle summarization errors gracefully (logging, retrying, or falling back to trimmed text).

- **FR4 – Categorization & Tagging**  
  - The system shall assign categories (e.g. traffic, weather, events, government notices) to news items based on keywords or LLM classification.  
  - The system shall attempt to infer location tags (district, area) where possible.

- **FR5 – Urgency Detection & Alerts**  
  - The system shall detect urgent items (e.g. floods, accidents, road closures) via keyword rules and/or LLM prompts.  
  - The system shall send immediate push notifications for urgent items to users who opt in for urgent alerts.

- **FR6 – Personalization & Filters**  
  - The system shall let users configure preferences (categories, locations, delivery frequency) through commands or menu interactions.  
  - The system shall only send news that matches a user’s preferences.

- **FR7 – Scheduled Delivery**  
  - The system shall support scheduled digests (e.g. morning/evening) summarizing the latest news in one message.  
  - The system shall avoid sending duplicate news items to the same user by checking delivery history.

- **FR8 – Bot Commands**  
  - `/start` – Begin using the bot, register user if new.  
  - `/help` – Show explanation of the bot’s features and commands.  
  - `/settings` – Configure categories, locations, frequency, and subscription ON/OFF.  
  - `/latest` – Request the latest summarized news immediately.

- **FR9 – Admin & Configuration**  
  - The system shall allow the maintainer to add or remove sources via configuration files.  
  - The system shall allow tuning summarization length, urgency thresholds, and schedule intervals.

### 9. Non-Functional Requirements

- **NFR1 – Performance**  
  - Summarization of a single article should typically complete within a few seconds.  
  - Scheduled digests should be generated and delivered within a defined time window (e.g. within 5 minutes after schedule).

- **NFR2 – Reliability**  
  - The system shall be resilient to temporary network or source failures (e.g. retry later, skip temporarily).  
  - All errors (scraping, LLM, Telegram API) shall be logged for debugging.

- **NFR3 – Scalability**  
  - The design should support dozens to hundreds of users on a single machine initially.  
  - Architecture should not tightly couple to one machine so it can be moved to a server later.

- **NFR4 – Security & Privacy**  
  - User data (chat IDs, preferences) shall be stored securely and never shared with third parties.  
  - All AI processing shall occur on the local LLM; no article content should be sent to external AI APIs.  
  - Secrets such as Telegram bot token and DB credentials shall be stored in environment variables or a non-committed config file.

- **NFR5 – Usability**  
  - Bot responses shall be concise, well formatted, and avoid overwhelming users with text.  
  - Commands should be simple and documented so that non-technical users can operate the bot easily.

- **NFR6 – Maintainability**  
  - Code shall be modular with clear separation of concerns (bot, scrapers, AI, business logic, storage).  
  - Key modules should include docstrings and comments.  
  - Unit tests shall cover critical features (scraping, summarization interface, urgency detection).

- **NFR7 – Portability**  
  - The system shall run on Windows (developer laptop) and be portable to Linux-based servers.  
  - Optionally, Docker can be used to containerize the backend and Ollama for deployment.

---

## Data Design

### 10. Conceptual Data Models

- **User**  
  - `telegram_id`, `username`, `first_seen_at`, `is_active`.

- **UserPreference**  
  - `user_id`, `categories`, `locations`, `frequency`, `wants_urgent_alerts`.

- **NewsItem**  
  - `id`, `source`, `title`, `url`, `raw_content`, `published_at`, `category`, `location`, `urgency_level`.

- **Summary**  
  - `news_id`, `summary_text`, `summary_type` (brief or bullets), `created_at`.

- **DeliveryLog**  
  - `user_id`, `news_id`, `delivered_at`, `delivery_type` (digest or urgent).

---

## Development & Setup (Draft)

### 11. Prerequisites

- **Software**:  
  - Python 3.10+  
  - Git  
  - Telegram account to create a bot via BotFather  
  - Ollama installed locally, with the Llama 3.1 model pulled

### 12. Basic Setup Steps

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd MVP
   ```

2. **Create and activate a virtual environment**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # On Windows
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Pull the Ollama model**
   ```bash
   ollama pull llama3.1
   ```

5. **Configure environment variables**
   - `TELEGRAM_BOT_TOKEN` – token from BotFather.  
   - `DATABASE_URL` – e.g. `sqlite:///mvp.db`.  
   - `DB_CLEANUP_ENABLED` – enable automatic old-news cleanup (`true`/`false`, default `true`).  
   - `DB_RETENTION_DAYS` – keep news for this many days (default `30`).  
   - `DB_CLEANUP_INTERVAL_HOURS` – cleanup job interval (default `24`).  
   - Any additional config related to Ollama (e.g. host/port if not default).

6. **Run the bot (development mode)**
   ```bash
   python -m src.bot.bot_main
   ```

### 13. Future Improvements

- Add web-based dashboard for admin monitoring and configuration.  
- Enhance NLP classification (e.g. topic modeling, sentiment).  
- Add support for multiple languages (e.g. English, Malay).  
- Deploy backend and local LLM on a dedicated server or cloud VM for 24/7 operation.

---

## Related / Similar Systems (Context)

- **Google News**: Strong global/national coverage; weak on hyper-local community updates.  
- **Facebook Community Groups**: Rich in local info but noisy, unstructured, and requires a Facebook account.  
- **Official WhatsApp Channels**: Deliver direct updates but users must subscribe to many channels individually.  
- **Standard Telegram RSS Bots**: Forward raw RSS items without summarization, personalization, or urgency prioritization.

This project aims to combine the strengths of these systems—aggregation and timeliness—with AI-powered summarization and personalization focused on local communities.

