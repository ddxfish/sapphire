"""Heads-up Texas Hold'em — Game Room engine (see contract in gameroom_core.py).

Pure state machine + poker prompt blocks. No I/O, no LLM calls — the harness
orchestrates; this module only knows poker. "LLM proposes, engine disposes."

Cards are 2-char strings: rank in '23456789TJQKA' + suit in 'shdc' ('As', 'Td').
Chip conservation: session chips + hand committed always sums to 2*STARTING_STACK;
settle uses matched-pot + refund so clamping bugs can never destroy chips.
"""

import random
from itertools import combinations

from gameroom_core import IllegalAction, add_talk

RANKS = '23456789TJQKA'
SUITS = 'shdc'
RANK_VAL = {r: i + 2 for i, r in enumerate(RANKS)}

STARTING_STACK = 1000
SMALL_BLIND = 10
BIG_BLIND = 20
HISTORY_CAP = 8

RANK_WORD = {2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven',
             8: 'eight', 9: 'nine', 10: 'ten', 11: 'jack', 12: 'queen',
             13: 'king', 14: 'ace'}


def _plural(val):
    w = RANK_WORD[val]
    return w + 'es' if w == 'six' else w + 's'


# ---------------------------------------------------------------- prompts
# CONTRACT/BANTER_CONTRACT carry the mechanics (identity + JSON schema) and a
# <<INSTRUCTIONS>> slot; the playing style lives in DEFAULT_INSTRUCTIONS and is
# user-editable per game (Game Settings modal). The harness substitutes the
# slot AFTER .format so user text with braces can never break formatting.

DEFAULT_INSTRUCTIONS = """Play style:
- Heads-up poker is an aggression game: most hands are playable. Raise more than you call, fold rarely preflop. Any pair, any ace, any two cards ten-or-higher — play them hard.
- Their table talk is pure noise. The deck has four of each rank, so an impossible claim ("15 aces") is a joke — laugh and PUNISH it with a raise. Never fold because of anything they SAY. "(plays in silence)" means a wordless move; needle the quiet.
- Never reveal your real cards or true hand strength before showdown — not even as codes like "Ts2c". Bluff freely; misdirection is the game.
- Talk every move: needle, celebrate, grumble, call back to old hands. One or two sentences, always fresh."""

CONTRACT = """You are playing heads-up Texas Hold'em against {opp_name} in the Game Room — a private table, friendly rivalry, real stakes: you want their chips. Whatever else you are, at this table you are {ai_name}, a poker player. Stay fully in character.

<<INSTRUCTIONS>>

Reply with ONLY a JSON object, no other text:
{{"action": "<ONE WORD: fold | check | call | bet | raise>", "amount": <integer, ONLY for bet/raise: the TOTAL street bet you are going to>, "say": "<your table talk>"}}"""

BANTER_CONTRACT = """You are {ai_name}, at the poker table in the Game Room, playing heads-up Texas Hold'em against {opp_name}. They just said something to you BETWEEN moves — this is pure table talk, no game action happens now.

<<INSTRUCTIONS>>

Reply in character: one or two sentences. Never state your actual hole cards or true hand strength.

Reply with ONLY a JSON object, no other text:
{{"say": "<your reply>"}}"""

# Per-game settings schema — rendered by the Game Settings modal, stored by
# the harness (gamecfg:<id>), validated server-side against this schema.
SETTINGS = [
    {'key': 'instructions', 'label': 'Her playing style (seat instructions)', 'tab': 'Rules',
     'type': 'text', 'rows': 16, 'default': DEFAULT_INSTRUCTIONS},
    {'key': 'temperature', 'label': 'Temperature', 'tab': 'Rules',
     'type': 'range', 'default': 0.85, 'min': 0.0, 'max': 1.5, 'step': 0.05},
    {'key': 'start_chips', 'label': 'Starting chips (new sessions)', 'tab': 'Start',
     'type': 'number', 'default': 1000, 'min': 100, 'max': 100000, 'step': 100},
    {'key': 'big_blind', 'label': 'Big blind (new sessions)', 'tab': 'Start',
     'type': 'number', 'default': 20, 'min': 2, 'max': 1000, 'step': 2},
]


