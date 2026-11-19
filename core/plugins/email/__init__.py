"""The email plugin. A real package on purpose: without this file the loader
cannot evict it on a reload, and its modules (accounts, mailbox, message...)
would keep running as the previous version until a restart."""
