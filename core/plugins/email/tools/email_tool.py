# Email tool - plugin tool
"""
Email for the AI: read any folder, search, reply, reply-all, forward, send to
several people with cc/bcc and pictures, save drafts, move mail between
folders (archive, trash, spam, back to the inbox) and mark it read/starred.

Privacy-first: the AI sees names, never addresses, and can only write to
whitelisted contacts unless allow-all is on. A reply goes to the people
already on the thread.

Mail is addressed by a stable id (inbox:4821~k7p) that the mail server keeps, not
by a position in the last listing. The server work lives in mailbox.py, the
message building in message.py, the logins in accounts.py.
"""
import email.utils
import json
import logging
import math
import mimetypes
import re
import smtplib

from plugins.email import accounts, mailbox, message, unattended
from plugins.email.mailbox import Mailbox, MailError

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '📧'
TOOL_CATEGORY = 'comms'
AVAILABLE_FUNCTIONS = [
    'get_inbox',
    'read_email',
    'search_emails',
    'move_emails',
    'archive_emails',
    'delete_emails',
    'mark_emails',
    'send_email',
    'forward_email',
    'get_recipients',
]

PAGE = 4000                      # characters of body per read
MAX_SEEN_IMAGES = 4              # pictures shown to her per read
MAX_IDS = 200                    # emails moved or marked in one call
MAX_ATTACH = 20 * 1024 * 1024    # bytes per outgoing mail
_TEXT_TYPES = ('text/', 'application/json', 'application/xml', 'application/csv')

_FOLDER_HELP = "inbox (default), sent, drafts, archive, spam, trash, or a folder name from the Folders line"
_PEOPLE_HELP = "Contact ids from get_recipients (or addresses, if allow-all is on)"
_IDS = {"type": "array", "items": {"type": "string"}, "description": "Email ids, whole, e.g. [\"inbox:4821~k7p\"]"}
_PEOPLE = {"type": "array", "items": {"type": "string"}}

