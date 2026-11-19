"""The Bitcoin plugin. A real package on purpose: without this file the loader
cannot evict it on a reload, and its qr module would keep running as the
previous version until a restart."""
