# Champions VGC Team Checker

Local teambuilder for Pokémon Champions Regulation M-C. Pick seeds, or scan a box screenshot and build a Bring-6 from Pokémon you own.

## What you need

- Windows 10 or 11
- Python 3.11 or newer
- Internet the first time, so it can pull usage, Baltimore lists, and Worlds
- Pillow, only if you scan box screenshots
- Tesseract, optional. The current box screen has no names, so sprite labeling does not need it

## 1. Install Python

Download Python from https://www.python.org/downloads/ and check **Add python.exe to PATH** during setup.

Confirm in Command Prompt or the VS Code terminal:

```text
py -3 --version
```

If `py` is not recognized, reopen the terminal after install. `python` works instead of `py -3` on some machines.

## 2. Get the project

Clone the repo, or copy the folder that contains `team_recs.py` and `webapp/`.

```text
cd C:\GitHub\CapstoneProject
```

Use your real folder if it is not that path.

## 3. Install Pillow

Required for **Scan roster**. Without it, the scan crashes with `No module named 'PIL'`.

```text
py -3 -m pip install pillow
```

## 4. Optional: Tesseract

Only needed if a screenshot has printed Pokémon names. The Champions box page is icons only, so you can skip this and type names under the sprites.

```text
winget install UB-Mannheim.TesseractOCR
```

Restart the terminal after it installs.

## 5. Run it

From the project folder (the one that contains `webapp`):

```text
py -3 webapp\server.py
```

Open http://127.0.0.1:8765

The first start downloads battle data and can take a few minutes. Wait until the status line says Regulation M-C and a team count. Later starts are faster because `data/cache/` is reused.

Leave the server running. Every 6 hours it clears the usage, Limitless, and official-event cache and downloads again, so new Regionals and cups are included. Worlds 2026 stays cached. The status line shows the last meta time.

Stop the server with Ctrl+C.

## 6. Use the page

- Type 1–6 Pokémon and click **Recommend**.
- Or drop a Champions box screenshot, click **Scan roster**, type a name under each sprite, then **Build best team from roster**.
- Red circle-slash sprites are skipped. They are locked, not owned.
- Hard-refresh the browser (Ctrl+F5) after you replace `webapp/static` files.

## Files that matter

- `team_recs.py` — recommendations, legality, data loaders
- `webapp/server.py` — local site
- `webapp/static/` — page, script, styles
- `data/cache/` — saved Worlds, usage, and official lists. Do not commit this if it is huge. The app recreates it.

## If it fails

- `No module named 'PIL'` — run the Pillow install in step 3 and restart the server.
- `Pick at least one Pokémon` on the roster button — hard-refresh so the page is not using an old `app.js`.
- Page stays on "Loading battle data…" — the first download is still running. Leave the terminal open.
- Port already in use — close the old server window, then start it again.