TOOLS = [
    {
        "type": "function",
        "is_local": False,
        "function": {
            "name": "get_inbox",
            "description": "Latest emails in a folder: id, date, name, subject. read_email(id) for the content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": _FOLDER_HELP},
                    "count": {"type": "integer", "description": "Default 20, max 50"},
                    "unread_only": {"type": "boolean", "description": "Only unread mail"},
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": False,
        "function": {
            "name": "read_email",
            "description": "Read an email in full by id: text, who it went to, attachments. You see attached pictures.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Email id, whole, e.g. inbox:4821~k7p"},
                    "page": {"type": "integer", "description": "For long emails: 2, 3, ..."},
                    "attachment": {"type": "integer", "description": "Open attachment N instead (pictures and text files)"},
                },
                "required": ["id"]
            }
        }
    },
    {
        "type": "function",
        "is_local": False,
        "function": {
            "name": "search_emails",
            "description": "Search mail. Give query (all folders unless folder is set), or the older sender / content / "
                           "date fields. Results list like get_inbox, newest first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "Search words, all must match: plain words, \"exact phrase\", from:ann, "
                                             "to:krem, cc:, subject:, body:, is:unread, is:starred, is:answered, "
                                             "has:attachment, after:YYYY-MM-DD, before:, on:, newer_than:7d, "
                                             "-word to leave out. Example: to:krem subject:invoice after:2026-11-01"},
                    "folder": {"type": "string",
                               "description": "With query: one folder to search instead of all. " + _FOLDER_HELP},
                    "count": {"type": "integer", "description": "Default 20, max 50"},
                    "sender": {"type": "string", "description": "Without query: name or address, partial ok (the recipient, in sent)"},
                    "content": {"type": "string", "description": "Without query: text in the subject or body"},
                    "date": {"type": "string", "description": "Without query: YYYY-MM-DD; ~10 emails on each side of that day"},
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": False,
        "function": {
            "name": "move_emails",
            "description": "Move emails to a folder. to='archive' archives, 'trash' deletes (recoverable), "
                           "'spam' marks as spam, 'inbox' restores (and means not-spam for mail in spam).",
            "parameters": {
                "type": "object",
                "properties": {
                    "ids": _IDS,
                    "to": {"type": "string", "description": "archive, trash, spam, inbox, or a folder name"},
                },
                "required": ["ids", "to"]
            }
        }
    },
    # The two names the plugin had before move_emails. Kept as tools of their
    # own: a saved toolset lists tools by name, so one made before 2.0 would
    # otherwise lose archive and delete without a word.
    {
        "type": "function",
        "writes": True,
        "is_local": False,
        "function": {
            "name": "archive_emails",
            "description": "Archive emails: out of the inbox, kept. Same as move_emails to='archive'.",
            "parameters": {"type": "object", "properties": {"ids": _IDS}, "required": ["ids"]}
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": False,
        "function": {
            "name": "delete_emails",
            "description": "Delete emails: they go to Trash and can be restored from there. Same as move_emails to='trash'.",
            "parameters": {"type": "object", "properties": {"ids": _IDS}, "required": ["ids"]}
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": False,
        "function": {
            "name": "mark_emails",
            "description": "Mark emails read, unread, starred or unstarred.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ids": _IDS,
                    "mark": {"type": "string", "enum": ["read", "unread", "starred", "unstarred"]},
                },
                "required": ["ids", "mark"]
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": False,
        "function": {
            "name": "send_email",
            "description": "Send an email. Give EITHER to (a new email, needs subject) OR reply_to (a reply: "
                           "recipient, subject, thread and quote are set for you).",
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {"type": "string", "description": "Your text only; the quoted original is added on a reply"},
                    "to": dict(_PEOPLE, description=_PEOPLE_HELP),
                    "reply_to": {"type": "string", "description": "Email id to reply to"},
                    "reply_all": {"type": "boolean", "description": "With reply_to: also everyone else on the email"},
                    "subject": {"type": "string", "description": "Required for a new email"},
                    "cc": dict(_PEOPLE, description=_PEOPLE_HELP),
                    "bcc": dict(_PEOPLE, description=_PEOPLE_HELP),
                    "attach": {"type": "array", "items": {"type": "string"},
                               "description": "Pictures from this chat: img:<id>. A library photo (doc:<N>) "
                                              "only on a new email to contacts, never on a reply."},
                    "draft": {"type": "boolean", "description": "Save to Drafts instead of sending"},
                },
                "required": ["body"]
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": False,
        "function": {
            "name": "forward_email",
            "description": "Forward an email, attachments included.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Email id"},
                    "to": dict(_PEOPLE, description=_PEOPLE_HELP),
                    "cc": dict(_PEOPLE, description=_PEOPLE_HELP),
                    "note": {"type": "string", "description": "Your message above the forwarded email"},
                },
                "required": ["id", "to"]
            }
        }
    },
    {
        "type": "function",
        "is_local": False,
        "function": {
            "name": "get_recipients",
            "description": "Whitelisted email contacts (ids + names) for send_email / forward_email.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
]


# --- Scope + settings ---------------------------------------------------------

def _get_current_email_scope():
    try:
        from core.chat.function_manager import scope_email
        return scope_email.get()
    except Exception:
        return None


def _get_current_people_scope():
    """The active people scope, None when unset/disabled. Never 'default' on
    an error: that would hand out another scope's contacts."""
    try:
        from core.chat.function_manager import scope_people
        return scope_people.get()
    except Exception as e:
        logger.debug(f"email: people scope resolution failed: {e}")
        return None


def _setting(key, default=False):
    try:
        from core.plugin_loader import plugin_loader
        return bool(plugin_loader.get_plugin_settings("email").get(key, default))
    except Exception:
        return default


def _open():
    """(scope, creds) for this chat's account, or MailError with the reason."""
    scope = _get_current_email_scope()
    creds, why = accounts.get_creds(scope)
    if not creds:
        raise MailError(why or "Email not configured.")
    return scope, creds


def _as_list(value) -> list:
    """A list argument as a list. Models often send one string for it:
    "inbox:1, inbox:2", "[inbox:1]", or a JSON array inside a string."""
    if value is None or value == '':
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith('[') and text.endswith(']'):
            try:
                got = json.loads(text)
                if isinstance(got, list):
                    return got
            except ValueError:
                pass
            text = text[1:-1]
        # commas and semicolons only: a folder name ("Sent Items:5") may hold a space
        return [p.strip().strip('"\'') for p in re.split(r'[,;]', text) if p.strip().strip('"\'')]
    return [value]


def _whole(value, default, what):
    """A whole number argument (page, attachment), or a refusal in words."""
    if value is None or value == '':
        return default
    try:
        number = float(str(value).strip())
        if number != number or number in (float('inf'), float('-inf')) or number != int(number):
            raise ValueError
        return int(number)
    except (TypeError, ValueError, OverflowError):
        raise MailError(f"{what} must be a whole number (got {value!r}).")


# --- Recipients -----------------------------------------------------------------

def _resolve_recipient(entry):
    """One to/cc/bcc entry -> ((name, addr), None) or (None, why).

    The whitelist gate lives HERE and nowhere else: send, forward, cc and bcc
    all come through it. An address needs allow-all; a contact id needs the
    contact to be whitelisted for email."""
    text = str(entry).strip()
    if '@' in text:
        if not _setting('allow_all_recipients'):
            return None, "Direct email addresses are not allowed. Use a contact id from get_recipients()."
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', text):
            return None, f"Invalid email address: {text}"
        return (text, text), None
    from core.contacts import get_people
    people_scope = _get_current_people_scope()
    if people_scope is None:
        return None, "People contacts are disabled for this chat."
    person = next((p for p in get_people(people_scope) if str(p.get('id')) == text), None)
    if not person:
        return None, f"Contact id {text} not found. Use get_recipients()."
    # Allow-all already lets her write to any address: a contact without the tick
    # is then offered by get_recipients, and has to work by id as well.
    if not person.get('email_whitelisted') and not _setting('allow_all_recipients'):
        return None, f"{person['name']} is not whitelisted for email."
    address = str(person.get('email') or '').strip()
    if not address:
        return None, f"{person['name']} has no email address."
    if not re.match(r'^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$', address):
        return None, f"The email address stored for {person['name']} is not a valid one. The person can fix it in Mind, People."
    return (person['name'], address), None


def _recipients(entries, label='to'):
    """A list of entries -> ([(name, addr)], None) or (None, why). Fails loud
    on the first bad one, so nobody is silently dropped."""
    out = []
    for entry in _as_list(entries):
        pair, why = _resolve_recipient(entry)
        if why:
            return None, f"{label} {entry}: {why}"
        out.append(pair)
    return out, None


def _unique(pairs, skip=()):
    """Drop repeated addresses (and any in `skip`), keeping order."""
    seen = {a.lower() for _, a in skip}
    out = []
    for n, a in pairs:
        if a.lower() not in seen:
            seen.add(a.lower())
            out.append((n, a))
    return out


def _get_recipients():
    from core.contacts import get_people
    allow_all = _setting('allow_all_recipients')
    people_scope = _get_current_people_scope()
    if people_scope is None and not allow_all:
        return "People contacts are disabled for this chat.", False
    lines = []
    if people_scope is not None:
        people = [p for p in get_people(people_scope)
                  if p.get('email') and (allow_all or p.get('email_whitelisted'))]
        if people:
            lines.append("Available contacts:")
            lines += [f"  [{p['id']}] {p['name']}" for p in people]
    if allow_all:
        lines.append("\nDirect addresses are enabled: to/cc/bcc also take any email address.")
    if not lines:
        return "No contacts are whitelisted for email. Add contacts in Mind > People and enable 'Allow email'.", False
    return '\n'.join(lines), True


# --- Listing --------------------------------------------------------------------

def _format_list(creds, mb, key, rows, title=None):
    title = title or key.title()
    if not rows:
        return f"Account: {creds['address']}\n{title}: no emails.\nFolders: {', '.join(mb.folder_keys())}"
    unread = sum(1 for r in rows if r['unread'])
    lines = [f"Account: {creds['address']}",
             f"{title}: {len(rows)} shown, {unread} unread"]
    for r in rows:
        tags = (" *NEW*" if r['unread'] else "") + (" (starred)" if r['starred'] else "") \
               + (" (attachment)" if r['attach'] else "")
        # A Gmail everywhere-search finds mail in All Mail; `where` is the folder it shows in.
        here = r['id'].rpartition(':')[0]
        place = r.get('where') or here
        sent = place == 'sent'                               # sent mail lists who it went to
        who = f"to {r.get('to_name') or r['name']}" if sent and here != 'sent' else (f"to {r['name']}" if sent else r['name'])
        if place != here:
            tags += f" (in {place})"
        lines.append(f"  [{r['id']}] {r['date']} - {who}: {r['subject']}{tags}")
    lines.append(f"Folders: {', '.join(mb.folder_keys())}")
    lines.append("read_email(id) for the content.")
    return '\n'.join(lines)


def _count(value, default=20):
    try:
        return min(max(1, int(value)), 50)
    except (TypeError, ValueError):
        return default


def _get_inbox(folder='inbox', count=20, unread_only=False):
    scope, creds = _open()
    with Mailbox(scope, creds) as mb:
        key, rows = mb.latest(folder or 'inbox', _count(count), bool(unread_only))
        return _format_list(creds, mb, key, rows), True


def _search_query(query, folder, count):
    """search_emails(query=...): the search words of search.py, everywhere unless a folder is named."""
    from plugins.email import search
    try:
        terms, place = search.where(search.parse(query))
    except ValueError as e:
        return str(e), False
    if not terms and not place and not folder:
        return f"The query is empty. {search.HELP}", False
    spot = str(folder or place or 'all').strip()
    everywhere = spot.lower() in ('all', 'anywhere', 'everywhere')
    scope, creds = _open()
    with Mailbox(scope, creds) as mb:
        rows, total = mb.find(terms, None if everywhere else spot, everywhere, _count(count))
        place_words = 'all folders' if everywhere else mb.key_for(mb.resolve(spot))
        title = f"Search \"{query.strip()}\" in {place_words}"
        if not rows:
            return (f"Account: {creds['address']}\n{title}: nothing matched.\n"
                    f"Folders: {', '.join(mb.folder_keys())}"), True
        text = _format_list(creds, mb, None, rows, title=title)
        if any(r.get('where') and r['where'] != r['id'].rpartition(':')[0] for r in rows):
            text += ("\nThese ids are from Gmail's All Mail (archive), which holds every mail; \"(in inbox)\" is "
                     "where the mail shows. The id works as it is for read, move and mark.")
        if total > len(rows):
            text += f"\n({total} matched; these are the newest {len(rows)}. Add words to narrow it.)"
        return text, True


def _search_emails(sender=None, content=None, date=None, folder=None, count=20, query=None):
    if query and str(query).strip():
        if sender or content or date:
            return "Give query, or the sender / content / date fields, not both. query can say it all: from:ann after:2026-11-01", False
        return _search_query(str(query), folder, count)
    folder = folder or 'inbox'
    if not any([sender, content, date]):
        return "Give query (e.g. to:krem subject:invoice), or at least one of: sender, content, date.", False
    day = None
    if date:
        day = mailbox.parse_day(date)
        if day is None:
            return f"Invalid date {date!r}. Use YYYY-MM-DD.", False
    scope, creds = _open()
    with Mailbox(scope, creds) as mb:
        key, rows = mb.search(folder or 'inbox', sender=sender, content=content, date=day, count=_count(count))
        if not rows:
            return "No matching emails found.", True
        return _format_list(creds, mb, key, rows, title=f"Search in {key}"), True


# --- Reading --------------------------------------------------------------------

def _size(n):
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB"


def _paged(text, page):
    pages = max(1, math.ceil(len(text) / PAGE))
    page = min(max(1, int(page or 1)), pages)
    return text[(page - 1) * PAGE: page * PAGE], page, pages


def _pictures(files):
    """Raw bytes of the first few pictures, shaped for the chat."""
    out = []
    try:
        from core import images as ci
    except Exception:
        return out
    for f in files:
        if len(out) >= MAX_SEEN_IMAGES:
            break
        if f['type'].startswith('image/'):
            try:
                out.append(ci.for_chat(f['data']))
            except Exception:
                pass   # not a picture PIL can open (svg, a broken file)
    return out


def _with_pictures(text, pics):
    if not pics:
        return text
    from core import images as ci
    return ci.result(text, pics)


def _read_attachment(handle, files, n, page):
    if not files:
        return f"Email {handle} has no attachments.", False
    if n < 1 or n > len(files):
        return f"No attachment {n}. This email has {len(files)}.", False
    f = files[n - 1]
    title = f"Attachment {n} of {handle}: {f['name']} ({f['type']}, {_size(len(f['data']))})"
    if f['type'].startswith('image/'):
        pics = _pictures([f])
        if pics:
            return _with_pictures(title, pics), True
    if f['type'] == 'message/rfc822':             # an email attached to the email
        inner = message.parse(f['data'])
        head = [f"From: {message.names(message.addresses(inner, 'From')) or 'Unknown'}{_stranger_tag(inner)}"]
        to = message.names(message.addresses(inner, 'To'))
        if to:
            head.append(f"To: {to}")
        head += [f"Subject: {message.header(inner, 'Subject', '(no subject)')}", f"Date: {message.header(inner, 'Date', '?')}"]
        more_files = message.attachments(inner)
        if more_files:
            head.append("Its own attachments (not openable from here): " + ', '.join(x['name'] for x in more_files))
        text, page, pages = _paged(message.body_text(inner) or '(no text content)', page)
        more = f"\n\n(page {page} of {pages}; read_email(id, attachment={n}, page={page + 1}) for more)" if page < pages else ""
        return f"{title}\nThis attachment is an email:\n" + '\n'.join(head) + f"\n\n{text}{more}", True
    if f['type'].startswith(_TEXT_TYPES):
        text, page, pages = _paged(f['data'].decode('utf-8', errors='replace'), page)
        more = f"\n\n(page {page} of {pages}; read_email(id, attachment={n}, page={page + 1}) for more)" if page < pages else ""
        return f"{title}\n\n{text}{more}", True
    return f"{title}\nThis kind of file can't be opened here.", False


def _stranger_tag(msg):
    """' (not in your contacts)' when the sender's address is no People
    contact's. She sees names, never addresses, and anyone can write any
    name: this is the one sign that "Mom" is not Mom."""
    scope = _get_current_people_scope()
    if scope is None:
        return ''
    frm = message.addresses(msg, 'From')
    if not frm:
        return ''
    try:
        from core.contacts import get_people
        known = {(p.get('email') or '').strip().lower() for p in get_people(scope)}
    except Exception as e:
        logger.debug(f"[EMAIL] contacts not read for the sender check: {e}")
        return ''
    return '' if frm[0][1].lower() in known else ' (not in your contacts)'


def _reply_warning(msg, own):
    """A line when a reply would not go back to the sender (a Reply-To that
    names someone else): the oldest trick for getting an answer sent elsewhere."""
    frm = {a.lower() for _, a in message.addresses(msg, 'From')}
    back, _cc = message.reply_targets(msg, own)
    other = [p for p in back if p[1].lower() not in frm]
    if not frm or not other:
        return []
    return [f"Replies go to: {message.names(other)} (NOT the sender; this email asks for replies elsewhere)"]


def _read_email(handle, page=1, attachment=None):
    page = _whole(page, 1, 'page')
    scope, creds = _open()
    with Mailbox(scope, creds) as mb:
        msg, flags = mb.message(handle, mark_read=True)
    files = message.attachments(msg)
    if attachment is not None:
        return _read_attachment(handle, files, _whole(attachment, 1, 'attachment'), page)

    text, page, pages = _paged(message.body_text(msg) or '(no text content)', page)
    lines = [f"From: {message.names(message.addresses(msg, 'From')) or 'Unknown'}{_stranger_tag(msg)}"]
    lines += _reply_warning(msg, creds['address'])
    for label in ('To', 'Cc'):
        who = message.names(message.addresses(msg, label))
        if who:
            lines.append(f"{label}: {who}")
    lines.append(f"Subject: {message.header(msg, 'Subject', '(no subject)')}")
    lines.append(f"Date: {message.header(msg, 'Date', '?')}")
    if b'\\flagged' in flags:
        lines.append("Starred")
    if files:
        lines.append("Attachments:")
        lines += [f"  [{i}] {f['name']} ({f['type']}, {_size(len(f['data']))})" for i, f in enumerate(files, 1)]
    lines += ["", text]
    if page < pages:
        lines.append(f"\n(page {page} of {pages}; read_email(id, page={page + 1}) for more)")
    return _with_pictures('\n'.join(lines), _pictures(files) if page == 1 else []), True


# --- Moving + marking -------------------------------------------------------------

_MOVED = {'archive': "Archived", 'trash': "Moved to Trash (recoverable there)",
          'spam': "Marked as spam", 'inbox': "Moved to Inbox"}
_MARKS = {'read': (b'\\Seen', True), 'unread': (b'\\Seen', False),
          'starred': (b'\\Flagged', True), 'unstarred': (b'\\Flagged', False)}


def _skipped_lines(skipped):
    if not skipped:
        return []
    return [f"Skipped {len(skipped)}:"] + [f"  [{h}] {why}" for h, why in skipped]


def _move_emails(ids, to, fixed=False):
    """`fixed`: called as archive_emails / delete_emails, where `to` is not hers to give."""
    ids, dest = _as_list(ids), str(to or '').strip()
    if not ids or not dest:
        return ("ids is required." if fixed else "ids and to are both required."), False
    if len(ids) > MAX_IDS:
        return f"At most {MAX_IDS} emails at a time.", False
    scope, creds = _open()
    with Mailbox(scope, creds) as mb:
        # Archive is the one folder made when missing; a server with no Trash
        # or Spam is refused, so a delete can never become a permanent one.
        moved, skipped = mb.move(ids, dest, create='Archive' if dest.lower() == 'archive' else None)
    lines = []
    if moved:
        lines.append(f"{_MOVED.get(dest.lower(), f'Moved to {dest}')}: {len(moved)} email{'s' if len(moved) != 1 else ''}")
        lines += [f"  [{h}] {s}" for h, s in moved]
        lines.append("They have new ids in that folder; list it to see them.")
        logger.info(f"[EMAIL] moved {len(moved)} to {dest}")
    lines += _skipped_lines(skipped)
    return '\n'.join(lines) or "Nothing to move.", bool(moved)


def _mark_emails(ids, mark):
    ids = _as_list(ids)
    if not ids:
        return "ids is required.", False
    if mark not in _MARKS:
        return f"mark must be one of: {', '.join(_MARKS)}.", False
    flag, on = _MARKS[mark]
    scope, creds = _open()
    with Mailbox(scope, creds) as mb:
        changed, skipped = mb.flag(ids, flag, on)
    lines = [f"Marked {changed} email{'s' if changed != 1 else ''} {mark}."] if changed else []
    lines += _skipped_lines(skipped)
    return '\n'.join(lines) or "Nothing to mark.", bool(changed)


# --- Sending --------------------------------------------------------------------

def _in_task_run():
    """True when this turn was started by an event (a new email, a webhook),
    not by a person typing. Mail sent from such a run is automatic mail: it
    says so in its headers, so the other side's responder does not answer it."""
    try:
        from core.continuity.executor import current_event_data
        return bool(current_event_data.get())
    except Exception:
        return False


def _attachments(sources, reply=False):
    """attach=[...] -> ([{'name','type','data'}], None) or (None, why).

    Pictures she already holds, by handle, and nothing else. A reply goes to
    whoever wrote in, and an incoming mail can ask for anything, so:
      - img:<id>, a picture in this chat: always.
      - doc:<N>, a library photo: only on a new email, whose recipients all
        passed the whitelist gate. Library photos are numbered 1, 2, 3...,
        so "reply with doc:1 to doc:20" would walk the whole library.
      - a file path or a web address: never. core.images would read any
        picture on this machine, or fetch any address this machine can reach
        (a camera on the home network), and mail it out."""
    out, total = [], 0
    for i, src in enumerate(_as_list(sources), 1):
        from core import images as ci
        handle = str(src).strip()
        if handle.startswith('doc:'):
            if reply:
                return None, (f"attach {handle}: a library photo can only go on a new email to contacts you are "
                              "allowed to write (send_email with to), not on a reply.")
        elif not handle.startswith('img:'):
            return None, (f"attach {handle}: only pictures you already have can be attached, as img:<id> "
                          "(a picture in this chat) or doc:<N> (a library photo). File paths and web "
                          "addresses are not attachable.")
        try:
            got = ci.resolve(handle)
        except ci.ImageError as e:
            return None, f"attach {src}: {e}"
        name = re.sub(r'[^\w.\- ]', '_', str(got.label or ''))[-80:] or f"image-{i}"
        if not re.search(r'\.\w{2,5}$', name):
            name += mimetypes.guess_extension(got.media_type) or '.jpg'
        total += len(got.data)
        if total > MAX_ATTACH:
            return None, f"Attachments are over {MAX_ATTACH // 1048576} MB in total."
        out.append({'name': name, 'type': got.media_type, 'data': got.data})
    return out, None


def _deliver(scope, creds, msg, pairs, what):
    """SMTP out + the Sent copy. (text, ok)."""
    try:
        return mailbox.send(scope, creds, msg, [a for _, a in pairs]), True
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"[EMAIL] SMTP login refused ({what}): {e.smtp_code}")
        return f"{what} failed: {getattr(e, 'words', '') or 'the mail server refused the login. Check the password.'}", False
    except smtplib.SMTPRecipientsRefused as e:
        # The server's own text names the addresses: she gets the people, the log gets the count.
        who = message.names([p for p in pairs if p[1] in e.recipients]) or f"{len(e.recipients)} recipient(s)"
        logger.error(f"[EMAIL] {what}: the server refused {len(e.recipients)} recipient(s)")
        return f"{what} failed: the mail server refused the address of {who}. Nothing was sent.", False
    except smtplib.SMTPResponseException as e:
        logger.error(f"[EMAIL] {what}: {type(e).__name__} {e.smtp_code}")
        if isinstance(e, smtplib.SMTPSenderRefused):
            return f"{what} failed: the mail server does not let this account send (code {e.smtp_code}).", False
        return f"{what} failed: the mail server answered with an error (code {e.smtp_code}).", False
    except Exception as e:
        words = getattr(e, 'words', '')
        if words:
            logger.error(f"[EMAIL] {what}: {type(e).__name__}")
            return f"{what} failed: {words}", False
        logger.error(f"[EMAIL] {what} error: {e}", exc_info=True)
        return f"{what} failed: {e}", False