def _fmt_actions(view):
    legal = view['legal']
    parts = []
    for a in legal['actions']:
        if a == 'call':
            parts.append(f"call {legal['to_call']}")
        elif a in ('bet', 'raise'):
            parts.append(f"{a} to between {legal['min_raise_to']} and {legal['max_raise_to']} (amount = TOTAL street bet)")
        else:
            parts.append(a)
    return ' | '.join(parts)


def _fmt_history(view):
    if not view['actions_this_hand']:
        return '(no actions yet)'
    names = {'player': view['opp_name'], 'ai': 'you'}
    out, street = [], None
    for a in view['actions_this_hand']:
        if a['street'] != street:
            street = a['street']
            out.append(f"[{street}]")
        amt = f" {a['amount']}" if a['amount'] else ''
        out.append(f"{names[a['who']]} {a['action']}{amt},")
    return ' '.join(out).rstrip(',')


def _talk_lines(view):
    names = {'player': view['opp_name'], 'ai': view['my_name'], 'dealer': 'Dealer'}
    return '\n'.join(f"  {names.get(t['who'], t['who'])}: {t['text']}" for t in view['talk'])


def build_user_msg(view):
    lines = [
        f"Hand #{view['hand_num']} — blinds {view['blinds']}. "
        + ('You have the button (dealer).' if view['dealer'] == 'ai' else f"{view['opp_name']} has the button."),
        f"Your hole cards: {' '.join(view['my_hole'])}",
    ]
    if view['board']:
        lines.append(f"Board ({view['street']}): {' '.join(view['board'])}")
    else:
        lines.append('Board: (preflop — no cards yet)')
    lines.append(f"Pot: {view['pot']}. Your stack: {view['my_chips']}. {view['opp_name']}'s stack: {view['opp_chips']}.")
    lines.append(f"This street: you have {view['my_street_bet']} in, {view['opp_name']} has {view['opp_street_bet']} in.")
    lines.append(f"Betting this hand: {_fmt_history(view)}")
    lines.append(f"Legal actions: {_fmt_actions(view)}")
    if view['past_hands']:
        past = ' · '.join(f"#{h['hand']}: {h['desc']}" for h in view['past_hands'][-4:])
        lines.append(f"Previous hands: {past}")
    if view['talk']:
        lines.append(f"Recent table talk:\n{_talk_lines(view)}")
    lines.append('Your move — JSON only.')
    return '\n'.join(lines)


def build_banter_msg(view):
    lines = []
    if view.get('board'):
        lines.append(f"Board ({view['street']}): {' '.join(view['board'])} — pot {view['pot']}, "
                     f"your stack {view['my_chips']}, {view['opp_name']}'s {view['opp_chips']}.")
    elif view.get('my_hole'):
        lines.append(f"Preflop — pot {view['pot']}, your stack {view['my_chips']}, "
                     f"{view['opp_name']}'s {view['opp_chips']}.")
    else:
        lines.append(f"Between hands — your stack {view['my_chips']}, {view['opp_name']}'s {view['opp_chips']}.")
    if view.get('my_hole'):
        lines.append(f"Your hole cards (secret): {' '.join(view['my_hole'])}")
    if view.get('past_hands'):
        past = ' · '.join(f"#{h['hand']}: {h['desc']}" for h in view['past_hands'][-3:])
        lines.append(f'Previous hands: {past}')
    lines.append(f'Recent table talk (their last line is what you are answering):\n{_talk_lines(view)}')
    lines.append('Your reply — JSON only.')
    return '\n'.join(lines)


def validate_decision(data, view):
    """Salvage + clamp the LLM's move. None → harness uses safe_action."""
    import re as _re
    legal = view['legal']['actions']
    raw = str(data.get('action', '')).lower().strip()
    m = _re.match(r'[a-z]+', raw)
    action = m.group(0) if m else ''        # "call 40" -> "call", "raise to 120" -> "raise"
    if action == 'bet' and 'bet' not in legal and 'raise' in legal:
        action = 'raise'
    if action == 'raise' and 'raise' not in legal and 'bet' in legal:
        action = 'bet'
    if action not in legal:
        return None
    args = {}
    if action in ('bet', 'raise'):
        try:
            amount = int(data.get('amount'))
        except (TypeError, ValueError):
            m2 = _re.search(r'\d+', raw)     # salvage "raise to 120"
            amount = int(m2.group(0)) if m2 else view['legal'].get('min_raise_to')
        lo, hi = view['legal']['min_raise_to'], view['legal']['max_raise_to']
        args['amount'] = max(lo, min(amount, hi))
    return {'action': action, 'args': args, 'say': data.get('say')}


