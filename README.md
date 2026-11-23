# Ableton Stage Helper

- Add +LOOP to locator name to loop from it to next locator
- Add >>> to locator name followed by name of a different locator to jump to that locator
  (it can sit off the bar line, e.g. on beat 3: Global Quantization switches to a finer setting just for that jump, then back)
- Name a locator STOP to stop playback

## Install

1. Quit Live.
2. Copy the `StageHelper` folder (the one with `__init__.py` inside) into
   `Documents\Ableton\User Library\Remote Scripts\`.
   If there's no `Remote Scripts` folder there yet, create it.

## Set up in Ableton

1. Start Live and open **Options → Preferences → Link, Tempo & MIDI**.
2. In an empty **Control Surface** row, choose **StageHelper**. Leave Input and
   Output on *None*.
3. If AbleSet is installed, set its Control Surface row to *None*, so the two
   don't both react to the same locators.
4. Make sure Global Quantization is set to **1 Bar** in every set - Ableton Stage Helper is optimized for use with this setting and might hiccup with another.

## Use it

1. Open your Set in Live.
2. Double-click **Start Stage Helper.cmd** (in the main folder of this
   repository). It waits until Live has loaded Stage Helper, then opens
   **http://localhost:8517** in your browser. You can do steps 1 and 2 in
   either order. Tip: right-click it → *Send to* → *Desktop (create shortcut)*.
3. When the pill at the top says **On**, Stage Helper is working. The page
   lists the locators it reacts to; anything in red won't work.
4. Keep that page open while you play. Closing it switches Stage Helper off,
   and Live then plays straight through every locator.
5. To leave a loop, press next locator in Live.

## If it doesn't connect

- **The browser says it can't reach the page**: Live hadn't finished loading
  Stage Helper yet (it can take a minute after Live starts). Use
  **Start Stage Helper.cmd** instead of a bookmark, or reload the page once
  Live has loaded.
- **The pill says "Off – not connected"**: Live is closed or still loading.
  The page reconnects by itself as soon as Live is ready.
- **Start Stage Helper.cmd says Stage Helper did not start**: check that the
  `StageHelper` folder is in `Documents\Ableton\User Library\Remote Scripts\`
  and that **StageHelper** is chosen as a Control Surface. Live's log,
  `%APPDATA%\Ableton\Live <version>\Preferences\Log.txt`, should contain the
  line `Page ready at http://localhost:8517`.
