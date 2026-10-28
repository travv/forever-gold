"""fgold: Claude-assisted gold making for WoW: Forever (stdlib only, Python 3.11+).

Python does the arithmetic, Claude edits a draft plan inside a fixed schema,
and `plan --apply` validates it before anything reaches the game. Nothing in
this package presses keys, clicks, or talks to the game client: files are the
only bridge (SavedVariables out, PlanData.lua in).
"""

__version__ = "0.1.0"
