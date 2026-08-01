# Boot splash "black gap" — investigation

**Status:** investigation + proposed fix. **No boot files have been changed.**
Requested in `docs/td5-dash-todo.md` #14, which explicitly warns that boot-sequence
changes have caused real problems before — so this is root-cause first, approval before
touching anything.

## Reported symptom

On boot: black screen for a while → Land Rover logo appears briefly → **black again** →
finally the UI loads. Expectation: the logo should stay up through more of the load,
not drop back to black.

## The actual boot visual sequence

| Stage | What's on screen | Where |
|-------|------------------|-------|
| 1 | Black (kernel boot, `quiet splash`, all VT colours forced black) | `deploy/setup.sh:366-393` |
| 2 | **LR logo fades in** (Plymouth splash) | `deploy/plymouth/td5-dash/td5-dash.script:16-28` |
| 3 | Plymouth quits but keeps the logo pixels via `--retain-splash` | `deploy/setup.sh:358-362` |
| 4 | tty1 autologin clears console to black, runs `startx` | `deploy/setup.sh:113-120` |
| 5 | X starts and **paints the root window solid black** (`xsetroot -solid black`) — the retained logo is wiped here | `deploy/xinitrc:24-26` |
| 6 | X root stays black while xinitrc **blocks up to 60 s** polling `/health` before Chromium is even launched | `deploy/xinitrc:41-50` |
| 7 | Chromium launches, paints `http://localhost:8000` (page bg `#0d0d0d`), gauges show placeholders, live data arrives on WS connect | `deploy/xinitrc:52-69` |

## Root cause of the second black gap

`--retain-splash` only bridges **Plymouth-quit → X-start** (stage 3→4). As soon as X
starts (stage 5) it takes over the framebuffer and `xsetroot -solid black` explicitly
paints the root black — destroying the retained logo. Then xinitrc **deliberately waits
for the backend** (stage 6): `/health` only returns 200 after the FastAPI lifespan
finishes starting every service, so Chromium isn't spawned until the backend is fully
up. Throughout stages 5–7 there is **no holding image** — just the black X root — until
Chromium paints.

So: logo (stage 2–3) → **black** (stages 5–6, dominated by the up-to-60 s health wait)
→ UI (stage 7). That middle black is the complaint.

Note the health wait exists for a good reason: the page is *served by the backend*
(`http://localhost:8000`), so launching Chromium before the backend is up would show a
connection-refused error page. We can't simply skip it.

## Proposed fix (low-risk, additive)

**Show the LR logo as a full-screen holding image on the X root during the wait,**
instead of black — then let Chromium paint over it.

1. In `deploy/xinitrc`, replace `xsetroot -solid black` with a lightweight full-screen
   image display of the logo on a black background (e.g. `feh --bg-max --image-bg black
   LR-Logo.png`, or centre it). `feh` sets the X root pixmap and exits; the image stays
   until Chromium's window covers it.
2. Add `feh` to the package install in `deploy/setup.sh` (tiny, no X session deps).
3. Use the **un-rotated** source logo (`LR-Logo.png`) — X already applies the display
   rotation via `xrandr`, unlike the Plymouth path which needs the pre-rotated asset.

Optional extra for a fully seamless handover (stage 6→7): add an in-app splash overlay
to `frontend/index.html` (same logo, `#0d0d0d` background) shown until the first
WebSocket message, then fade out. That makes it logo (feh) → logo (page) → UI, with no
black at any transition. This part is pure frontend and carries no boot risk.

### Why this is low risk
- It's **additive**: if `feh` is missing or fails, we fall back to the existing
  `xsetroot -solid black` (keep it as an `||` fallback), so worst case is today's
  behaviour — no regression.
- It touches only the *visual* holding content, not timing, not the health gate, not
  Plymouth, not cmdline/config.
- The in-app splash is entirely inside the web app and independent of boot.

### What I deliberately did NOT propose
- Changing the `/health` wait logic or launching Chromium early (would risk the
  connection-refused error page — the exact kind of boot breakage to avoid).
- Touching Plymouth theme, `cmdline.txt`, `config.txt`, or initramfs.

## Recommendation

Implement step 1–3 (feh holding image + fallback) and optionally the in-app splash.
Both are reversible and low-risk. Await confirmation before editing `deploy/` files.
