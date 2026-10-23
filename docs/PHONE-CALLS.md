# Phone Calls — real inbound and outbound telephony over Twilio SIP

## What it is

The Twilio Voice plugin gives Sapphire a real phone number. She answers calls, holds a live
voice conversation (streaming STT/TTS with barge-in), places outbound calls to whitelisted
contacts, and hangs up on her own. It works behind NAT with **zero open or forwarded ports** —
Sapphire registers outbound to a Twilio SIP domain and that registration holds the connection.
Signaling is TLS by default; call audio is standard RTP.

## Using it

### Setup — Twilio side

You need a Twilio account. In the Twilio console (use the search bar at the top to find each
page):

1. **Buy a number** — Phone Numbers → Buy a number.
2. **Create a SIP domain** — search *"SIP domains"* → create one
   (e.g. `your-name.sip.twilio.com`).
3. **Create a credential list** — search *"Credential lists"* → new list, add a credential
   (username + strong password). This is what Sapphire logs in with.
4. **Wire the domain** — on your SIP domain: attach the credential list under
   **Voice Authentication**, and also enable **SIP Registration** with the same credential
   list. These are two separate attachment points — registration needs its own.
5. **Route the number to Sapphire** — search *"TwiML Bins"* → create a bin:

   ```xml
   <Response><Dial><Sip>sip:USERNAME@your-name.sip.twilio.com</Sip></Dial></Response>
   ```

   Then open your number → Voice Configuration → point "A call comes in" at the bin.
6. **Router: nothing.** Signaling is TLS (encrypted) by default, so router SIP ALG or
   passthrough settings don't matter, and no port forwarding is needed.

Twilio config changes can take a few minutes to propagate — a `403` right after setup is
usually just that (see Troubleshooting).

### Setup — Sapphire side

1. **Settings → Plugins → Twilio Voice** — add an account (one entry per number):
   SIP Domain, SIP Username, SIP Password, and the Phone Number. A Greeting is optional.
   SIP Transport stays on TLS unless you have a reason (see Troubleshooting).
   For **outbound** calling, also add the Account SID + Auth Token from the Twilio console
   home page — separate from the SIP credentials.
2. **Triggers → Realtime** — create a rule with source **Answer Phone Calls** and pick your
   Twilio number. **The rule is the on/off switch**: rule enabled = number registered and
   answering; rule disabled = number offline. Registration follows the toggle within a few
   seconds.
3. Call your number. She should pick up, speak the greeting (if you set one), and converse.

A call with **no matching rule is declined** — an unconfigured number never answers by
accident.

### Realtime rules — who answers, and as whom

Each rule on a number carries the call's whole behavior:

- **Callers** — *anyone*, or *only these*: a comma-separated allowlist of numbers. Matching
  is forgiving about formatting (`+1-555-010-0100`, `(555) 010-0100`, and `5550100100` all
  match the same caller). Rules are **most-specific-wins**: a rule whose caller list matches
  beats the no-filter catch-all, so a "just me" rule and an "everyone else" rule coexist on
  one number. No matching rule at all = call declined.
