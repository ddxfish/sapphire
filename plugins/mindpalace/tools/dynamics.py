# plugins/mindpalace/tools/dynamics.py
# Importance dynamics (2026-07-12) — nightly decay + reinforcement-on-recall.
#
# Observation-week contract (Krem's ruling): the numbers MOVE but nothing may
# newly MATTER. Enforced here in code, not prompt:
#   - NULL importance is never touched. Unrated stays unrated — initializing
#     it would silently change spider pricing, where NULL = neutral (1.0).
#   - The core band (>= 0.9) and favorites are exempt in both directions —
#     "0.9+ = core, never fades" is a promise made in the librarian pass.
#   - Boost ceiling 0.85: recall alone can never mint core status. Only she
#     (mark_processed) and favorites reach 0.9+.
#   - Decay floor 0.1: nothing fades to zero. Rows at/below the floor rest.
#   - `updated` is never bumped — dynamics are physiology, not edits, and
#     recency ordering stays honest. No ledger rows for the same reason.
#
# Master toggle (2026-07-15): the whole dynamics pair rides the
# `importance_enabled` alpha setting — off (the default) means _rates()
# returns 0/0 and neither half moves a number. The recall instrumentation
# (recall_count / last_recalled) stays on regardless: it's passive
# observability for the report tool, not an importance write.
#
# Rates live in plugin settings (importance_decay_per_night /
# importance_boost_per_recall); 0 disables either half. Raw observations land
# in chunks.recall_count / chunks.last_recalled for the report tool
# (tools/importance_report.py — Krem-facing CLI).

import logging

logger = logging.getLogger(__name__)

DECAY_FLOOR = 0.1
BOOST_CEILING = 0.85
CORE_BAND = 0.9


def _pt():
    from plugins.mindpalace.tools import palace_tools
    return palace_tools


def _rates():
    try:
        from core.plugin_loader import plugin_loader
        s = plugin_loader.get_plugin_settings('mindpalace')
    except Exception:
        s = {}
    # Alpha master toggle — fails toward OFF (silent-default invariant).
    if not s.get('importance_enabled'):
        return {'decay': 0.0, 'boost': 0.0}

    def f(key, default):
        try:
            return max(0.0, float(s.get(key, default)))
        except (TypeError, ValueError):
            return default
    return {'decay': f('importance_decay_per_night', 0.005),
            'boost': f('importance_boost_per_recall', 0.02)}


def decay_tick(scope):
    """One night of drift for a scope: rated, non-favorite chunks below the
    core band slide toward the floor. Returns rows touched (0 on disabled or
    error — never raises)."""
    rate = _rates()['decay']
    if not rate:
        return 0
    try:
        pt = _pt()
        with pt._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                'UPDATE chunks SET importance = MAX(?, importance - ?) '
                'WHERE scope = ? AND importance IS NOT NULL '
                'AND importance < ? AND importance > ? AND favorite = 0',
                (DECAY_FLOOR, rate, scope, CORE_BAND, DECAY_FLOOR))
            n = cur.rowcount
            conn.commit()
        return n
    except Exception as e:
        logger.warning(f"[MINDPALACE] Decay tick failed for '{scope}': {e}")
        return 0


def boost_recall(scope, chunk_ids):
    """Reinforcement for direct search hits. Two writes: the importance bump
    (day-gated per chunk, so a hot conversation can't pump one memory all
    afternoon), then instrumentation (recall_count/last_recalled — EVERY
    recall counts there). Never raises."""
    ids = [i for i in (chunk_ids or []) if i is not None]
    if not ids:
        return
    try:
        pt = _pt()
        rate = _rates()['boost']
        now = pt._now()
        ph = ','.join('?' * len(ids))
        with pt._get_connection() as conn:
            cur = conn.cursor()
            if rate:
                cur.execute(
                    f'UPDATE chunks SET importance = MIN(?, importance + ?) '
                    f'WHERE id IN ({ph}) AND scope = ? '
                    f'AND importance IS NOT NULL AND importance < ? '
                    f'AND favorite = 0 '
                    f'AND (last_recalled IS NULL OR substr(last_recalled, 1, 10) != ?)',
                    [BOOST_CEILING, rate, *ids, scope, BOOST_CEILING, now[:10]])
            cur.execute(
                f'UPDATE chunks SET recall_count = recall_count + 1, '
                f'last_recalled = ? WHERE id IN ({ph}) AND scope = ?',
                [now, *ids, scope])
            conn.commit()
    except Exception as e:
        logger.warning(f"[MINDPALACE] Recall boost skipped: {e}")
