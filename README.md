# Ableton Stage Helper

- Add +LOOP to locator name to loop from it to next locator
- Add >>> to locator name followed by name of a different locator to jump to that locator
- Name a locator "STOP" to stop playback

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
2. Open **http://localhost:8517** in a browser. When the pill at the top says
   **On**, Stage Helper is working. The page lists the locators it reacts to;
   anything in red won't work.
3. Keep that page open while you play. Closing it switches Stage Helper off,
   and Live then plays straight through every locator.
4. To leave a loop, press next locator in Live.