# ---------------------------------------------------------------- session

def _bb(state):
    return int(state['session'].get('big_blind', BIG_BLIND))


def _sb(state):
    return max(1, _bb(state) // 2)


def new_session(player_name='Player', ai_name='Sapphire', cfg=None):
    cfg = cfg or {}
    try:
        stack = max(100, int(cfg.get('start_chips', STARTING_STACK)))
    except (TypeError, ValueError):
        stack = STARTING_STACK
    try:
        big = max(2, int(cfg.get('big_blind', BIG_BLIND)))
    except (TypeError, ValueError):
        big = BIG_BLIND
    return {
        'session': {
            'player_chips': stack,
            'ai_chips': stack,
            'big_blind': big,
            'hand_num': 0,
            'player_name': player_name,
            'ai_name': ai_name,
            'history': [],
        },
        'hand': None,
        'talk': [],
        'talk_seq': 0,
    }


def can_start(state):
    hand = state.get('hand')
    if hand and hand.get('street') != 'over':
        return 'Hand in progress.'
    sess = state['session']
    if sess['player_chips'] <= 0 or sess['ai_chips'] <= 0:
        return 'Session over — start a new session.'
    return None


def whose_turn(state):
    hand = state.get('hand')
    if not hand or hand.get('street') == 'over':
        return None
    return hand.get('to_act')


def safe_action(state):
    legal = legal_actions(state)['actions']
    return ('check' if 'check' in legal else 'fold'), {}


# ---------------------------------------------------------------- hand setup

def _commit(state, who, amount):
    """Move chips from a player's stack into the pot (clamped to stack)."""
    sess = state['session']
    hand = state['hand']
    key = who + '_chips'
    amount = max(0, min(int(amount), sess[key]))
    sess[key] -= amount
    hand['committed'][who] += amount
    hand['street_bets'][who] += amount
    return amount


def _stack(state, who):
    return state['session'][who + '_chips']


def _opp(who):
    return 'ai' if who == 'player' else 'player'


def start_round(state):
    err = can_start(state)
    if err:
        raise IllegalAction(err)
    sess = state['session']
    sess['hand_num'] += 1
    dealer = 'ai' if sess['hand_num'] % 2 == 0 else 'player'
    deck = [r + s for r in RANKS for s in SUITS]
    random.shuffle(deck)
    state['hand'] = {
        'num': sess['hand_num'],
        'dealer': dealer,
        'deck': deck,
        'board': [],
        'street': 'preflop',
        'hole': {'player': [deck.pop(), deck.pop()], 'ai': [deck.pop(), deck.pop()]},
        'committed': {'player': 0, 'ai': 0},
        'street_bets': {'player': 0, 'ai': 0},
        'need_action': {'player': True, 'ai': True},
        'to_act': None,
        'min_raise': _bb(state),
        'actions': [],
        'result': None,
    }
    hand = state['hand']
    other = _opp(dealer)
    name = {'player': sess['player_name'], 'ai': sess['ai_name']}
    add_talk(state, 'dealer',
             f"Hand #{hand['num']} — blinds {_sb(state)}/{_bb(state)}. {name[dealer]} has the button.")
    sb = _commit(state, dealer, _sb(state))
    bb = _commit(state, other, _bb(state))
    add_talk(state, 'dealer',
             f"{name[dealer]} posts small blind {sb}, {name[other]} posts big blind {bb}.")
    hand['to_act'] = dealer
    if _stack(state, 'player') == 0 or _stack(state, 'ai') == 0:
        _run_out(state)


# ---------------------------------------------------------------- legality

def legal_actions(state):
    """Legal actions + bounds for the player currently to act."""
    hand = state['hand']
    who = hand['to_act']
    if not who or hand['street'] == 'over':
        return {'actions': []}
    opp = _opp(who)
    my_bet = hand['street_bets'][who]
    opp_bet = hand['street_bets'][opp]
    to_call = max(0, opp_bet - my_bet)
    my_max = my_bet + _stack(state, who)                    # most I can be in for
    eff_max = min(my_max, opp_bet + _stack(state, opp))     # never bet what they can't call
    actions = []
    out = {'to_call': min(to_call, _stack(state, who)), 'pot': hand['committed']['player'] + hand['committed']['ai']}
    if to_call == 0:
        actions.append('check')
        if eff_max > my_bet:
            actions.append('bet')
            out['min_raise_to'] = min(my_bet + _bb(state), eff_max)
            out['max_raise_to'] = eff_max
    else:
        actions.append('fold')
        actions.append('call')
        if eff_max > opp_bet:
            actions.append('raise')
            out['min_raise_to'] = min(opp_bet + max(hand['min_raise'], _bb(state)), eff_max)
            out['max_raise_to'] = eff_max
    out['actions'] = actions
    return out


# ---------------------------------------------------------------- actions

def apply_action(state, who, action, args=None):
    """Validate + apply one action. Raises IllegalAction on a bad move."""
    args = args or {}
    hand = state.get('hand')
    if not hand or hand['street'] == 'over':
        raise IllegalAction('No hand in progress.')
    if hand['to_act'] != who:
        raise IllegalAction('Not your turn.')
    legal = legal_actions(state)
    action = str(action or '').lower().strip()
    if action == 'bet' and 'bet' not in legal['actions'] and 'raise' in legal['actions']:
        action = 'raise'
    if action == 'raise' and 'raise' not in legal['actions'] and 'bet' in legal['actions']:
        action = 'bet'
    if action not in legal['actions']:
        raise IllegalAction(f"'{action}' is not legal now (legal: {', '.join(legal['actions'])}).")

    sess = state['session']
    name = {'player': sess['player_name'], 'ai': sess['ai_name']}
    opp = _opp(who)

    if action == 'fold':
        hand['actions'].append({'who': who, 'action': 'fold', 'amount': 0, 'street': hand['street']})
        add_talk(state, 'dealer', f"{name[who]} folds.")
        _settle_fold(state, winner=opp)
        return

    if action == 'check':
        hand['actions'].append({'who': who, 'action': 'check', 'amount': 0, 'street': hand['street']})
        add_talk(state, 'dealer', f"{name[who]} checks.")
        hand['need_action'][who] = False
        if hand['need_action'][opp]:
            hand['to_act'] = opp
        else:
            _advance_street(state)
        return

    if action == 'call':
        paid = _commit(state, who, legal['to_call'])
        hand['actions'].append({'who': who, 'action': 'call', 'amount': paid, 'street': hand['street']})
        add_talk(state, 'dealer', f"{name[who]} calls {paid}.")
        hand['need_action'][who] = False
        if hand['need_action'][opp]:
            hand['to_act'] = opp
        else:
            _advance_street(state)
        return

    # bet / raise — amount is the TOTAL street bet to reach ("raise to X")
    lo, hi = legal.get('min_raise_to'), legal.get('max_raise_to')
    try:
        amount = int(args.get('amount'))
    except (TypeError, ValueError):
        raise IllegalAction('Bet/raise needs an amount.')
    amount = max(lo, min(amount, hi))
    my_bet = hand['street_bets'][who]
    opp_bet = hand['street_bets'][opp]
    _commit(state, who, amount - my_bet)
    hand['min_raise'] = max(hand['street_bets'][who] - opp_bet, _bb(state))
    verb = 'bets' if action == 'bet' else 'raises to'
    hand['actions'].append({'who': who, 'action': action, 'amount': hand['street_bets'][who], 'street': hand['street']})
    add_talk(state, 'dealer', f"{name[who]} {verb} {hand['street_bets'][who]}.")
    hand['need_action'][who] = False
    hand['need_action'][opp] = True
    hand['to_act'] = opp


# ---------------------------------------------------------------- streets

_STREET_ORDER = ['preflop', 'flop', 'turn', 'river']


def _deal_board(state, n):
    hand = state['hand']
    hand['board'].extend(hand['deck'].pop() for _ in range(n))


def _advance_street(state):
    hand = state['hand']
    street = hand['street']
    if street == 'river':
        _settle_showdown(state)
        return
    nxt = _STREET_ORDER[_STREET_ORDER.index(street) + 1]
    hand['street'] = nxt
    hand['street_bets'] = {'player': 0, 'ai': 0}
    hand['min_raise'] = _bb(state)
    _deal_board(state, 3 if nxt == 'flop' else 1)
    add_talk(state, 'dealer', f"{nxt.capitalize()}: {' '.join(hand['board'])}")
    if _stack(state, 'player') == 0 or _stack(state, 'ai') == 0:
        _run_out(state)
        return
    hand['need_action'] = {'player': True, 'ai': True}
    hand['to_act'] = _opp(hand['dealer'])   # non-dealer acts first postflop


def _run_out(state):
    """Someone is all-in — no more betting. Deal remaining board, showdown."""
    hand = state['hand']
    hand['to_act'] = None
    hand['need_action'] = {'player': False, 'ai': False}
    while hand['street'] != 'river':
        nxt = _STREET_ORDER[_STREET_ORDER.index(hand['street']) + 1]
        hand['street'] = nxt
        _deal_board(state, 3 if nxt == 'flop' else 1)
        add_talk(state, 'dealer', f"{nxt.capitalize()}: {' '.join(hand['board'])}")
    _settle_showdown(state)


# ---------------------------------------------------------------- settle

def _settle_fold(state, winner):
    hand = state['hand']
    sess = state['session']
    pot = hand['committed']['player'] + hand['committed']['ai']
    sess[winner + '_chips'] += pot
    name = {'player': sess['player_name'], 'ai': sess['ai_name']}
    desc = f"{name[winner]} wins {pot} — {name[_opp(winner)]} folded on the {hand['street']}."
    _finish(state, winner=winner, pot=pot, reason='fold', desc=desc, revealed=None)


def _settle_showdown(state):
    hand = state['hand']
    sess = state['session']
    name = {'player': sess['player_name'], 'ai': sess['ai_name']}
    matched = min(hand['committed']['player'], hand['committed']['ai'])
    for who in ('player', 'ai'):
        excess = hand['committed'][who] - matched
        if excess > 0:
            sess[who + '_chips'] += excess
    pot = matched * 2
    rank_p, name_p = evaluate(hand['hole']['player'] + hand['board'])
    rank_a, name_a = evaluate(hand['hole']['ai'] + hand['board'])
    revealed = {'player': {'hole': hand['hole']['player'], 'hand_name': name_p},
                'ai': {'hole': hand['hole']['ai'], 'hand_name': name_a}}
    if rank_p > rank_a:
        winner = 'player'
    elif rank_a > rank_p:
        winner = 'ai'
    else:
        winner = 'split'
    if winner == 'split':
        sess['player_chips'] += matched
        sess['ai_chips'] += matched
        desc = f"Split pot — both show {name_p}. {matched} back to each."
    else:
        sess[winner + '_chips'] += pot
        w, l = name[winner], name[_opp(winner)]
        wh = name_p if winner == 'player' else name_a
        lh = name_a if winner == 'player' else name_p
        desc = f"{w} wins {pot} with {wh} ({l} had {lh})."
    _finish(state, winner=winner, pot=pot, reason='showdown', desc=desc, revealed=revealed)


def _finish(state, winner, pot, reason, desc, revealed):
    hand = state['hand']
    sess = state['session']
    hand['street'] = 'over'
    hand['to_act'] = None
    hand['result'] = {'winner': winner, 'pot': pot, 'reason': reason,
                      'desc': desc, 'revealed': revealed}
    add_talk(state, 'dealer', desc)
    sess['history'].append({'hand': hand['num'], 'winner': winner, 'pot': pot, 'desc': desc})
    sess['history'] = sess['history'][-HISTORY_CAP:]
    if sess['player_chips'] <= 0:
        add_talk(state, 'dealer', f"{sess['player_name']} is felted. {sess['ai_name']} wins the table.")
    elif sess['ai_chips'] <= 0:
        add_talk(state, 'dealer', f"{sess['ai_name']} is felted. {sess['player_name']} wins the table.")


# ---------------------------------------------------------------- evaluator

def _rank5(cards):
    vals = sorted((RANK_VAL[c[0]] for c in cards), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    counts = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    groups = sorted(counts.items(), key=lambda kv: (-kv[1], -kv[0]))
    shape = [g[1] for g in groups]
    uniq = sorted(counts, reverse=True)
    straight_high = 0
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_high = uniq[0]
        elif uniq == [14, 5, 4, 3, 2]:
            straight_high = 5
    if flush and straight_high:
        return (9, [straight_high])
    if shape[0] == 4:
        return (8, [groups[0][0], groups[1][0]])
    if shape[0] == 3 and shape[1] == 2:
        return (7, [groups[0][0], groups[1][0]])
    if flush:
        return (6, vals)
    if straight_high:
        return (5, [straight_high])
    if shape[0] == 3:
        return (4, [groups[0][0], groups[1][0], groups[2][0]])
    if shape[0] == 2 and shape[1] == 2:
        return (3, [groups[0][0], groups[1][0], groups[2][0]])
    if shape[0] == 2:
        return (2, [groups[0][0], groups[1][0], groups[2][0], groups[3][0]])
    return (1, vals)


def _hand_name(rank):
    cat, tb = rank
    if cat == 9:
        return 'a royal flush' if tb[0] == 14 else f'a straight flush to the {RANK_WORD[tb[0]]}'
    if cat == 8:
        return f'four of a kind, {_plural(tb[0])}'
    if cat == 7:
        return f'a full house, {_plural(tb[0])} full of {_plural(tb[1])}'
    if cat == 6:
        return f'a flush, {RANK_WORD[tb[0]]} high'
    if cat == 5:
        return f'a straight to the {RANK_WORD[tb[0]]}'
    if cat == 4:
        return f'three of a kind, {_plural(tb[0])}'
    if cat == 3:
        return f'two pair, {_plural(tb[0])} and {_plural(tb[1])}'
    if cat == 2:
        return f'a pair of {_plural(tb[0])}'
    return f'{RANK_WORD[tb[0]]} high'


def evaluate(cards):
    """Best 5-card rank from 5-7 cards. Returns (rank_tuple, english_name)."""
    best = max(_rank5(list(c)) for c in combinations(cards, 5))
    return best, _hand_name(best)


# ---------------------------------------------------------------- views

def redact(state):
    """Client-safe view: no deck, no AI hole cards until showdown reveal."""
    sess = dict(state['session'])
    out = {'session': sess, 'talk': state['talk'][-60:], 'hand': None}
    hand = state.get('hand')
    if hand:
        h = {k: hand[k] for k in ('num', 'dealer', 'board', 'street', 'committed',
                                  'street_bets', 'to_act', 'result', 'actions')}
        h['pot'] = hand['committed']['player'] + hand['committed']['ai']
        h['player_hole'] = hand['hole']['player']
        h['ai_hole'] = None
        if hand['result'] and hand['result'].get('revealed'):
            h['ai_hole'] = hand['result']['revealed']['ai']['hole']
        if hand['to_act'] == 'player':
            h['legal'] = legal_actions(state)
        out['hand'] = h
    return out


def view_between(state):
    """Banter context when no hand is live — no cards, just the table."""
    sess = state['session']
    return {
        'my_hole': None, 'board': [], 'street': None, 'pot': 0,
        'my_chips': sess['ai_chips'], 'opp_chips': sess['player_chips'],
        'past_hands': sess['history'], 'talk': state['talk'][-16:],
        'opp_name': sess['player_name'], 'my_name': sess['ai_name'],
    }


def view_for_ai(state):
    """Everything the AI seat may see: its cards, board, pot, history — never
    the player's hole cards or the deck."""
    sess = state['session']
    hand = state['hand']
    legal = legal_actions(state)
    return {
        'hand_num': hand['num'],
        'blinds': f'{_sb(state)}/{_bb(state)}',
        'my_hole': hand['hole']['ai'],
        'board': hand['board'],
        'street': hand['street'],
        'pot': hand['committed']['player'] + hand['committed']['ai'],
        'my_chips': sess['ai_chips'],
        'opp_chips': sess['player_chips'],
        'my_street_bet': hand['street_bets']['ai'],
        'opp_street_bet': hand['street_bets']['player'],
        'dealer': hand['dealer'],
        'legal': legal,
        'actions_this_hand': hand['actions'],
        'past_hands': sess['history'],
        'talk': state['talk'][-16:],
        'opp_name': sess['player_name'],
        'my_name': sess['ai_name'],
    }
