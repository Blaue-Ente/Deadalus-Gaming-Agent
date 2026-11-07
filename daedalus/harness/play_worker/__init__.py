"""Native Play Worker: runs a game in a real window, looks at it, and drives it with real input.

Proof levels reached here (never claimed beyond what was observed):
  L1  the engine started and answered on its bridge (headless or rendered)
  L3  a rendered window frame was captured and it is not blank
  L4  L3 + scripted keyboard/mouse input + an observed change in the frame
The worker runs in its own process (``python -m daedalus.harness.play_worker``); a session owns its own temp copy
of the project, display, port and process groups, so one cannot touch another or the owner's files.
"""
