# Calendar

A calendar that lives on this machine, with a page to see it and tools so your AI can use it. Other calendars can be linked in: a Google calendar (two-way) or any calendar address (read-only).

It replaces the Google Calendar plugin. A Google account connected there is still connected here, each chat keeps the calendar it was set to, and saved toolsets keep working because the tool names are the same.

## The page

Apps > Calendar. Four views: Year, Month (the default), Week and Day.

- Click a day to add an event, click an event to change or delete it.
- A repeating event can be daily, weekly (on chosen weekdays), monthly or yearly, every N, ending never, on a date, or after a number of times.
- Deleting a repeating event asks: only that day, or every occurrence.
- Events your AI adds show up by themselves (the page refreshes when you come back to it and every 30 seconds).

## Calendars and links

Events are always kept on this machine. One calendar named `default` exists from the start; add more from the calendar picker ("+ New calendar..."). Each chat uses one calendar, chosen in the chat's settings, or none.

The **Calendars** button links other calendars into the one you are looking at:

| Link | Direction | What it is |
|------|-----------|------------|
| Google | two-way | A calendar of a connected Google account. Changes made here are sent to Google first. |
| Calendar address | read-only | An `.ics` or `webcal` address: holidays, a team calendar, another app's "secret address in iCal format". |

- **Connect Google** uses Easy Connect: approve on Google's page and you are back. Google may warn that the app is unverified: Advanced > Continue.
- A Google account has several calendars; the first one is linked for you, and "Link this one too" adds more.
- **New events go to** chooses where an event made here is saved: this machine only, or a Google calendar. It is also where your AI's new events go. Connecting Google for the first time points it at Google.
- **Unlink** removes the copies from this page. Nothing is deleted at Google or at the address.
- A link that cannot be reached keeps its last copy, and the Calendars button turns to a warning that says why.

Linked events are copies. Google stays the authority for its own events: nothing is merged, so there are no conflicts to resolve. Copies are refreshed when you open the page, before your AI reads the calendar, and right after a change. Nothing is fetched while nobody is looking.

### Your own Google client (advanced)

Easy Connect needs no Google Console. To use your own OAuth client instead, open Calendars > "Use your own Google client", save its client ID and secret, and add this redirect URI to the client in Google Cloud Console:

```
https://<your Sapphire address>/api/plugin/calendar/callback
```

If you used your own client with the old Google Calendar plugin, your connection still works. Add the new redirect URI before the next time you connect; the old one ended in `/api/plugin/google-calendar/callback`.

## Tools

| Tool | What it does |
|------|--------------|
| `calendar_today` | Today's events with their ids, what is past and on now, guests, hours scheduled |
| `calendar_range` | Events for a range of days (default: today and the next 7), optional text filter |
| `calendar_add` | Add an event, one-time or repeating; `attendees` invites People contacts through Google |
| `calendar_update` | Change an event by id; send only what changes |
| `calendar_delete` | Delete an event by id |

- Events are addressed by an id that never changes, never by a position in a list.
- A repeating event: the id alone means the whole series, the id plus `date` means the one occurrence that starts that day. This is the same for local and Google events.
- Times are your local wall time. No Z, no offsets.
- Guests ride the People whitelist, like email: only contacts with "Allow AI to send email" ticked can be invited, and Google sends the invitation. Guests need a Google calendar.
- She cannot make or remove links. Only the page can, so nothing she reads can point your calendar at a new address.

## Privacy

- The calendar file is `user/plugin_state/calendar_events.db`. It is not inside any chat's vault.
- A private chat can read the calendar but not change it, and nothing is fetched from Google or an address while it reads: it sees the copy already here, and is told so.
- A calendar address can be a private one. It is stored in the calendar file and never sent to the page or to your AI; the page shows only its host name.
- Guest names, addresses and answers of Google events are stored in the calendar file with the event.
- With Google linked, an event your AI adds to a Google calendar is sent to Google.

## Settings

- **Week starts on**: Sunday or Monday.
- **Easy Connect relay**: only if you run your own relay.

## Not in this version

Reminders, drag to move, CalDAV, adding guests from the page, and a repeat rule the page's form cannot express (it is kept and shown, not edited). On Google events, a repeat's rule is changed in Google; here you can move, rename or delete one occurrence or the whole series.

## Reference for AI

- Tools: `calendar_today()`, `calendar_range(start_date, end_date, query)`, `calendar_add(title, start, end, location, notes, attendees, save_to, repeat, every, days, until, times)`, `calendar_update(id, date, ...)`, `calendar_delete(id, date)`.
- `start` is `YYYY-MM-DD HH:MM`, or `YYYY-MM-DD` for all day. `end` may be just `HH:MM`. All-day `end` is the last day, inclusive.
- Repeating events: `id` = whole series, `id` + `date` = one occurrence. Listings show every occurrence with an id.
- A line tagged with a link's name came from that link; `read-only` means it cannot be changed here.
- `save_to`: `local` or a linked calendar's name. Left out, the event goes where the person chose.
- If a listing ends with a line saying a linked calendar could not be refreshed, its events are a copy from the time given.
- Scope: the chat's calendar (`gcal_scope`). `none` turns the tools off.
- Files: `api.py` (the one set of operations), `store.py` (SQLite), `repeat.py` (repeat rules), `google.py`, `ics.py`, `sync.py` (links), `routes/` (page, links, Google sign-in), `app/` (the page).