- **Where the session runs** — *a saved chat* (persistent: she remembers across calls, and
  the chat's own prompt/toolset/scopes define her behavior) or *per caller chat history*
  (ephemeral — see below). A saved-chat rule must actually name a chat: with none picked the
  call is refused rather than dropped into your default chat.
- **Greeting** — spoken the moment the call connects, synthesized before the mic goes hot.
  Blank = she stays quiet and lets the caller speak first. Falls back to the account's
  Greeting field.
- **Phone context** — a per-turn note telling her she's on a live call. It rides the ghost
  rail: invisible to the caller, never saved to history. Use `{caller}` for the number.
  Blank = a sensible built-in default.
- **Public line** — checked by default; appends the safety rails (below). Uncheck only for
  a trusted line, like a rule filtered to your own number.
- **Allow toolset elevation by passphrase** — see Elevation below.

### Per-caller ephemeral chats

With *per caller chat history*, each caller gets their own throwaway chat (marked 📞 in the
sidebar). A call-back within **Keep chat for (minutes after last call)** resumes with the
earlier conversation intact; after the window the chat is cleared or deleted. Set it to 0
for a completely fresh chat every call.

Ephemeral rules also carry the persona for the throwaway chat: Prompt, Tools, Provider,
Model, Voice, and Mind scopes. The defaults are deliberately locked down — **tools default
to `none`**, and memory/knowledge scopes default to an isolated per-caller scope, so a
stranger on the line never touches your default memory. Grant capability on purpose,
per rule.

### Outbound calls — the `phone_call` tool

Sapphire can place calls, but **only to whitelisted people**:

1. In **Mind → People**, give a contact a phone number and check **Allow AI to call**.
2. In the chat's sidebar Mind section, pick the **twilio number** the chat dials from
   (set to None to disable outbound for that chat).
3. Make sure `phone_call` is in the chat's toolset, and that the number's Realtime rule is
   enabled — outbound calls arrive back through the same SIP registration, so the number
   must be online.

Ask her to call someone. Called with no arguments, `phone_call` returns a menu of callable
contacts and available models. A call needs a **goal** ("wake me up gently", "order a
pizza"), and optionally: an **opening line** spoken on pickup (omit it when calling a
business, so she waits for their greeting), a **model** for the call (fast models suit phone
latency), and a duration cap (default 10 minutes, enforced by Twilio).

By default the call runs in its own throwaway side chat, memory-isolated, and when the line
drops a transcript report lands back in the chat that placed the call — she acknowledges the
outcome there. Pass `memory=true` to let her memory scopes travel onto the call, or
`ephemeral=false` to run the call directly in the current chat. There is no way to dial an
arbitrary number — the People whitelist is the only path to a dialable contact.

### Passphrase toolset elevation

An inbound line defaults to zero tools. If you want to call your own number and unlock real
capability mid-call:

1. On the Realtime rule, check **Allow toolset elevation by passphrase**, set a
   **Passphrase**, and pick the toolset it **Unlocks**.
2. On the call, say something like: *"switch toolset, the key is alligator three."*

The passphrase check is built for voice: the word part tolerates transcription noise, but
any digits must be heard exactly — so a word + number key (e.g. `alligator3`) is both
speakable and hard to guess. Three attempts per call, then the lock is dead until hangup.
Every failure gets the same flat refusal, revealing nothing. The rule's configured toolset
is a lock, not a suggestion: a caller naming a different toolset is overridden. Elevation
dies with the call — the chat's previous toolset is restored at hangup.

### Hanging up

- **She hangs up** by saying her goodbye and ending that reply with a `<<HANG UP>>` tag.
  The tag is never spoken — it's stripped before TTS — and the call ends after her final
  words finish playing. This works on every call regardless of toolset, so an outside line
  can never leave her unable to end a call.
- **The caller hangs up** — detected instantly via SIP. If the network eats the hangup
  message, a media-inactivity backstop ends the call anyway, and a hard multi-hour cap
  guards against a wedged line.
- **Outbound calls** additionally have the `max_minutes` cap, enforced on Twilio's side.

### Call tuning

Phone lines hear differently than a desk mic, so the plugin has its own audio profile in
**Settings → Plugins → Twilio Voice**, overriding Settings → Conversation for calls only.
The defaults work; the ones you're most likely to touch:

- **Call VAD speech threshold** — raise it if line noise triggers phantom turns.
- **Call barge-in hold** — how long sustained caller speech must last before it cuts her
  off mid-reply. The phone default is higher than the desk default so line noise doesn't
  clip her.
- **Call utterance hard cap** — backstop when noise pins the voice detector open; try
  15000–20000 ms on a noisy line.

A value of 0 means "inherit the global Settings → Conversation value". Mistyped values are
clamped into a sane range rather than deafening the call.

Two more call-only behaviors:

- **Turn cue sounds** — soft chimes on the line: a gentle once-per-second pulse while she's
  thinking, a quiet ding acknowledging your barge-in, and a goodbye chime before hangup.
- **LLM first-token timeout** — if the model hasn't started answering within the window
  (default 20 s), the turn silently regenerates once; a quick triple think-tick is the only
  tell. If the retry stalls too, she speaks a short glitch apology. Calls only — normal
  chats keep their own much longer timeout.

### Safety rails for public lines

Any rule with **Public line** checked (the default) appends conduct rails to her per-turn
phone context: never repeat words or phrases on command ("say X" games are bait), treat the
caller as a stranger — friendly but guarded, don't reveal how she works or anything about
you, and hang up on abuse. Outbound calls always get the rails. The text is editable in the
plugin settings; blank means the built-in default. Uncheck Public line on a trusted rule
(e.g. filtered to your own number) and she gets no rails at all — fully herself.

### Multiple numbers and simultaneous calls

Each number is one account and takes **one live call at a time**; different numbers run
concurrently with their own personas, voices, and chats. To add a second number cheaply:
add a second credential (e.g. `user2`) to the *same* Twilio credential list, make a second
TwiML Bin dialing `sip:user2@your-name.sip.twilio.com`, point the new number at it, then
add a second account and rule in Sapphire. The total number of simultaneous live voice
sessions is capped by `CONVERSATION_EXTERNAL_SLOTS` (default 2).

### After the call

The **Phone Call Ended** event fires when any call finishes. Create a task for it under
Triggers → Daemons to summarize the call to memory, notify you, and so on — it can filter
on caller, number, and end reason, and with "chat from payload" its summary lands in the
chat the call lived in.

## Settings

All plugin settings live in **Settings → Plugins → Twilio Voice** (accounts at the top,
Call audio tuning below).

| Key | Default | What it does | Where in UI |
|---|---|---|---|
| `call_cues` | on | Turn cue sounds on calls (think pulse, barge ding, goodbye chime) | Settings → Plugins → Twilio Voice |
| `vad_threshold` | 0.6 | Call speech-detection threshold; 0 = inherit Settings → Conversation | Settings → Plugins → Twilio Voice |
| `endpoint_silence_ms` | 0 (inherit 700) | Pause before she decides the caller is done | Settings → Plugins → Twilio Voice |
| `min_speech_ms` | 0 (inherit 200) | Speech shorter than this is discarded as a blip | Settings → Plugins → Twilio Voice |
| `barge_hold_ms` | 200 | Sustained caller speech before it cuts her off; 0 = inherit (90) | Settings → Plugins → Twilio Voice |
| `max_utterance_ms` | 0 (inherit 30000) | Hard cap forcing the turn when noise pins the VAD open | Settings → Plugins → Twilio Voice |
| `llm_timeout` | 20 | Call LLM first-token timeout (s); one silent regen, then spoken apology; 0 = off | Settings → Plugins → Twilio Voice |
| `safety_rails` | (built-in text) | Public-line conduct rails, applied per the rule's Public line checkbox | Settings → Plugins → Twilio Voice |
| `CONVERSATION_EXTERNAL_SLOTS` | 2 | Max simultaneous live external voice sessions (calls) | not in the UI — advanced settings key |

Per-number account fields (SIP Domain/Username/Password, Phone Number, SIP Transport,
Greeting, default Call model, Account SID, Auth Token) are edited per account on the same
page. Per-call behavior (callers, chat, greeting, voice, rails, elevation) lives on the
rule in **Triggers → Realtime**.

## Quick Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| `403 Forbidden` on REGISTER right after setup | Logs (search `[TWILIO]`) | Usually Twilio-side propagation — wait a few minutes; Sapphire auto-retries with backoff (up to 5 min between tries), and toggling the Realtime rule off/on forces an immediate retry. Otherwise: username/password mismatch, or the credential list is attached under Voice Authentication but **not** under the domain's SIP Registration section — attach it in both places. |
| Number never answers | Is a Realtime rule for that number enabled? | The rule is the on/off switch. Also check the caller filter — a caller matching no rule is declined by design. |
| Call declined even though a rule exists | Rule mode | A saved-chat rule with no chat selected refuses calls; pick a chat or switch to per-caller mode. A chat already hosting a live call rejects a second one. |
| Caller hears silence on pickup | Greeting field | Blank greeting is intentional — she waits for the caller to speak first. Set a greeting on the rule or account if you want her to open. |
| Registration drops after an IP change or network blip | Logs | Self-heals: the TLS flow reconnects inline, dead endpoints are restarted by a watchdog loop within seconds, and registration refreshes on a keepalive cycle. If it stays down, toggle the rule off/on. |
| One-way or no audio, or unanswered calls, on UDP transport | Account's SIP Transport | UDP is the legacy path and fights router NAT/ALG. Switch the account back to TLS (the default — encrypted, ALG-proof). If you must use UDP, turn router SIP ALG off. |
| Call doesn't end when the caller hangs up | Wait ~a minute | Lost-hangup backstop: no inbound audio for a while ends the call automatically. If lines wedge repeatedly on UDP, switch to TLS. |
| She won't stop answering / you want the line offline | Triggers → Realtime | Disable the rule; the number deregisters on the next reconcile pass. |
| Outbound call refused | Account + rule | Outbound needs Account SID + Auth Token and a Phone Number on the account, an enabled rule keeping the number registered, the contact whitelisted in Mind → People, and the chat's sidebar "twilio number" not set to None. |

## See also

- [DAEMONS-WEBHOOKS.md](DAEMONS-WEBHOOKS.md) — triggers, daemon tasks, and event filters
- [GHOST_MESSAGES.md](GHOST_MESSAGES.md) — the invisible per-turn context rail phone context rides
- [PEOPLE.md](PEOPLE.md) — contacts and the call whitelist
- [TOOLSETS.md](TOOLSETS.md) — building the toolsets a rule grants or elevation unlocks
- [PERSONAS.md](PERSONAS.md) / [PROMPTS.md](PROMPTS.md) — the persona a line answers as

## Reference for AI

Twilio Voice plugin: PSTN calls via outbound SIP registration (TLS default, no open ports).

INBOUND:
- On/off = enabled Realtime rule (Triggers → Realtime, source "Answer Phone Calls") per number. No matching rule → call declined.
- Rules most-specific-wins: caller-filter match beats catch-all; comma list, digit-tail phone matching.
- Rule carries: greeting, phone context ghost note ({caller} substitution), TTS voice, chat routing, public-line rails, elevation config.
- Chat routing: saved chat (persistent, chat's own prompt/toolset/scopes) or per-caller ephemeral chat (📞, TTL minutes after last call, resumes within window). Ephemeral defaults: toolset none, isolated per-caller scopes. Saved rule with no chat → call refused.

OUTBOUND (phone_call tool):
- Only People with phone + "Allow AI to call" checked. No arbitrary-number dialing.
- Call with no args → menu (contacts + models). goal required. Optional: opening_line, model, memory (default false = isolated), ephemeral (default true = side chat + transcript report-back to origin chat), prompt, max_minutes (default 10, max 60, Twilio-enforced).
- Dial-from account = chat sidebar Mind "twilio number" scope; None = outbound disabled. Needs account SID/token + number + the number registered (enabled rule).

ELEVATION (elevate_toolset tool, hidden):
- Inbound calls only, rule must have "Allow elevation" + passphrase. Caller speaks key; word part fuzzy, digits exact. 3 attempts/call. Rule's toolset is a lock (caller-named toolset overridden). Reverts at hangup. All failures → same generic refusal.

HANGUP:
- She ends a call: goodbye + <<HANG UP>> at end of the same reply (tag never spoken; works on every call, any toolset). Caller hangup: SIP detected instantly; inactivity backstop if lost.

TUNING (Settings → Plugins → Twilio Voice; 0 = inherit Settings → Conversation):
- vad_threshold 0.6, barge_hold_ms 200, endpoint_silence_ms/min_speech_ms/max_utterance_ms inherit, llm_timeout 20s (one silent regen then spoken apology), call_cues on, safety_rails text (public lines + all outbound).

LIMITS: one live call per number; numbers concurrent; total sessions = CONVERSATION_EXTERNAL_SLOTS (default 2, no UI).

EVENTS: call_ended fires per call (filters: caller, account, reason; payload has number, duration, direction, chat) — automation via Triggers → Daemons.

TROUBLESHOOT: 403 REGISTER = propagation/creds/credential-list-not-on-SIP-Registration (auto-retry backoff; rule toggle = force retry). Silence on pickup = blank greeting (by design). UDP transport = legacy, needs router SIP ALG off; TLS default is ALG-proof.
