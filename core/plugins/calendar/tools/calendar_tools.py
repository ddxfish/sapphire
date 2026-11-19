# core/plugins/calendar/tools/calendar_tools.py - her doors into the calendar
"""
Thin doors into plugins/calendar/api.py.

Which calendar a chat uses is its calendar scope (chat settings, or the
persona's; the setting key is `gcal_scope`, kept from the Google Calendar
plugin this one replaced, so every chat keeps its choice). The person picks
it; no tool can name another one. Events are addressed by their id, which
never changes, never by a position in a list.

Times are the person's local wall time: no Z, no offsets.

A calendar may have links (a Google calendar, an ICS address). Before a tool
reads, the links are refreshed, and when one cannot be reached the answer
says the copy is old. New events go where the person chose on the Calendar
page ("new events go to"), unless the call names another place.

Private chats. The tools are flagged local so a private chat can still READ
the calendar, and the rule is kept here: in a private chat nothing is
refreshed (no network at all) and nothing is written.

Guests ride the People whitelist exactly like email: the only path to an
address is a contact with 'Allow AI to send email' ticked.
"""
import json
import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001f4c6'
TOOL_CATEGORY = 'comms'
AVAILABLE_FUNCTIONS = ['calendar_today', 'calendar_range', 'calendar_add', 'calendar_update', 'calendar_delete']

_REPEAT = {
    "repeat": {"type": "string", "enum": ["none", "daily", "weekly", "monthly", "yearly"],
               "description": "How it repeats. none = one time only."},
    "every": {"type": "integer", "description": "Repeat interval: 2 with weekly = every 2 weeks. Default 1."},
    "days": {"type": "string", "description": "Weekly only: the weekdays, e.g. mon,wed,fri. Default: the start's weekday."},
    "until": {"type": "string", "description": "Last day it repeats, YYYY-MM-DD."},
    "times": {"type": "integer", "description": "Or: how many times it happens in all."},
}
_ONE_DAY = {"type": "string",
            "description": "Repeating event only: YYYY-MM-DD of the one occurrence to act on. Left out = the whole series."}