def _send_email(body='', to=None, reply_to=None, reply_all=False, subject=None,
                cc=None, bcc=None, attach=None, draft=False):
    # Exactly one target mode. A call naming both people AND a mail to reply
    # to is ambiguous, and guessing once sent a mail to the wrong person.
    has_to = bool(_as_list(to))
    if has_to and reply_to:
        return ("Pass EITHER to (a new email) OR reply_to (a reply), not both. "
                "To add people to a reply, use cc.", False)
    if not has_to and not reply_to:
        return ("No recipient. Pass to (contact ids from get_recipients) for a new email, "
                "or reply_to (an email id) for a reply.", False)

    scope, creds = _open()
    automatic, claimed = _in_task_run(), False
    cc_pairs, why = _recipients(cc, 'cc')
    if why:
        return why, False
    bcc_pairs, why = _recipients(bcc, 'bcc')
    if why:
        return why, False
    files, why = _attachments(attach, reply=bool(reply_to))
    if why:
        return why, False
    if not isinstance(body, str):
        return "body must be the email's text.", False

    original, mode = None, None
    if reply_to:
        if reply_all and not _setting('allow_reply_all', True):
            return "Reply-all is turned off in Settings > Plugins > Email. Send a plain reply, or ask for it to be turned on.", False
        with Mailbox(scope, creds) as mb:
            original, flags = mb.message(reply_to)
        to_pairs, thread_cc = message.reply_targets(original, creds['address'], reply_all)
        if not to_pairs:
            return "Could not determine a reply address from that email.", False
        if automatic and not draft:
            # Nobody is in the loop in a run an incoming mail started, and that mail
            # may itself say what to do. So this door has the auto-reply handler's
            # rules (unattended.py), whatever the task's prompt or the mail asks for.
            if reply_all:
                return ("Not sent: in a run started by an incoming email, a reply goes to its sender only, "
                        "never to everyone on it. Send a plain reply."), False
            to_pairs, thread_cc = to_pairs[:1], []
            no = unattended.refusal(scope, original, flags, to_pairs[0][1], _get_current_people_scope(),
                                    _setting('auto_reply_anyone'))
            if no:
                return f"Not sent: {no[1]}. Leave it unanswered.", False
            if not unattended.claim(creds['address'], reply_to):
                return "Not sent: that email is being answered, or has been, by another run.", False
            if not unattended.under_cap(scope, to_pairs[0][1]):
                unattended.release(creds['address'], reply_to)
                return f"Not sent: {unattended.CAP_WORDS}. Leave it unanswered.", False
            claimed = True
        cc_pairs = thread_cc + cc_pairs
        subject = subject or message.prefixed(message.header(original, 'Subject'), 'Re')
        mode = 'reply'
    else:
        to_pairs, why = _recipients(to, 'to')
        if why:
            return why, False
        if not subject:
            return "subject is required for new emails.", False

    to_pairs = _unique(to_pairs)
    cc_pairs = _unique(cc_pairs, skip=to_pairs)
    bcc_pairs = _unique(bcc_pairs, skip=to_pairs + cc_pairs)
    msg = message.build(creds['address'], to_pairs, cc_pairs, subject, body, original, mode, files,
                        auto=automatic)

    extras = (f" (cc {message.names(cc_pairs)})" if cc_pairs else "") \
        + (f" (bcc {message.names(bcc_pairs)})" if bcc_pairs else "") \
        + (f" ({len(files)} attached)" if files else "")
    if draft:
        if bcc_pairs:        # a draft is a file, not a delivery: its Bcc line is how the mail program knows
            msg['Bcc'] = ', '.join(email.utils.formataddr(p) for p in bcc_pairs)
        with Mailbox(scope, creds) as mb:
            mb.append('drafts', msg, flags=(b'\\Draft', b'\\Seen'))
        return f"Draft saved for {message.names(to_pairs)}{extras}: \"{subject}\" (not sent)", True

    note, ok = _deliver(scope, creds, msg, to_pairs + cc_pairs + bcc_pairs, "Send")
    if not ok:
        if claimed:
            unattended.release(creds['address'], reply_to)       # nothing went out: it may be answered later
        return note, False
    if reply_to:
        try:
            with Mailbox(scope, creds) as mb:
                mb.flag([reply_to], b'\\Answered')
        except Exception as e:
            logger.debug(f"[EMAIL] could not flag {reply_to} answered: {e}")
    logger.info(f"[EMAIL] sent ({'reply' if reply_to else 'new'}) to {len(to_pairs + cc_pairs + bcc_pairs)} "
                f"recipient(s){', ' + str(len(files)) + ' attached' if files else ''}")
    return f"Email sent to {message.names(to_pairs)}{extras}: \"{subject}\"{note}", True


