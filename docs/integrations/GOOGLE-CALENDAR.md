# Google Calendar

Sapphire can read your schedule, add events, and delete them — all through voice or chat. Uses Google's Calendar API with OAuth2.

## Setup

This one takes a few minutes because of Google's OAuth flow, but you only do it once.

### 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Go to **APIs & Services → Library**
4. Search for "Google Calendar API" and enable it

### 2. Create OAuth Credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Web application**
4. Add authorized redirect URI: `https://localhost:8073/api/plugin/google-calendar/callback`
   - Replace `localhost` with your Sapphire hostname if accessing remotely
5. Copy the **Client ID** and **Client Secret**

### 3. Configure OAuth Consent Screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External** (unless you have Google Workspace)
3. Fill in the required fields (app name, email)
4. Add scope: `https://www.googleapis.com/auth/calendar`
5. Add your Google account as a test user (required while in testing mode)

### 4. Connect in Sapphire

1. Open Settings → Plugins → Google Calendar
2. Enter your Client ID and Client Secret
3. Click **Connect** — this opens Google's OAuth page
4. Authorize the app
5. You'll be redirected back to Sapphire with a success message

## Available Tools

| Tool | What it does |
|------|--------------|
| `calendar_today` | Show today's events with times and free hours |
| `calendar_range` | Get events for a date range (default: next 7 days) |
| `calendar_add` | Add an event with title, date/time, and optional description |
| `calendar_delete` | Delete an event by its number from the last results |

### Adding Events

The AI can add events with flexible time input:
- "Add a meeting tomorrow at 3pm" → creates 1-hour event
- "Add vacation from March 20 to March 25" → multi-day event
- "Schedule dentist appointment on Friday at 10am for 2 hours"

### Deleting Events

After listing events, each gets a number (#1, #2, etc.). The AI uses these to delete:
- "Delete event #3"
- "Cancel my 2pm meeting today"

## Multi-Account

Multiple Google accounts can be connected via scopes.

1. Set up each account's credentials in Settings
2. Connect each one through the OAuth flow
3. Switch using the Calendar scope dropdown in Chat Settings

## Calendar ID

By default, Sapphire uses your primary calendar. To use a different one:
- Settings → Plugins → Google Calendar → Calendar ID
- Enter the calendar ID (found in Google Calendar → Settings → calendar → Integrate)

## Example Commands

- "What's on my calendar today?"
- "What do I have this week?"
- "Schedule a team standup for Monday at 9am"
- "Add 'Pick up groceries' on Saturday at noon"
- "Cancel my 4pm meeting"
- "Am I free Thursday afternoon?"

## Troubleshooting

- **OAuth redirect failed** — Check the redirect URI matches exactly (including port and https)
- **Token expired** — Sapphire auto-refreshes tokens. If it fails, disconnect and reconnect
- **Wrong calendar** — Check Calendar ID setting (default is "primary")
- **Test user required** — While the Google app is in "testing" mode, your account must be listed as a test user
- **Tools not available** — Add Calendar tools to your active toolset

## Reference for AI

Google Calendar integration via OAuth2 REST API.

SETUP:
- Settings → Plugins → Google Calendar
- Enter Client ID + Client Secret from Google Cloud Console
- Click Connect to authorize via OAuth2
- Calendar ID defaults to "primary"

AVAILABLE TOOLS:
- calendar_today() - today's events with free time summary
- calendar_range(start_date?, end_date?) - events in date range (YYYY-MM-DD, default today+7)
- calendar_add(title, start, end?, description?) - add event (start: YYYY-MM-DD or YYYY-MM-DDTHH:MM)
- calendar_delete(event_id) - delete by short ID like "#1" from results

MULTI-ACCOUNT:
- scope_gcal ContextVar for account routing
- Per-scope credentials in credentials_manager

TIME FORMAT:
- 12-hour display, respects USER_TIMEZONE setting
- Events show [past] or [NOW] markers
- Free time summary included in today view

TROUBLESHOOTING:
- OAuth fails: check redirect URI matches exactly
- Token expired: auto-refreshes, reconnect if broken
- Wrong calendar: check GCAL_CALENDAR_ID setting
