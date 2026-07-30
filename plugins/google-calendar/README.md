# Google Calendar Plugin

View your schedule, add events, and delete events — all through voice or chat.

## Easy Connect (recommended)

No Google Cloud Console needed:

1. Open **Settings > Google Calendar** and leave Client ID / Client Secret **blank**
2. Click **Connect Google Calendar**
3. Sign into the Google account whose calendar you want, and approve
4. If Google shows a "Google hasn't verified this app" warning, click
   **Advanced > Go to Sapphire** — expected while the shared app awaits
   Google's verification review
5. You're bounced back to Sapphire showing "Connected ✓"

Works per-calendar too: in the calendar accounts editor, leave a calendar's
Client ID blank, Save, then hit its Connect button.

### Which calendar does it use?

Every connection defaults to `primary` — the main calendar of the Google
account you approved. To point a connection at a different calendar:

1. In [Google Calendar](https://calendar.google.com), hover the calendar in
   the left sidebar, click the **three dots** > **Settings and sharing**
2. Scroll to **Integrate calendar** and copy the **Calendar ID** — it looks
   like `abc123@group.calendar.google.com` (the display name will NOT work)
3. Paste it into that connection's **Calendar ID** field in Sapphire and Save

The calendar must belong to — or be shared with edit rights to — the Google
account you connected.

Easy Connect routes the one-time authorization and token refreshes through
`oauth.sapphireblue.dev`, which holds the shared Google client. Your calendar
data never touches that server — Sapphire talks to Google directly; only
OAuth tokens pass through the relay in transit, and nothing is stored there.
Prefer zero third parties? Use the Advanced setup below — it behaves exactly
as before.

## Advanced: Bring Your Own Google Client

Use your own Google Cloud OAuth client instead of the shared one. The setup
is a one-time thing, but Google's console is a maze. Follow these steps
exactly. Once your Client ID/Secret are filled in, the Connect button uses
them automatically instead of Easy Connect.

### 1. Create a Google Cloud Project (if you don't have one)

- Go to [console.cloud.google.com](https://console.cloud.google.com)
- Click the project dropdown at the top (next to "Google Cloud")
- Click **New Project**, name it whatever you want, click **Create**

### 2. Enable the Google Calendar API

This is the step Google's error messages love to be vague about.

- In the **search bar at the top** of Google Cloud Console, type **Google Calendar API**
- Click the result that says "Google Calendar API"
- Click the big blue **Enable** button
- Wait a few seconds for it to activate

> If you skip this step, you'll get a `403` error about the API not being enabled.

### 3. Configure the OAuth Consent Screen

- Go to **Google Auth Platform** (search "OAuth consent screen" in the top search bar)
- Click **Branding** in the left sidebar
- Fill in:
  - **App name**: anything (e.g. "Sapphire")
  - **User support email**: your email
  - **Developer contact email**: your email
- **Leave "Authorized domains" blank** — you don't need it for personal/local use
- Click **Save**

#### Add Yourself as a Test User

- Click **Audience** in the left sidebar
- Under **Test users**, click **Add users**
- Add your Google email address
- Save

> Without this, you'll see a scary "app not verified" wall with no way through.

### 4. Create OAuth Credentials

- Go to **APIs & Services > Credentials** (or click **Clients** in the Auth Platform sidebar)
- Click **+ Create Credentials > OAuth client ID**
- Application type: **Web application**
- Name: anything
- Under **Authorized redirect URIs**, add your Sapphire callback URL:

```
https://localhost:8073/api/plugin/google-calendar/callback
```

Replace `localhost:8073` with your actual Sapphire host and port if different.

- Click **Create**
- Copy the **Client ID** and **Client Secret** — you'll need these next

### 5. Configure in Sapphire

- Open Sapphire, go to **Settings**
- Find **Google Calendar** in the plugin list
- Paste your **Client ID** and **Client Secret**
- Calendar ID: leave as `primary` for your main Google calendar. If you want a specific calendar, you need the **Calendar ID** (not the display name):
  - In Google Calendar, click the **three dots** next to the calendar name
  - Click **Settings and sharing**
  - Scroll to **Integrate calendar**
  - Copy the **Calendar ID** — it looks like `abc123@group.calendar.google.com`
- Click **Connect Google Calendar**
- Google will ask you to authorize — click through
- You should see "Connected" back in Sapphire settings

## Available Tools

Once connected, the AI can use these tools:

| Tool | What it does |
|------|-------------|
| `calendar_today` | Show today's schedule with times and free hours |
| `calendar_range` | Show events for a date range (defaults to next 7 days) |
| `calendar_add` | Add an event (timed or all-day) |
| `calendar_delete` | Delete an event by ID |

## Troubleshooting

**"Google Calendar API has not been used in project..."**
You skipped step 2. Go enable the API — search "Google Calendar API" in the Cloud Console search bar and click Enable.

**"App not verified" / "Sapphire has not completed Google verification"**
You skipped the test user step (3b). Add your email under Audience > Test users.

**"Invalid domain: must be a top private domain"**
You're trying to add `localhost` to authorized domains. Don't — leave that section blank. You only need the redirect URI in your OAuth client credentials (step 4).

**"redirect_uri_mismatch"**
The redirect URI in Google Console doesn't exactly match what Sapphire sends. Make sure it's exactly:
`https://<your-host>:<your-port>/api/plugin/google-calendar/callback`
— including the protocol (`https` not `http`), port, and full path.

**404 Not Found when using a named calendar**
The Calendar ID isn't the display name. It's a long string like `abc123@group.calendar.google.com`. Find it in Google Calendar > calendar settings > Integrate calendar.

**Token refresh errors after it was working**
Go to Sapphire settings, click Disconnect, then Connect again to re-authorize.
