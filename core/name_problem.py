"""core/name_problem.py — a name check's refusal: its words for the person, and
a code every caller branches on. Callers used to pick 409 vs 400 (and import
keep-vs-refuse) by matching the wording — rewording a message silently changed
the behavior (ultrareview 2026-11-08). A Problem IS its words (a str), so
callers that only show it are unchanged."""

TAKEN = 'taken'              # the name is held: user, pack, or unlocked vault
ASLEEP = 'asleep'            # held by a prompt asleep in the locked vault
INVALID = 'invalid'          # shape, reserved, or an unknown type
PERSONA_TAKEN = 'persona_taken'


class Problem(str):
    code = INVALID

    def __new__(cls, words, code=INVALID):
        p = super().__new__(cls, words)
        p.code = code
        return p

    @property
    def status(self) -> int:
        return 409 if self.code in (TAKEN, ASLEEP, PERSONA_TAKEN) else 400
