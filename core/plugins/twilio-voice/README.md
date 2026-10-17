# Twilio Voice Plugin

Real phone calls for Sapphire — she answers your Twilio number(s) and can place
calls out. Works behind NAT with **zero open ports** (outbound SIP registration
holds the pinhole). One live call per number; multiple numbers run concurrently.

> Working notes — short on purpose, will be finalized later.

## Twilio setup (new console — use the search bar at the top for everything)

1. **Buy a number** — Phone Numbers → Buy a number.
2. **Create a SIP domain** — search *"SIP domains"* → create one
   (e.g. `yourname.sip.twilio.com`).
3. **Create a credential list** — search *"Credential lists"* → new list, add a
   credential (username + strong password). This is what Sapphire logs in with.
4. **Wire the domain** — on your SIP domain: attach the credential list under
   **Voice Authentication**, and enable **SIP Registration** with the same
   credential list.
5. **Route the number to Sapphire** — search *"TwiML Bins"* → create a bin:

   ```xml
   <Response><Dial><Sip>sip:USERNAME@yourname.sip.twilio.com</Sip></Dial></Response>
   ```

   Then open your number → Voice Configuration → point "A call comes in" at the bin.
6. **Router: nothing.** Signaling is TLS (encrypted) by default, so router SIP
   ALG/passthrough settings don't matter. No port forwarding needed either.

Twilio config changes can take a few minutes to propagate.

## Sapphire setup

1. **Settings > Plugins > Twilio Voice** — add an account: SIP domain, username,
   password, the phone number. Greeting optional. For **outbound** calling, also
   add your Account SID + Auth Token (Twilio console home page).
2. **Triggers > Realtime** — create an *incoming call* rule for the account.
   **This is the on/off switch**: rule enabled = number registered and answering.
   The rule carries the call's persona/prompt, voice, chat, and an optional
   caller filter (rules are most-specific-wins: a "just my number" rule and an
   "everyone else" rule coexist on one number; no matching rule = call declined).
3. **Outbound** — in Mind > People, give a person a phone number and check
   **Allow AI to call**. Sapphire dials via her `phone_call` tool; she can only
   call whitelisted people.

## Adding a second number (concurrent calls)

1. Twilio: add a **second credential** (e.g. `user2`) to the *same* credential
   list — no domain changes needed.
2. Second TwiML Bin dialing `sip:user2@yourdomain...`; point the new number at it.
3. Sapphire: add a second account (same domain, new username/password/number) +
   its own Realtime rule (own persona/voice).

Both numbers register on their own ports and take calls at the same time —
different personas, voices, and chats per line. Concurrent-call cap is the
`CONVERSATION_EXTERNAL_SLOTS` setting (default 2).

## Notes / gotchas

- **`403 Forbidden` on REGISTER right after setup** (check the logs): either the
  new credential is still propagating on Twilio's side (give it a few minutes —
  Sapphire auto-retries with backoff, up to 5 min between tries; toggling the
  Realtime rule off/on forces an immediate retry), the username/password don't match, or the
  credential list is attached under Voice Authentication but **not** under the
  domain's **SIP Registration** section — they're two separate attachment points
  and registration needs its own.

- Sapphire hangs up by saying goodbye and writing `<<HANG UP>>` — automatic,
  built into every call. Caller hangup is detected instantly.
- A call runs in the chat the rule targets (or a per-caller ephemeral chat);
  outbound calls run in a side chat and report a transcript back to the chat
  that placed the call.
- If registration drops (IP change etc.) it self-heals within ~30s; endpoint
  threads are watched and restarted by the reconcile loop (~12s).
- SIP signaling is **TLS by default** (verified working with router SIP
  ALG/passthrough ON — the router can't read the stream, so it can't break it).
  The per-account **SIP Transport** setting offers legacy UDP; only use it if
  TLS can't connect, and turn router SIP ALG OFF if you do.