def _forward_email(handle, to, cc=None, note=''):
    to_pairs, why = _recipients(to, 'to')
    if why:
        return why, False
    if not to_pairs:
        return "to is required: contact ids from get_recipients().", False
    cc_pairs, why = _recipients(cc, 'cc')
    if why:
        return why, False
    scope, creds = _open()
    with Mailbox(scope, creds) as mb:
        original, _ = mb.message(handle)

    files, left_out = message.attachments(original), ""
    if sum(len(f['data']) for f in files) > MAX_ATTACH:
        files, left_out = [], f" (attachments over {MAX_ATTACH // 1048576} MB were left out)"
    subject = message.prefixed(message.header(original, 'Subject', '(no subject)'), 'Fwd')
    to_pairs = _unique(to_pairs)
    cc_pairs = _unique(cc_pairs, skip=to_pairs)
    msg = message.build(creds['address'], to_pairs, cc_pairs, subject, note or '', original, 'forward', files)

    sent_note, ok = _deliver(scope, creds, msg, to_pairs + cc_pairs, "Forward")
    if not ok:
        return sent_note, False
    extras = (f" (cc {message.names(cc_pairs)})" if cc_pairs else "") \
        + (f" ({len(files)} attached)" if files else "") + left_out
    logger.info(f"[EMAIL] forwarded to {len(to_pairs + cc_pairs)} recipient(s)")
    return f"Forwarded to {message.names(to_pairs)}{extras}: \"{subject}\"{sent_note}", True


