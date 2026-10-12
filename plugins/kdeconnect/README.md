# KDE Connect: a phone as a device

The phone runs [KDE Connect](https://kdeconnect.kde.org/) (F-Droid or Play).
Sapphire speaks its protocol (version 8) and appears on the phone as a desktop
named "Sapphire <hostname>". Nothing of theirs is copied here; the plugin is a
clean implementation of the open protocol (`protocol.py`, `link.py`).

## Setting it up

1. Settings > Devices > Add Device > Phone (KDE Connect). Open the app on the
   phone (same network). The phone appears in the pick list within seconds,
   with its pairing state under it.
2. Pair there: press **Pair** under the phone and accept on the phone (the
   same 8-character key shows on both), or request pairing in the phone's app
   and press **Accept pairing** here. Then **Add and test**. An unpaired phone
   cannot be added. Needs a 2025+ app (protocol 8).
3. On the phone, give KDE Connect the permissions you want it to have: SMS
   for texts, Contacts for names, Notifications > "Receive notifications" if
   she is to notify you.

## What she gets

| Tab | Actions |
|---|---|
| Status | pair, unpair (owner only), beside the device's health |
| Battery | read |
| Share | send (words or a link to the phone), list (files the phone shared); where a shared picture goes |
| Text messages | conversations (who texted lately, names and times), read (a name or number, loosely matched), send. **Locked by default**: unlock on its tab when she may read and send texts as you. "Texts wake her" lives here too |
| Notify | ping (a notification with words), find (rings the phone) |
| Contacts | find: a name's numbers, or a number's name |

The phone shares TO her by itself: pick "Sapphire" in the phone's share sheet.
A picture lands in the chat as a pasted picture would; a document is read
inline (and put into the chat's documents when kept); text and links land as
words. "Texts wake her" starts a turn for every text that arrives on the phone.

## Where things are

- `user/plugin_state/kdeconnect_identity/`: this Sapphire's certificate and key
  (its identity to phones; losing it means pairing again).
- `user/plugin_state/kdeconnect.json`: the paired phones' certificates.
- `user/plugin_state/kdeconnect_inbox/<phone>/`: files the phone shared.

Everything is LAN only. Tests: `pytest plugins/kdeconnect/tests`.