TOOLS = [
    {"type": "function", "is_local": True, "network": True,
     "function": {
         "name": "calendar_today",
         "description": "Today's events with their ids, what is on now, guests and hours scheduled.",
         "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "is_local": True, "network": True,
     "function": {
         "name": "calendar_range",
         "description": "Events for a range of days, with their ids. Default: today and the next 7 days.",
         "parameters": {"type": "object", "properties": {
             "start_date": {"type": "string", "description": "First day, YYYY-MM-DD. Default today."},
             "end_date": {"type": "string", "description": "Last day, YYYY-MM-DD. Default start + 7 days."},
             "query": {"type": "string", "description": "Only events with this text in the title, place or notes."},
         }, "required": []}}},
    {"type": "function", "is_local": True, "network": True, "writes": True,
     "function": {
         "name": "calendar_add",
         "description": "Add an event. Times are local: no Z, no offset.",
         "parameters": {"type": "object", "properties": {
             "title": {"type": "string", "description": "What it is"},
             "start": {"type": "string", "description": "YYYY-MM-DD HH:MM, or YYYY-MM-DD for an all-day event"},
             "end": {"type": "string", "description": "YYYY-MM-DD HH:MM or just HH:MM (default: 1 hour). All-day: the last day."},
             "location": {"type": "string", "description": "Where"},
             "notes": {"type": "string", "description": "Anything else worth keeping"},
             "attendees": {"type": "array", "items": {"type": "string"},
                           "description": "Guests: contact names from People (or ids). Google emails each an invite, so "
                                          "this needs a linked Google calendar. Only contacts with 'Allow AI to send "
                                          "email' resolve."},
             "save_to": {"type": "string",
                         "description": "Optional: 'local' or a linked calendar's name. Default: where this calendar's new events go."},
             **_REPEAT,
         }, "required": ["title", "start"]}}},
    {"type": "function", "is_local": True, "network": True, "writes": True,
     "function": {
         "name": "calendar_update",
         "description": "Change an event by id. Send only what changes. A new start keeps the event's length.",
         "parameters": {"type": "object", "properties": {
             "id": {"type": "string", "description": "Event id from calendar_today / calendar_range"},
             "date": _ONE_DAY,
             "title": {"type": "string", "description": "New title"},
             "start": {"type": "string", "description": "YYYY-MM-DD HH:MM, or YYYY-MM-DD for all day"},
             "end": {"type": "string", "description": "YYYY-MM-DD HH:MM or HH:MM. All-day: the last day."},
             "location": {"type": "string", "description": "Where ('' clears it)"},
             "notes": {"type": "string", "description": "Notes ('' clears them)"},
             **_REPEAT,
         }, "required": ["id"]}}},
    {"type": "function", "is_local": True, "network": True, "writes": True,
     "function": {
         "name": "calendar_delete",
         "description": "Delete an event by id. Cannot be undone.",
         "parameters": {"type": "object", "properties": {
             "id": {"type": "string", "description": "Event id from calendar_today / calendar_range"},
             "date": _ONE_DAY,
         }, "required": ["id"]}}},
]

_WRITE_KEYS = ('title', 'start', 'end', 'location', 'notes', 'repeat', 'every', 'days', 'until', 'times')
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _api():
    from plugins.calendar import api
    return api


def _scope():
    """The chat's calendar; None when it is off or cannot be read (never a default)."""
    try:
        from core.chat.function_manager import scope_gcal
        value = scope_gcal.get()
    except Exception as e:
        logger.warning(f"[calendar] calendar scope unreadable, treating as off: {e}")
        return None
    if value is None or str(value).strip().lower() in ('', 'none'):
        return None
    return str(value).strip()


def _private():
    """True in a private chat, and True when it cannot be told (fail closed)."""
    try:
        from core.chat.function_manager import scope_private
        return bool(scope_private.get())
    except Exception as e:
        logger.warning(f"[calendar] could not read the private flag: {e}")
        return True


# -- guests ---------------------------------------------------------------------

def _people_scope():
    """None when unset or off: refuse, never fall back to a real scope."""
    try:
        from core.chat.function_manager import scope_people
        return scope_people.get()
    except Exception as e:
        logger.debug(f"[calendar] people scope unreadable: {e}")
        return None


def _split_guests(raw):
    """The attendees argument as a list of strings. Models sometimes hand over
    one string ('Rob, Sam' / 'Rob and Sam') or a JSON array inside a string."""
    if raw is None:
        return []
    if isinstance(raw, bool):
        return []
    if isinstance(raw, (int, float)):
        return [str(int(raw))]
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith('['):
            try:
                return _split_guests(json.loads(raw))
            except ValueError:
                pass
        return [s for s in re.split(r'\s*(?:,|;|&|\band\b)\s*', raw) if s.strip()]
    return [str(x) for x in raw if str(x).strip()]


def _match_person(key, people):
    """One name or id -> (person, None) or (None, why). An exact name first,
    then a single partial match; two candidates is a refusal that names them."""
    key = key.strip().lstrip('#')
    if key.isdigit():
        pid = int(key)
        person = next((p for p in people if p.get('id') == pid), None)
        return (person, None) if person else (None, f"Contact id {pid} not found.")
    low = key.lower()
    names = lambda ps: ', '.join(f"{p['name']} [id:{p.get('id')}]" for p in ps)
    exact = [p for p in people if (p.get('name') or '').strip().lower() == low]
    if len(exact) == 1:
        return exact[0], None
    if exact:
        return None, f"'{key}' matches several contacts: {names(exact)}. Use the id."
    partial = [p for p in people if low in (p.get('name') or '').lower()]
    if len(partial) == 1:
        return partial[0], None
    if partial:
        return None, f"'{key}' matches several contacts: {names(partial)}. Use the full name or id."
    return None, f"No contact named '{key}'."


def _resolve_attendees(raw):
    """People names or ids -> (Google attendee dicts, None) or (None, why)."""
    wanted = _split_guests(raw)
    if not wanted:
        return [], None
    scope = _people_scope()
    if scope is None:
        return None, "People contacts are disabled for this chat, so guests can't be resolved."
    from core.contacts import get_people
    people = get_people(scope)
    invitable = [p for p in people if p.get('email_whitelisted') and p.get('email')]
    hint = ("\nInvitable contacts: " + ', '.join(p['name'] for p in invitable)) if invitable else (
        "\nNo contacts are allowed yet. Tick 'Allow AI to send email' on them in Mind, People.")
    out, seen = [], set()
    for key in wanted:
        person, err = _match_person(key, people)
        if err:
            return None, err + hint
        name = person.get('name') or key
        if not person.get('email_whitelisted'):
            return None, f"{name} isn't allowed for invites. Tick 'Allow AI to send email' on them in Mind, People."
        email = (person.get('email') or '').strip()
        if not email:
            return None, f"{name} has no email address in Mind, People."
        if not _EMAIL_RE.match(email):
            return None, f"{name}'s email '{email}' isn't a valid address. Fix it in Mind, People."
        if email.lower() in seen:
            continue
        seen.add(email.lower())
        out.append({'email': email, 'displayName': name})
    return out, None


# -- words --------------------------------------------------------------------

def _clock(text):
    return text[11:16]


def _day_words(day_text):
    d = datetime.strptime(day_text[:10], '%Y-%m-%d')
    return f"{d.strftime('%a %b')} {d.day}" + (f", {d.year}" if d.year != _api().today().year else '')


def _when(o):
    """'Wed Nov 18 09:00-10:00' / 'Wed Nov 18 (all day)' / a span for several days."""
    a, b = o['start'], o['end']
    if o['all_day']:
        return f"{_day_words(a)} (all day)" if a == b else f"{_day_words(a)} to {_day_words(b)} (all day)"
    if a[:10] == b[:10]:
        return f"{_day_words(a)} {_clock(a)}-{_clock(b)}"
    return f"{_day_words(a)} {_clock(a)} to {_day_words(b)} {_clock(b)}"


def _guest_words(o, limit=8):
    guests = o.get('guests') or []
    if not guests:
        return ''
    parts = [f"{g.get('name') or '?'} ({g.get('rsvp') or 'pending'})" for g in guests[:limit]]
    if len(guests) > limit:
        parts.append(f"+{len(guests) - limit} more")
    return f"  [guests: {', '.join(parts)}]"


def _line(o, now=None):
    if o['all_day']:
        when = 'all day' if o['start'] == o['end'] else f"all day, to {_day_words(o['end'])}"
    elif o['start'][:10] == o['end'][:10]:
        when = f"{_clock(o['start'])}-{_clock(o['end'])}"
    else:
        when = f"{_clock(o['start'])} to {_day_words(o['end'])} {_clock(o['end'])}"
    text = f"  {when}  {o['title']}"
    if now and not o['all_day']:
        stamp = now.strftime('%Y-%m-%dT%H:%M')
        text += ' [past]' if o['end'] <= stamp else (' [NOW]' if o['start'] <= stamp else '')
    if o['location']:
        text += f" @ {o['location']}"
    if o['repeats']:
        text += f" ({o['repeat_text']}{_cut_words(o)})"
    if o.get('link_name'):
        text += f" ({o['link_name']}{'' if o['editable'] else ', read-only'})"
    text += f"  [id {o['id']}]{_guest_words(o)}"
    if o['notes']:
        note = ' '.join(o['notes'].split())
        # A read-only link's text was written by whoever publishes that calendar, not by the person.
        whose = '' if o['editable'] else f"note from {o.get('link_name') or 'the linked calendar'}: "
        text += f"\n      {whose}{note[:160]}{'...' if len(note) > 160 else ''}"
    return text


def _cut_words(o):
    """The rule alone would promise a day that was removed from the series: name the days."""
    days = o.get('skipped_days') or []
    if not days:
        return ''
    if len(days) > 4:
        return f", {len(days)} days removed"
    return ", not " + ', '.join(_day_words(d) for d in days)


def _fresh(calendar, first, last):
    """Refresh the calendar's links before a read. Returns the lines that say
    a copy is old; nothing is fetched in a private chat."""
    api = _api()
    if _private():
        has_links = bool(api.links(calendar))
        return ["(Private chat: linked calendars were not refreshed; this is the copy already here.)"] if has_links else []
    out = []
    for link, why in api.refresh(calendar, need=(first, last)):
        if link['synced_at']:
            seen = datetime.fromtimestamp(link['synced_at']).strftime('%b %d %H:%M')
            out.append(f"('{link['name']}' could not be refreshed: {why} Showing its copy from {seen}.)")
        else:
            out.append(f"('{link['name']}' could not be read: {why} Its events are missing here.)")
    return out


def _minutes(found):
    total = 0
    for o in found:
        if not o['all_day']:
            a = datetime.strptime(o['start'], '%Y-%m-%dT%H:%M')
            b = datetime.strptime(o['end'], '%Y-%m-%dT%H:%M')
            total += max(0, (b - a).total_seconds() / 60)
    return total


def _today(calendar):
    api = _api()
    day = api.today()
    stale = _fresh(calendar, day, day)
    found = api.list_range(day, day, calendar)
    head = f"Today ({day.strftime('%A %B')} {day.day}), calendar '{calendar}'"
    if not found:
        return '\n'.join([f"{head}: nothing scheduled."] + stale), True
    lines = [f"{head}:"] + [_line(o, api.now()) for o in found]
    lines.append(f"{len(found)} event{'s' if len(found) != 1 else ''}, about {_minutes(found) / 60:.1f} hours scheduled.")
    return '\n'.join(lines + stale), True


def _range(args, calendar):
    api = _api()
    first_arg = args.get('start_date') or args.get('start')
    last_arg = args.get('end_date') or args.get('end')
    first = api.parse_day(first_arg, 'start_date') if first_arg else api.today()
    if last_arg:
        last = api.parse_day(last_arg, 'end_date')
    else:
        try:
            days = int(args.get('days') or 7)
        except (TypeError, ValueError):
            raise api.CalError("days must be a whole number.")
        last = first + timedelta(days=min(max(days, 0), 366))
    if last < first:
        raise api.CalError("end_date is before start_date.")
    if (last - first).days > 366:
        raise api.CalError("At most 366 days at a time. Narrow the range.")
    query = str(args.get('query') or '').strip()
    stale = _fresh(calendar, first, last)
    found = api.list_range(first, last, calendar, query)
    span = _day_words(first.isoformat()) if first == last else f"{_day_words(first.isoformat())} to {_day_words(last.isoformat())}"
    head = f"Calendar '{calendar}', {span}"
    if query:
        head += f", matching '{query}'"
    if not found:
        return '\n'.join([f"{head}: nothing."] + stale), True
    shown = found[:200]
    lines = [f"{head}: {len(found)} event{'s' if len(found) != 1 else ''}."]
    day = None
    for o in shown:
        if o['day'] != day:
            day = o['day']
            mark = ' (today)' if day == api.today().isoformat() else ''
            lines.append(f"{_day_words(day)}{mark}")
        lines.append(_line(o))
    if len(found) > len(shown):
        lines.append(f"({len(found) - len(shown)} more not shown. Narrow the range.)")
    return '\n'.join(lines + stale), True


def _given(args):
    """Only the fields the call carried: a field left out keeps what the event has."""
    out = {k: args[k] for k in _WRITE_KEYS if k in args and args[k] is not None}
    if 'notes' not in out and args.get('description') is not None:
        out['notes'] = args['description']            # the old tool's name for it
    return out


def _saved_words(ev):
    return f" Saved to {ev['link_name']}." if ev.get('link_name') else ''


def execute(function_name, arguments, config):
    arguments = arguments or {}
    calendar = _scope()
    if calendar is None:
        return "The calendar is off for this chat.", False
    api = _api()
    try:
        if function_name == 'calendar_today':
            return _today(calendar)
        if function_name == 'calendar_range':
            return _range(arguments, calendar)
        if function_name not in ('calendar_add', 'calendar_update', 'calendar_delete'):
            return f"Unknown calendar function '{function_name}'.", False
        if _private():
            return ("This chat is private. The calendar is not inside the chat's vault and may be linked to "
                    "Google, so it can be read here but not changed. Changes need a chat that is not private."), False
        if function_name == 'calendar_add':
            guests, err = _resolve_attendees(arguments.get('attendees'))
            if err:
                return err, False
            ev = api.describe(api.create(_given(arguments), calendar, by='ai',
                                         to=arguments.get('save_to') or None, guests=guests))
            more = f", {ev['repeat_text']}" if ev['repeats'] else ''
            invited = ("\nInvited (Google is emailing them): " + ', '.join(g['displayName'] for g in guests)) if guests else ''
            return f"Added '{ev['title']}': {_when(ev)}{more}.{_saved_words(ev)} [id {ev['id']}]{invited}", True
        event_id = str(arguments.get('id') or arguments.get('event_id') or '').strip().lstrip('#')
        if not event_id:
            return "id is required (from calendar_today or calendar_range).", False
        day = arguments.get('date') or None
        if function_name == 'calendar_update':
            fields = _given(arguments)
            if not fields:
                return "Nothing to change: give at least one of title, start, end, location, notes, repeat.", False
            ev = api.describe(api.update(event_id, fields, calendar, day))
            more = f", {ev['repeat_text']}" if ev['repeats'] else ''
            only = ' (that one occurrence; the rest of the series is as it was)' if day else ''
            return f"Updated '{ev['title']}'{only}: {_when(ev)}{more}. [id {ev['id']}]", True
        kind, ev = api.remove(event_id, day, calendar)
        if kind == 'occurrence':
            words = _day_words(api.parse_day(day).isoformat())
            return f"Deleted the {words} occurrence of '{ev['title']}'. The rest of the series stays.", True
        series = ' and all its repeats' if (ev['rrule'] or ev.get('series')) else ''
        return f"Deleted '{ev['title']}'{series}.", True
    except api.CalError as e:
        return str(e), False
    except Exception as e:
        logger.error(f"[calendar] {function_name} failed: {e}", exc_info=True)
        return f"Calendar error: {e}", False


def shutdown():
    """Plugin disabled, reloaded or Sapphire stopping: let go of the database file."""
    try:
        from plugins.calendar import store
        store.shutdown()
    except Exception as e:
        logger.warning(f"[calendar] shutdown: {e}")
