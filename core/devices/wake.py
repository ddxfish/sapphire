# core/devices/wake.py - one listener answers a wake word (tmp/device-manager-upgrade-plan.md §10)
#
# Three things can hear "hey Sapphire" in one room: the main app's own
# microphone and every satellite. Left alone, each records, transcribes and
# answers. This keeps the recent hearings of the room, so that one wake
# gets one answer:
#   claim(who)      a wake word fired at `who`. The main app claims the
#                   instant it hears (in-process, no network) and always
#                   wins: it takes a satellite's claim younger than CLAIM.
#   shadowed(who)   the other hearings younger than SHADOW, newest first.
#                   A satellite whose recording arrives compares its words
#                   with theirs (words_of, same) and is dropped only when it
#                   repeats one; a second person's different question goes
#                   through. With none, it claims the room on arrival.
#   heard(who, words), release(who)
# Satellites claim on arrival for now, seconds after the wake (the floor,
# §10 A). Claiming at the wake itself, over the wire, is the lock that comes
# with firmware (§10 B); claim() is written for it.
import difflib
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

MAIN = 'main'
CLAIM = 1.5        # seconds within which one wake's claims land; the main app pre-empts inside it
LISTEN_MAX = 20.0  # seconds a hearing without words yet is still listening: a recording and its upload
SHADOW = 12.0      # seconds a hearing's words shadow later arrivals (the slower endpoint, the upload)
WORDS_WAIT = 4.0   # seconds a shadowed arrival waits for the holder's words
SAME = 0.75        # ratio at which two transcripts are one utterance
CONTAINED = 12     # characters a transcript needs before "inside the other" counts

_cv = threading.Condition()
_recent = []       # [{'who', 'at', 'words', 'heard_at', 'done'}], oldest first
told = None        # callable(who): a satellite whose claim the main app took is told to stand down (voice sets it)


def _listening(h, now):
    """Still recording, or its recording is on its way: the room is held."""
    return h['words'] is None and not h['done'] and now - h['at'] < LISTEN_MAX


def _fresh(h, now):
    """Its words are known and recent: a later arrival may be the same words."""
    return h['words'] is not None and now - h['heard_at'] < SHADOW


def _prune():
    now = time.monotonic()
    _recent[:] = [h for h in _recent if _listening(h, now) or _fresh(h, now)]


def claim(who):
    """A wake word fired at `who`. (yours, taken_by). The main app always
    gets the room; a satellite does unless another listener claimed it
    within CLAIM, or is still listening - one long question, with the wake
    word said again inside it, is one question."""
    with _cv:
        _prune()
        now = time.monotonic()
        live = [h for h in _recent if h['who'] != who and not h['done']
                and (_listening(h, now) or now - h['at'] < CLAIM)]
        if live and who != MAIN:
            return False, live[-1]['who']
        for h in live:
            h['done'] = True
            logger.info(f"[WAKE] {MAIN} takes the room from {h['who']}")
        _recent.append({'who': who, 'at': now, 'words': None, 'heard_at': 0.0, 'done': False})
        _cv.notify_all()
    for h in live:
        if told is not None and h['who'] != MAIN:
            try:
                told(h['who'])
            except Exception as e:
                logger.debug(f"[WAKE] {h['who']} could not be told to stand down: {e}")
    return True, None


def holds(who):
    """True while `who` has a hearing that is not finished."""
    with _cv:
        _prune()
        return any(h['who'] == who and not h['done'] for h in _recent)


def shadowed(who):
    """The other listeners' hearings an arrival of `who` must be compared
    with: still listening, or with words known within SHADOW. Newest first."""
    with _cv:
        _prune()
        return [h for h in reversed(_recent) if h['who'] != who]


def heard(who, words):
    """What `who` made of its recording."""
    with _cv:
        for h in reversed(_recent):
            if h['who'] == who:
                h['words'], h['heard_at'] = str(words or ''), time.monotonic()
                break
        _cv.notify_all()


def release(who):
    """`who` is finished with the room (its turn ended, or it heard nothing)."""
    with _cv:
        for h in reversed(_recent):
            if h['who'] == who and not h['done']:
                h['done'] = True
                break
        _cv.notify_all()


def words_of(hearing, timeout=WORDS_WAIT):
    """A hearing's words, waiting up to `timeout` for them. None when they
    never came: the holder failed, or is still recording."""
    end = time.monotonic() + timeout
    with _cv:
        while hearing['words'] is None and not hearing['done']:
            left = end - time.monotonic()
            if left <= 0:
                return None
            _cv.wait(left)
        return hearing['words']


def _norm(text):
    return ' '.join(re.sub(r"[^\w\s']", ' ', str(text or '').lower()).split())


def same(a, b):
    """Two transcripts of one utterance? Either may be the other cut short
    by a faster endpoint."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    if len(short) >= CONTAINED and short in long_:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= SAME


def clear():
    """Tests only."""
    with _cv:
        _recent.clear()
        _cv.notify_all()
