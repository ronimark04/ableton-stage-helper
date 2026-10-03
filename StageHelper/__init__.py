"""Stage Helper: STOP, +LOOP and >>> jump locators for Ableton Live.

Live loads this folder as a Control Surface script. It does nothing until the
page at http://localhost:8517 is open in a browser. See README.md.
"""


def create_instance(c_instance):
    # Imported here so the show logic can be tested without Live.
    from .surface import StageHelper
    return StageHelper(c_instance)
