# Social: Bluesky

The worked example of a **Social provider plugin**. Enable **Social** first, then this.

What it is: one block in `plugin.json` under `capabilities.social` that names the
provider, its fields and what it can do, and one module, `provider.py`, that exports
the functions. The Social plugin supplies everything else: profiles, the chat's
`social` scope, the Outbox, the tools (`social_post` and friends) and the card.

To make a provider for another platform, copy this folder, change the block and
rewrite the functions. The contract is in `docs/plugin-author/social.md`.

Setup: Settings › Plugins › Social › a profile › **+ Add connection** → Bluesky →
handle, app password (bsky.app → Settings → Privacy and security → App passwords).
No developer account, no approval.
