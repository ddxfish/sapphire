# Calendar

A calendar that lives on this machine, with a page to see it and tools so your AI can use it. Other calendars can be linked in through an account: Google, iCloud, Fastmail, Nextcloud, Yahoo or any CalDAV server (all two-way), or a calendar address (read-only).

It replaces the Google Calendar plugin. A Google account connected there is still connected here, each chat keeps the calendar it was set to, and saved toolsets keep working because the tool names are the same.

## The page

Apps > Calendar. Four views: Year, Month (the default), Week and Day.

- Click a day to add an event, click an event to change or delete it.
- A repeating event can be daily, weekly (on chosen weekdays), monthly or yearly, every N, ending never, on a date, or after a number of times.
- Deleting a repeating event asks: only that day, or every occurrence.
- Events your AI adds show up by themselves (the page refreshes when you come back to it and every 30 seconds).

## Calendars, accounts and links

Events are always kept on this machine. One calendar named `default` exists from the start; add more from the calendar picker ("+ New calendar..."). Each chat uses one calendar, chosen in the chat's settings, or none.

A calendar can hold **accounts**, and each account brings its own calendars, which you tick in or leave out:

```
Calendar: default
  On this machine                      always there
  Google                               tick: Personal, Family
  iCloud                               tick: Home
  Calendar addresses                   US holidays (read-only)
  New events go to: Google, Personal
```

Accounts are added in one panel that shows in two places: the **Accounts** button on the Calendar page, and Settings > Plugins > Calendar. Press **+ Add account** and pick:

| Account | Direction | How you sign in |
|---------|-----------|-----------------|
| Google | two-way, with guests and invitations | Easy Connect: approve on Google's page and you are back. Google may warn that the app is unverified: Advanced > Continue. One Google account per calendar. |
| iCloud | two-way | Apple Account email and an **app-specific password** (account.apple.com, Sign-In and Security). Your normal Apple password does not work. |
| Fastmail | two-way | Fastmail address and an app password that covers calendars. |
| Yahoo | two-way | Yahoo address and an app password. |
| Nextcloud | two-way | Your Nextcloud's address, login name and an app password. |
| Other (CalDAV) | two-way | The CalDAV address your provider or own server gives, a login name and a password. |
| A calendar address | read-only | An `.ics` or `webcal` address: holidays, a team calendar, another app's "secret address in iCal format". No login. |

- The login is tried before it is kept: a wrong password is refused there and then, and nothing is stored.
- Adding an account links every calendar it has. Untick the ones you do not want to see.
- If an email account is set up, an account here can **borrow its login** instead of holding a password of its own (iCloud, Fastmail and Yahoo use one app password for mail and calendar). Change the password under Email and both follow.
- **New events go to** chooses where an event made here is saved: this machine only, or one linked calendar you can write to. It is also where your AI's new events go. Adding the first account points it at that account's first writable calendar.
- **Remove** takes an account and its calendars off this page. Nothing is deleted at the provider.
- A calendar that cannot be reached keeps its last copy, and the Accounts button turns to a warning that says why. A login a server refuses is not tried again for ten minutes, so a changed password cannot lock the account out; fixing the login in Accounts is tried at once.
- Microsoft (Outlook, Microsoft 365) is not here yet. Its published calendar address works as a calendar address, read-only.

Linked events are copies. The account stays the authority for its own events: nothing is merged, so there are no conflicts to resolve. Copies are refreshed when you open the page, before your AI reads the calendar, and right after a change. Nothing is fetched while nobody is looking.

### What a change to a CalDAV event keeps

A change made here to an iCloud, Fastmail, Nextcloud or other CalDAV event is made in the server's own copy of the event: only the lines that change are rewritten. Alarms, the guest list and anything another app stored on the event stay as they were. If the event was changed somewhere else since it was last read here, the change is refused instead of overwriting it; the copy here is brought up to date, and the change can be made again on what is really there.

- A repeating event: changing one day makes that day a changed occurrence of the series, as other calendar apps do. A new title or place for one day never moves its time. Moving the whole series to another time moves its exceptions and its end date with it.
- A whole series can be moved to another day when it simply repeats every N days, weeks, months or years, or weekly on its one weekday (Mondays become Tuesdays). A series pinned to several days (Mondays and Wednesdays, the second Tuesday) is not moved to another day from here: move one occurrence, or change it where the calendar is kept.
- How a series repeats can be changed here unless some of its days were changed on their own; then change it where the calendar is kept.
- Words longer than this calendar keeps (a title or place over 200 characters, notes over 4,000) are shown cut short and are not rewritten from here, so nothing is lost by an edit.
- When a repeat ends on a date, the event's file gets that end as the last second of that day in the event's own time zone, which is how other calendar apps read it.
- Guests cannot be invited or taken off from here on a CalDAV event (that is Google only). An event that already has guests shows them, and the server that holds it may tell them of a change.
- A server with a self-signed certificate is refused. Use a certificate this machine trusts, or a plain `http://` address on your own network (the password then travels unencrypted, so only do that at home).