# --- Executor -------------------------------------------------------------------

_OLD_ARGS = ('index', 'indices', 'reply_to_index')


def execute(function_name, arguments, config):
    # Scope gate at the ONE door: a chat with email off gets a refusal before
    # any credential or server path runs.
    if _get_current_email_scope() is None:
        return "Email is disabled for this chat.", False
    arguments = arguments or {}
    if any(k in arguments for k in _OLD_ARGS):
        return ("Emails are addressed by id now (like inbox:4821~k7p), not by a number in the list. "
                "Call get_inbox or search_emails to see the ids.", False)
    try:
        if function_name == "get_inbox":
            return _get_inbox(arguments.get('folder', 'inbox'), arguments.get('count', 20),
                              arguments.get('unread_only', False))
        if function_name == "read_email":
            if not arguments.get('id'):
                return "id is required.", False
            return _read_email(arguments['id'], arguments.get('page', 1), arguments.get('attachment'))
        if function_name == "search_emails":
            return _search_emails(arguments.get('sender'), arguments.get('content'), arguments.get('date'),
                                  arguments.get('folder'), arguments.get('count', 20), arguments.get('query'))
        if function_name == "move_emails":
            return _move_emails(arguments.get('ids'), arguments.get('to'))
        if function_name == "archive_emails":
            return _move_emails(arguments.get('ids'), 'archive', fixed=True)
        if function_name == "delete_emails":
            return _move_emails(arguments.get('ids'), 'trash', fixed=True)
        if function_name == "mark_emails":
            return _mark_emails(arguments.get('ids'), arguments.get('mark'))
        if function_name == "send_email":
            if not arguments.get('body'):
                return "body is required.", False
            return _send_email(
                body=arguments['body'], to=arguments.get('to'), reply_to=arguments.get('reply_to'),
                reply_all=bool(arguments.get('reply_all')), subject=arguments.get('subject'),
                cc=arguments.get('cc'), bcc=arguments.get('bcc'), attach=arguments.get('attach'),
                draft=bool(arguments.get('draft')))
        if function_name == "forward_email":
            if not arguments.get('id'):
                return "id is required.", False
            return _forward_email(arguments['id'], arguments.get('to'), arguments.get('cc'),
                                  arguments.get('note', ''))
        if function_name == "get_recipients":
            return _get_recipients()
        return f"Unknown email function '{function_name}'.", False
    except MailError as e:
        return str(e), False
    except Exception as e:
        # accounts.py says why a connection failed (wrong name, port or password, a
        # certificate nobody trusted). Only a person can fix those: the words say so.
        words = getattr(e, 'words', '')
        if words:
            logger.error(f"Email tool {function_name}: {type(e).__name__}: {e}")
            return words, False
        logger.error(f"Email tool error in {function_name}: {e}", exc_info=True)
        if type(e).__name__ == 'LoginError':
            return f"Email login failed - check the account's password. ({e})", False
        return f"Email error: {e}", False


def shutdown():
    """Plugin disabled, reloaded or Sapphire stopping: log out the Email page's kept logins."""
    try:
        from plugins.email import index, mailbox
        mailbox.close_kept()
        index.shutdown()
    except Exception as e:
        logger.warning(f"[EMAIL] shutdown: {e}")
