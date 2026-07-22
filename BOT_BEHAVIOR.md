# Discord Prayer Bot — Detailed Behavior Specification

This document provides a comprehensive, plain-English description of how the Discord Prayer Bot is designed to behave. It covers scheduling, audio interactions, administrative controls, and system reliability.

---

## 1. Scheduled Prayer Life-Cycle

The bot follows a strict timeline for every scheduled prayer to ensure a respectful and welcoming environment.

### 🕒 Phase 1: Pre-Join & Greeting (T-10 minutes)
*   **Entrance**: Exactly 10 minutes before a scheduled prayer (or immediately upon bot startup if a prayer is starting in less than 10 minutes), the bot joins the configured voice channel.
*   **Connection Buffer**: Upon joining, the bot waits for **5 seconds**. This allows the Discord backend and the users' local clients to fully establish the audio stream.
*   **Welcome Greeting**: 
    *   If users are already in the channel when the bot joins, it says: *"Welcome [Names], thank you for joining."*
    *   As new users join during this 10-minute window, the bot greets them individually: *"Welcome [Name], thank you for coming, we will start the prayer in [X] minutes."*
    *   **Greeting Queue**: If multiple people join at once, the bot puts their greetings in a sequential queue so they play one after another without overlapping or cutting each other off.

### 🛐 Phase 2: Recitation (T-0 minutes)
*   **Exact Start**: At the exact scheduled minute, the bot begins playing the selected prayer audio (e.g., Buddhist, Christian, etc.).
*   **Silence Guard**: The bot will not perform any greetings while the prayer audio is playing to maintain the sanctity of the recitation.
*   **Status Update**: The bot updates its "Voice Status" text next to the channel name to: *"Prayer in progress"*.

### 🌅 Phase 3: Post-Prayer Blessing & Exit (T+Finish)
*   **The Blessing**: Immediately after the prayer audio finishes, the bot identifies everyone still present in the channel and says: *"Thank you [Name A] and [Name B] for joining, god bless you."*
*   **Graceful Exit**: After the blessing, the bot starts a **5-minute countdown**.
*   **Departure**: Once the 5 minutes have passed, the bot disconnects from the voice channel.
*   **Cancellation**: If another prayer is scheduled to start (or its 10-minute entry window begins) during this wait period, the bot will cancel the exit and remain connected.

---

## 2. Manual & Adhoc Controls

Admins can trigger prayers manually via the dashboard's "Play Now" button or the `/start` slash command.

*   **Respectful Sequence**: Manual prayers do not start abruptly. They follow a specific sequence:
    1.  Bot joins and waits **5 seconds**.
    2.  Bot announces: *"Reciting [Tradition] prayers."*
    3.  Bot waits another **5 seconds** for everyone to prepare.
    4.  Recitation begins.
*   **Exit Rule**: Manual prayers follow the same "Leave 5 minutes after" rule as scheduled prayers.
*   **Emergency Stop**: The `/exit` command immediately stops any active prayer, clears the notification, and makes the bot leave the channel.

---

## 3. Presence & Visibility

The bot provides constant feedback on the upcoming schedule through three different mechanisms.

### 💬 Bot Activity
*   The text appearing under the bot's name in the member list always shows the earliest upcoming prayer across all servers (e.g., *"Playing Next prayer in ~2h 15m"*).

### 🎙️ Voice Channel Status
*   Every **minute**, the bot updates the countdown status (e.g., *"Next prayer starts in ~45m"*).
*   **Status Blips**: If enabled by an admin, the bot will temporarily enter the voice channel (if not already there), update the status text next to the channel name, and then immediately leave. 
*   **Control**: This feature is **disabled by default** and can be toggled per-server in the Dashboard settings.
*   This ensures the countdown is always visible to members looking at the channel list without requiring the bot to stay connected.

### 🧹 Notification Cleanup
*   The bot posts a text notification when a prayer starts: *"🕌 [Tradition] Prayer is now playing..."*
*   To prevent channel spam, the bot **automatically deletes** its own notification message as soon as the prayer finishes or the bot disconnects.

---

## 4. Administrative Features

### 📅 Dashboard Bulk Actions
*   **Enable All**: Activating this will fill and enable all 21 weekly slots. It intelligently fills empty slots with defaults (00:00, 08:00, 16:00 UTC) but **never overwrites** your custom times or prayer types.
*   **Disable All**: Immediately deactivates the entire schedule without deleting your saved times.

### 🔊 Volume Management
*   Admins can adjust the volume from **50% to 750%** per server.
*   **Consistent Greetings**: Greetings and blessings always play at a clear 100% volume, while the volume boost applies only to the prayer audio itself.
*   **Live Updates**: If you change the volume while a prayer is playing, the bot will instantly restart the stream at the new volume level from the exact same second.

### 📋 Logging & History
*   **Logging Channel**: If configured, the bot posts technical logs (joins, errors, idle timeouts) to a private admin channel.
*   **Activity History**: The dashboard shows a history of the last 10 recitations and the last 50 voice room joins/leaves (who stayed and for how long). Data is automatically purged after 30 days.

---

## 5. System Reliability

*   **Isolation**: Every Discord server is completely isolated. Changing the volume, pausing a prayer, or updating a schedule in one server will **never** affect another server.
*   **Idle Detection**: If everyone leaves the voice room while a prayer is paused, the bot will wait 5 minutes and then disconnect to save resources.
*   **DST Awareness**: All time calculations are handled on the server using official timezone databases. This ensures that when your local time changes for Daylight Savings, the prayer remains at the correct local hour.
*   **Startup Resilience**: If the bot restarts, it immediately checks if it should be in a voice channel (the 10-minute window) and resumes its duties without missing a beat.
