# Crack the Code — Final Build

A local deduction game designed around calm reasoning, controlled guessing, and a clean minimal interface.

## Core game
- 3–7 digit codes
- Unique digits or repetition mode
- Repetition is a maximum constraint, never a requirement
- Relaxed / Easy / Normal / Hard / Extreme
- SAME + CORRECT feedback
- One continuous numeric input — type the code without clicking individual boxes
- Human-readable timer
- Live attempts, best, highest, streak and time on the game screen

## Final gameplay tools
- Optional Thinking Coach (ON/OFF)
- Constraint Map with digit states and position candidate boxes
- Focus Mode keeps the Constraint Map visible while reducing navigation clutter
- No-BS Mode removes optional coaching/decorative panels
- Pressure Mode with configurable time limit, attempt limit, or both
- Daily Challenge
- Guess Lab that never changes Records or normal game history
- Post-game analysis
- Local player profiles with separate history and statistics
- Reset Records, Reset History, and Reset Everything
- Light / Dark theme
- Victory fireworks and win overlay without reloading the website

## Robot Battle
- True alternating Human vs Robot battle
- Both sides have independent private secrets
- Same length, repetition rules and robot difficulty
- Robot guesses your secret; you can enable Auto Feedback so SAME/CORRECT is calculated automatically
- Manual robot feedback is still available when Auto Feedback is OFF
- You guess the robot's secret and receive automatic SAME/CORRECT feedback
- Robot secret mask always uses the selected code length
- Relaxed / Easy / Normal / Hard / Extreme robot strategies

## Navigation and input
- Back, Forward and Home navigation without full-page reloads
- Arrow keys move through visible controls when the current focus is not a text field
- Enter submits active game guesses
- Numeric input accepts continuous typing and rejects invalid characters
- Responsive layout designed to avoid expanding/overlapping side boxes

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open:

`http://127.0.0.1:5000`

## Run on your LAN

```bash
HOST=0.0.0.0 python run.py
```

Then open the host machine's LAN address from another device.

## Notes

The application uses SQLite and stores local profiles, games and guesses in `data/game.db`.

The secret-code rules remain numeric: only decimal digits `0–9` are used. Leading zeroes are valid because the secret is treated as a code string rather than an integer.


## V7 — Two Player Internet Mode

V7 preserves the V6 solo game and adds a separate private-room multiplayer mode.

### Multiplayer flow
1. Create a six-character private room.
2. Share the room code/link with the other player.
3. The host selects the V6 code settings and feedback mode.
4. Both players enter their own secret. A secret is never sent to the opposing browser.
5. Turns alternate strictly, one guess at a time.
6. Automatic feedback calculates SAME/CORRECT on the server.
7. Manual feedback sends the guess to the secret owner, who verifies SAME/CORRECT and submits the result.
8. Both players receive the ordered match history in realtime.
9. The first player whose guess reaches CORRECT equal to the code length wins.

### Run V7
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

The WebSocket endpoint is `/ws`. For Internet play, deploy the Flask application behind HTTPS/WSS or use a secure tunnel/reverse proxy. A local `127.0.0.1:5000` server is not reachable by a remote player.