### Your own Google client (advanced)

Easy Connect needs no Google Console. To use your own OAuth client instead, open Accounts > + Add account > "Use your own Google client", save its client ID and secret, and add this redirect URI to the client in Google Cloud Console:

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
| `calendar_update` | Change an event by id; send only what changes. `add_guests` / `remove_guests` change who is invited, `rsvp` answers an invitation (Google events) |
| `calendar_delete` | Delete an event by id |

- Events are addressed by an id that never changes, never by a position in a list.
- A repeating event: the id alone means the whole series, the id plus `date` means the one occurrence that starts that day. This is the same for local, Google and CalDAV events.
- Times are your local wall time. No Z, no offsets.
- Guests ride the People whitelist, like email: only contacts with "Allow AI to send email" ticked can be invited, and Google sends the invitation. Guests need a Google calendar.
- Invitations, as they work on a Google event: invite people when adding it; add or take off guests later; a change of time, place or notes and a deletion are sent to the guests by Google; listings show each guest's answer; an invitation sent to the calendar's owner shows "your answer: pending" and can be answered yes, no or maybe. Notes are the event's description in Google.
- On the Calendar page the person invites by typing addresses (the page is the person, so no whitelist), takes guests off with the × on their name, and answers an invitation with "Your answer".
- She cannot add, change or remove an account or a link. Only a person can, so nothing she reads can hand your calendar a new login or point it at a new address.

## Privacy

- The calendar file is `user/plugin_state/calendar_events.db`. It is not inside any chat's vault.
- A private chat can read the calendar but not change it, and nothing is fetched from an account or an address while it reads: it sees the copy already here, and is told so.
- A CalDAV account's password is kept in Sapphire's credentials file, scrambled with this machine's key, never in the calendar file. It is only ever sent to the server it was saved for: changing an account's address or user name asks for the password again.
- A CalDAV calendar's address names your account at the provider. It is stored in the calendar file and never sent to the page or to your AI: the page gets a short key for it and the server's host name, and an error your AI is told about names the host at most.
- A login is only sent where its own server points it, and never from an encrypted address to an unencrypted one, or from a server on the internet to an address on your own network.
- A calendar address can be a private one. It is stored in the calendar file and never sent to the page or to your AI; the page shows only its host name.
- Guest names, addresses and answers of linked events are stored in the calendar file with the event. Your AI is shown guests by name, never by address.
- With an account linked, an event your AI adds to one of its calendars is sent to that provider.

## Settings

- **Week starts on**: Sunday or Monday.
- **Easy Connect relay**: only if you run your own relay.

## Not in this version

Reminders, drag to move, Microsoft accounts, guests on CalDAV events, invitations from an event kept only on this machine (no Google link), and a repeat rule the page's form cannot express (it is kept and shown, not edited). On a linked repeating event the page moves, renames or deletes one occurrence or the whole series; its repeat rule is not edited on the page.

## Reference for AI

- Tools: `calendar_today()`, `calendar_range(start_date, end_date, query)`, `calendar_add(title, start, end, location, notes, attendees, save_to, repeat, every, days, until, times)`, `calendar_update(id, date, ..., add_guests, remove_guests, rsvp)`, `calendar_delete(id, date)`.
- `start` is `YYYY-MM-DD HH:MM`, or `YYYY-MM-DD` for all day. `end` may be just `HH:MM`. All-day `end` is the last day, inclusive.
- Repeating events: `id` = whole series, `id` + `date` = one occurrence. Listings show every occurrence with an id.
- A line tagged with a link's name came from that link; `read-only` means it cannot be changed here.
- `save_to`: `local` or a linked calendar's name. Left out, the event goes where the person chose.
- If a listing ends with a line saying a linked calendar could not be refreshed, its events are a copy from the time given.
- Scope: the chat's calendar (`gcal_scope`). `none` turns the tools off.
- Guests can only be invited on a Google calendar. On a CalDAV event `attendees`, `add_guests`, `remove_guests` and `rsvp` are refused in words.
- Files: `api.py` (the one set of operations), `store.py` (SQLite), `repeat.py` (repeat rules), `sources.py` (one module per kind of link: `gcal.py` over `google.py`, `dav.py` over `caldav.py`, `ics.py`), `icsw.py` (writing calendar files), `accounts.py` and `connect.py` (accounts), `sync.py`, `routes/` (page, accounts, links, Google sign-in), `web/` (the Accounts panel and the Settings card), `app/` (the page).
