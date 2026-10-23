# Google Calendar Plugin

View your schedule, add events, and delete events — all through voice or chat.

## Easy Connect (recommended)

No Google Cloud Console needed:

1. Open **Settings > Google Calendar** and click **+ Add Calendar** — name it
   whatever you like ("work", "personal"). That name becomes the calendar's
   scope, which you pick per-chat in the sidebar.
2. Leave **Client ID** and **Client Secret** blank, then click **Save**
   (Connect stays greyed out until the calendar has been saved once)
3. Click **Connect Google**
4. Sign into the Google account whose calendar you want, and approve
5. If Google shows a "Google hasn't verified this app" warning, click
   **Advanced** and continue — expected for an app that hasn't finished
   Google's verification review
6. You're bounced back to Sapphire and the calendar reads **Connected**

Add as many calendars as you like — each one is its own connection, with its
own Google account, its own calendar, and its own chat scope.

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
them automatically instead of Easy Connect — blank the Client ID and connect
again to go back to Easy Connect.

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

- Open Sapphire, go to **Settings > Google Calendar**
- Click **+ Add Calendar** and name it (or select an existing calendar)
- Paste your **Client ID** and **Client Secret**
- Calendar ID: leave as `primary` for your main Google calendar. If you want a specific calendar, you need the **Calendar ID** (not the display name):
  - In Google Calendar, click the **three dots** next to the calendar name
  - Click **Settings and sharing**
  - Scroll to **Integrate calendar**
  - Copy the **Calendar ID** — it looks like `abc123@group.calendar.google.com`
- Click **Save**, then **Connect Google**
- Google will ask you to authorize — click through
- The calendar should read **Connected** back in Sapphire settings

Editing a saved calendar later? Leaving the Secret box empty keeps the stored
one — retype it only if it changed.

## Available Tools

Once connected, the AI can use these tools — always against whichever calendar
that chat has selected in the sidebar:

| Tool | What it does |
|------|-------------|
| `calendar_today` | Show today's schedule with times and free hours |
| `calendar_range` | Show events for a date range (defaults to next 7 days) |
| `calendar_add` | Add an event (timed or all-day) |
| `calendar_delete` | Delete an event by its number from the last listing (or a raw Google event ID) |

`calendar_today` and `calendar_range` number what they list (#1, #2...) — that
number is what `calendar_delete` takes. The numbering is dropped on restart, so
ask for the list again before deleting.

## Troubleshooting

**"Google Calendar API has not been used in project..."**
You skipped step 2. Go enable the API — search "Google Calendar API" in the Cloud Console search bar and click Enable.

**"App not verified" / "hasn't completed the Google verification process"**
On Easy Connect this is expected — click **Advanced** and continue. On your own
client it means you skipped the test user step (3b): add your email under
Audience > Test users.

**"Couldn't reach the connect relay"**
Easy Connect needs `oauth.sapphireblue.dev` reachable, both to connect and to
refresh tokens later. Try again in a moment; if it stays down, switch that
calendar to your own Client ID (Advanced). Calendars using their own client
are unaffected.

**"Invalid domain: must be a top private domain"**
You're trying to add `localhost` to authorized domains. Don't — leave that section blank. You only need the redirect URI in your OAuth client credentials (step 4).

**"redirect_uri_mismatch"**
The redirect URI in Google Console doesn't exactly match what Sapphire sends. Make sure it's exactly:
`https://<your-host>:<your-port>/api/plugin/google-calendar/callback`
— including the protocol (`https` not `http`), port, and full path.

**404 Not Found when using a named calendar**
The Calendar ID isn't the display name. It's a long string like `abc123@group.calendar.google.com`. Find it in Google Calendar > calendar settings > Integrate calendar.

**"No current event listing to resolve #3 against"**
Ask for today's schedule (or the range) again, then delete using the fresh
number — the numbering resets whenever Sapphire restarts.

**"Google Calendar is disabled for this chat"**
That chat's calendar scope is set to none. Pick a calendar in the chat sidebar.

**Token refresh errors after it was working**
Go to Sapphire settings, click Disconnect, then Connect again to re-authorize.
