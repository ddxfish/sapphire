# Social

Sapphire on social media. **Profiles** are who posts (one per persona: Anita, Sapphire);
each holds **connections** to the platforms. A chat or persona picks its profile in
**Mind › Social**. One tool grammar: `social_post("hello", image="img:1234")`.

## Setup

1. Apps › Social (the Apps menu in the rail; the gear on Settings › Plugins › Social opens the
   same page) › **+ Add Profile**. Start with one (e.g. `sapphire`): a profile
   holds every platform connection at once, one Facebook Page, one X account, one Instagram and
   so on. A second profile is for a second persona (Anita) with her own accounts.
2. In the profile, **+ Add connection** → pick the platform, fill the form, Save.
   OAuth platforms (Facebook, X, YouTube, Instagram, LinkedIn) then need **Connect**:
   paste the redirect URI the card shows into the vendor console first.
3. **Test** proves the connection. Set its **mode**: `draft` (she queues, you post from
   the Outbox) or `auto` (she posts at once). **AI label** sets the platform's flag where
   one exists (Instagram).
4. Mind › Social in the chat (or the persona editor) = the profile that chat posts as.
5. **Daily round**: one click makes a Triggers task — at the hour you set, as the persona
   you pick, she reads her inbox, looks for news and posts (or queues).

## Platforms

| Platform | Needs | Does |
|---|---|---|
| Facebook Page | your Meta app (name it anything but "Facebook"); you must manage a Page | post, images, links, comments + mentions, reply, delete, followers |
| X | your X app: Settings → User authentication settings → Set up (then Keys and tokens shows the OAuth 2.0 Client ID); pay-per-use billing in the developer console, no Premium; the Automated label is under Account information → Automation | post, images, links, mentions, reply, delete, followers |
| YouTube | your Google OAuth client with the YouTube Data API on; consent screen "In production" | comments + replies, reply, delete, channel numbers (no uploads) |
| Instagram | the same Meta app; a Business/Creator account linked to a Page; the **image drop folder** | post (image required), comments, reply, followers |
| LinkedIn | your LinkedIn app with Share on LinkedIn + Sign In with OpenID Connect; token lasts 60 days | post, image, article link, delete — nothing comes back |
| Bluesky | handle + app password (the `social-bluesky` plugin) | post, images, links, mentions + replies, reply, delete, followers |

Instagram fetches images from a public URL. Settings at the bottom of the card:
`user@host:/var/www/html/drop` (copied with scp over your ssh key) and the URL it is
served at.

## Tools

`social_list` · `social_post(text, connections?, image?, link?)` ·
`social_inbox(hours?, connections?)` · `social_reply(connection, item_id, text)` ·
`social_stats` · `social_delete(connection, post_id)` · `social_meme(image, top, bottom)`.
`connections` takes names or platforms (`facebook, x`); blank = every connection.
Every tool posts as the chat's profile (Mind › Social); none of them can name another.

## Where things live

Tokens and secrets: core's credentials manager (`plugin_accounts.social`), scrambled,
never in backups. Wiring (profiles, modes): `user/plugin_state/social.json`. Drafts and
the posting log: the same file, image bytes beside it in `plugin_state/social-outbox/`.

## Adding a platform

`docs/plugin-author/social.md`. Copy `core/plugins/social-bluesky`.
