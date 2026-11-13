# Game quality handbook

What makes a small game good to play, as rules a writer can check in its own code. Owner, 2026-11-13: the bar
is The Labirinth: a game is done when a stranger can start it, understand it in ten seconds, lose fairly, want
another try and win. Daedalus reads the **Core** section on every game build and the section for the game's genre.
Each rule names the thing to look for in code, so a rule is kept or broken, never "felt".

## Core
Words: game, игра*

- **Start in one action.** A title screen with one clear button or "press any key"; the game runs on the first
  key or tap. No settings to open first. The goal and the controls fit on the start screen in two lines.
- **Controls answer in the same frame.** Read input every frame, not on key-repeat events; movement starts and
  stops without lag. Arrow keys and WASD both work; Space or Enter is the main action; touch works on phones
  (pointer events, buttons large enough for a thumb, at least 44 px).
- **Every action has feedback.** Sound (sfx.js in a browser game), a visual change (flash, particles, squash, a number popping up)
  and, for big moments, a short screen shake (under 200 ms, a few pixels). A hit that shows nothing is a bug.
- **Fair losses.** The player sees what killed them: danger is telegraphed (a colour, a wind-up, a warning
  half a second before), hitboxes are a little smaller than the art, the first seconds after a (re)start are safe.
- **Fast retry.** From game over back to playing in one key and under two seconds. Keep the best score
  (localStorage wrapped in try/catch in a browser; `user://` in Godot; PlayerPrefs in Unity) and show it on game over: "Score 120 — Best 340".
- **A curve, not a wall.** Difficulty rises from a gentle start: speed, spawn rate or enemy count grow with time or
  score through one function (`difficulty(t)`), capped, so the game is winnable or endlessly survivable for a
  good player. The first 20 seconds teach; nothing kills in the first 3.
- **Readable screen.** Player, danger, reward and background each have their own colour and contrast; the player is
  never the same colour as the floor. The HUD (score, lives, time) sits in a corner, large, never over the action.
- **Pause and focus.** P or Escape pauses; the game pauses itself when it loses focus (`visibilitychange` in a
  browser, the focus notification in an engine).
- **Frame-rate independent.** Movement uses the time between frames (`dt`, clamped to about 1/30 s), so the game
  plays the same at 60 and 144 Hz and does not jump after a tab switch.
- **Fits the window.** The game scales to the window keeping its aspect (letterbox), crisp on high-DPI screens
  (`devicePixelRatio` for a canvas), and plays on a phone in landscape (about 780×340).
