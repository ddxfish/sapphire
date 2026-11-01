// shared/slash.js - what counts as a slash command in the composer (core/commands.py)
// `/remove 2` is a command; `/home/me/x` has a second slash and is a message.
export const isCommand = (text) => /^\/[a-z][\w-]*(\s|$)/i.test(text);
