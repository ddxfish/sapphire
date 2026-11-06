# Game: Hold'em

Heads-up Texas Hold'em against her, a game for the **Game Room** (enable that
plugin too). On by default. No tools, no dependencies, no images beyond its
tile.

She plays through a sealed seat: her hole cards never reach the chat, and the
engine referees every move, hers and yours, so neither of you can cheat. She
talks every hand; needle her, bluff her, or play in silence. See
`docs/GAME-ROOM.md`.

## Game Settings

The ⚙ on the tile, or **Game Settings** in the room sidebar. Per game, not per
session:

| Setting | Default | |
|---|---|---|
| Starting chips | 1000 | New sessions only |
| Big blind | 20 | New sessions only; the small blind is half |
| Her playing style | built in | Seat instructions she plays by |
| Temperature | 0.85 | Her seat's temperature |

For game authors: the engine is `games/poker/engine.py`, the board
`app/poker.js`. The contract is `docs/plugin-author/games.md`.
