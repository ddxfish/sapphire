# Webcam

Lets her see through the camera of the device you are chatting from. On by
default (the setup wizard offers it). Network cameras are a separate plugin,
**IP Cameras** (`ipcam`).

## Tool

`capture_webcam()`, no arguments. She calls it when you ask her to look at
something or at you.

1. The Sapphire tab open in your browser shows an **Allow Camera** / **Deny** prompt.
2. On Allow, the browser takes one photo (up to 1280x720) and sends it back.
3. She sees it as an image in her tool result.

The tool waits up to 15 seconds. With no tab open, a denied prompt or no
answer, she is told the capture failed.

## Good to know

- Browsers allow the camera only on a secure page: `https://`, or `localhost`
  on the same machine. A plain `http://192.168.1.50` page is refused, and she
  is told why.
- The browser asks its own camera permission the first time as well.
- Nothing is captured without a click on **Allow Camera**.
