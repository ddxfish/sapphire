# IP Cameras

Network cameras that speak ONVIF, as devices. Sapphire sees through them, and
turns and zooms the ones that can move (PTZ).

## Add one

1. Switch ONVIF on in the camera's own settings page.
2. Settings > Devices > **+ Add Device** > **IP camera (ONVIF)**.
3. Enter its address, user and password. The ONVIF port is found for you.

The camera has to be on your own network. An internet address is refused.

## What Sapphire can do

```
device_action("porch","camera","look")            one quick picture
device_action("porch","camera","look","sharp")    the full-size picture, a few seconds slower
device_action("porch","ptz","move","left 30")     turn, then see the new view
device_action("porch","ptz","zoom","in")          closer, then see the new view
device_action("porch","ptz","goto","door")        turn to a place you named
device_action("porch","ptz","stop")
```

Both tabs carry a **Sapphire may use this** switch. A camera that cannot move
has no Move tab.

## Places

1. On the Move tab, use **move** and **zoom** until it points where you want.
2. Run **save** with a number, for example `1`. Use low numbers: some cameras
   keep the high ones for themselves.
3. Under **Places**, add a row: a name (`door`) and that number.

## Good to know

- The numbers in `move` and `zoom` are time, not degrees: 100 is about eight
  seconds of turning. Cheap cameras do not report where they point.
- When a move changes nothing, she is told the camera may be at the end of
  its travel.
- A picture is taken with no warning light or sound. The camera has neither.
- The quick picture is whatever size the camera's snapshot is, often small.
  `sharp` comes off its video stream and needs the `av` package.