- **One loop, one state.** One `update(dt)` and one `draw()` (or the engine's `_process`/`Update`); game states (title, playing, paused, over, win) are an
  explicit value, not booleans scattered across the code. Restarting resets every piece of state.
- **An end.** A win (a level cleared, a target score, a boss beaten) with its own screen and sound, or an endless
  mode whose score is the goal. A game that only ends in losing needs the best score to be the win.
- **No dead ends.** Nothing random may block the way to the exit; spawns never appear on the player; a level can
  always be finished from any state it can reach.

## Platformer
Words: platformer, platform, jump, jumping, mario, metroidvania, side-scroller, sidescroller, climb, платформ*, скача*, скок*

- **Jump feel:** coyote time (about 100 ms after leaving a ledge the jump still works) and jump buffering (a jump
  pressed up to 100 ms before landing happens on landing). Releasing the button early cuts the jump (variable height).
- Gravity is stronger when falling than when rising; fall speed has a cap.
- Collisions resolve one axis at a time (move X, resolve; move Y, resolve), so the player never sticks in walls or
  tunnels through thin platforms at high speed.
- The camera leads a little in the direction of movement and is smoothed (lerp), never jerks on a jump.
- Checkpoints: dying sends the player back seconds, not minutes.

## Shooter
Words: shooter, shoot, shooting, shmup, bullet, bullets, space invaders, asteroids, twin-stick, gun, laser, стрел*, куршум*, изстрел*

- The player's shots are fast and visible; enemy bullets are slower, larger and a different colour.
- Fire rate has a cooldown; holding the button keeps firing (no button mashing required).
- Enemies arrive in waves with a pattern the player can learn; each new enemy type appears alone first.
- Hits flash the enemy and knock it back a little; kills burst into particles and add a floating score.
- Reuse bullets and particles from a pool instead of creating objects every frame.

## Puzzle
Words: puzzle, match, match-3, match3, sokoban, tetris, sliding, logic, tiles, grid, block, blocks, пъзел*, загадк*, логическ*

- Every move is undoable (Z or a button) and the level restartable (R) without losing progress.
- The rules are taught by the first levels, one new rule per level, never in a wall of text.
- Input snaps to the grid; an invalid move gives a soft "no" (a shake and a low sound), never silence.
- Levels are data (arrays or JSON), each checked solvable; the level number and moves are shown.
- Winning a level waits for the last animation to end, then celebrates before the next.

## Arcade and endless
Words: arcade, endless, runner, flappy, snake, pong, breakout, catcher, dodge, survive, highscore, high score, аркад*, безкрайн*, змия*, бягащ*

- One button or one axis of control; the challenge comes from speed and timing.
- Speed rises steadily and is capped; obstacles are generated with a minimum gap the player can always pass.
- Score counts up visibly while playing; milestones (every 100 points) get a sound and a flash.
- Near-misses can be rewarded (a small bonus and a sound): they make the player feel skilful.

## Roguelike
Words: roguelike, roguelite, dungeon, procedural, permadeath, run-based, loot, crawler, подземи*, рогълайк*

- Generation uses a seeded random function (a small PRNG, not `Math.random` everywhere) so a run can be replayed
  and tests can fix the seed. Every generated level is checked connected (flood fill from start to exit).
- Each run offers choices (pick one of three upgrades), and the choices change how the next minutes play.
- Death shows what the run achieved and what killed the player; the next run starts in one key.
- Turn-based games show whose turn it is and preview what an action will do (damage, range) before it is taken.

## Strategy and tower defense
Words: strategy, tower defense, tower defence, td, rts, waves, base, build, resources, tycoon, idle, clicker, incremental, стратеги*, кули*, отбрана*, вълни*

- The player sees the next wave (count and type) before it starts and starts it when ready, or a timer shows when.
- Costs and what the player can afford are always visible; an unaffordable choice is greyed out.
- Placement shows a preview (range circle, valid or invalid colour) before it is confirmed.
- Speed control (1×, 2×) once the player knows the game; numbers grow on a curve, never by doubling forever.

## Racing
Words: racing, racer, race, kart, car, cars, drift, track, lap, laps, състезан*, кола*, коли*, писта*, обиколк*

- Steering is smoothed and speed-sensitive; the car never turns on the spot at full speed.
- Laps, position and a lap timer are on screen; the best lap is kept.
- Off-track slows the car but never stops it dead; a reset key puts the car back on the track.
- The camera follows behind with a little lag and a field of view that widens with speed.

## Card and board
Words: card, cards, deck, deckbuilder, solitaire, chess, checkers, board game, tabletop, poker, карт*, тесте*, шах*, дама*

- Legal moves are highlighted when a piece or card is picked up; illegal ones cannot be dropped.
- Cards and pieces move with a short animation (100–250 ms), never teleport.
- The rules live in pure functions (`legalMoves(state)`, `apply(state, move)`) with tests; the screen only draws.
- An opponent's move waits a moment and is shown, so the player can follow it.

## RPG and adventure
Words: rpg, adventure, quest, quests, story, dialogue, npc, inventory, zelda, exploration, приключен*, мисия*, мисии*, истори*, диалог*, герой*

- Dialogue advances on a key and can be skipped; text appears quickly; the speaker is named.
- The player always knows the current goal (a one-line quest note on screen or a key that shows it).
- Inventory and stats are readable at a glance; a new item says what it does.
- Save progress automatically (localStorage) at safe points; say "saved".

## 3D
Words: 3d, three.js, threejs, webgl, babylon, first-person, fps, third-person, voxel, триизмер*, 3д

- The camera never clips through walls (raycast from target to camera and pull in) and is smoothed.
- Pointer lock for mouse look with a visible "click to play" overlay and Escape to release.
- Lighting: one directional light with soft shadows plus ambient; the player is lit and readable from every angle.
- Keep draw calls low: reuse geometries and materials, merge or instance repeated objects.
- Show a loading screen with progress while models and textures load.

## Mobile and touch
Words: mobile, phone, touch, android, ios, expo, tap, swipe, capacitor, телефон*, мобилн*, докосван*, тъч*

- Every control has an on-screen touch equivalent; nothing needs a keyboard.
- Thumb zones: movement bottom-left, actions bottom-right, nothing important at the top centre.
- Prevent page scroll and zoom on the game canvas (`touch-action: none`), and handle portrait and landscape.
- Audio unlocks on the first tap; vibration (`navigator.vibrate`) on hits where supported, short.

## Checklist before done
Words: game

1. Start: one action from the page load to playing.
2. Controls: keyboard and touch, answered the same frame, explained on screen.
3. Feedback: every action has sound and a visual response.
4. Curve: gentle start, rising, capped; first seconds safe.
5. Loss: telegraphed danger, fair hitboxes, game over with score and best, retry in one key.
6. Win or endless goal: reachable, celebrated.
7. Pause, focus loss, window resize and a phone in landscape all handled.
8. Tests cover the rules (score, collisions, win and lose conditions, level solvability).
