# core/plugins/calendar/sources.py - where a link's events are kept
"""
A link feeds a calendar from somewhere else. Each KIND of link is one module
with the same few functions, so api.py and sync.py never ask which kind they
are holding:

    KIND, WINDOWED, GUESTS, EVERY, RETRY
    pull(link, first_day, last_day, force)  -> (rows, note, done) | None
    add(link, row, guests)                  -> the stored event
    change(link, event, fields, day, guests, shown) -> the stored event
    remove(link, event, day)                -> 'event' | 'occurrence'

  google   gcal.py    two-way, guests and invitations
  caldav   dav.py     two-way (iCloud, Fastmail, Nextcloud, Yahoo, any CalDAV server)
  ics      ics.py     read-only: a published calendar address

pull() returning None means "the source says nothing changed". `done`, when
given, is called once the rows are stored. A read-only kind has no add,
change or remove: its links are never `writable`, and api.py refuses before
it would call them.

A new provider (Microsoft, say) is one more module here and one more entry.
"""
from . import dav, gcal, ics
from .errors import CalError

BY_KIND = {gcal.KIND: gcal, dav.KIND: dav, ics.KIND: ics}


def of(link):
    src = BY_KIND.get(link['kind'])
    if src is None:
        raise CalError(f"This link is of a kind ({link['kind']}) this version cannot read.")
    return src
