# 200-Week MA Scanner — self-updating site

Your scanner as a website: open it from any phone or computer, data refreshes
itself every weekday morning on GitHub's servers (free), and your trade log
saves automatically in each browser you use.

## One-time setup (~10 minutes)

1. **Create a GitHub account** at github.com if you don't have one (free).
2. **Create a new repository**: click **+** (top right) → *New repository*.
   Name it `ma200-scanner`, set it to **Public**, click *Create repository*.
   (Public is required for free hosting. The repo only contains market data
   and the dashboard code — your trades never leave your browser.)
3. **Upload these files**: on the new repo page click *uploading an existing
   file*, drag in everything from this folder — `index.html`, `data.json`,
   `update_data.py`, `scoring_v2.py`, `README.md` — and commit.
   Then add the workflow: click *Add file → Create new file*, name it exactly
   `.github/workflows/update.yml`, paste the contents of that file from this
   folder, and commit. (Drag-and-drop can't create dot-folders, so this one
   file is pasted by hand.)
4. **Turn on the website**: repo *Settings → Pages* → under "Branch" pick
   `main` and `/ (root)` → Save. After a minute your site is live at:
   `https://YOUR-USERNAME.github.io/ma200-scanner/`
5. **Turn on auto-updates**: repo *Actions* tab → click "I understand my
   workflows, go ahead and enable them" if asked → select **Update scan data**
   → *Run workflow* to test it. Green check = data.json refreshed. It then
   runs by itself weekdays at 7:00am ET.

## Phone install

Open the site in your phone's browser → Share → **Add to Home Screen**.
It gets its own icon and opens like an app.

## Notes

- The dashboard shows "(live)" next to the date when it's reading fresh data.
- Trades are stored per-browser (localStorage). Use Export/Import to move
  the log between devices.
- If a workflow run fails (rare — usually a ticker symbol change), open the
  Actions tab, click the red run, and paste the error to Claude to fix.
- Scoring is `scoring_v2.py` — the same leader-pullback, elite-curved model
  as the Claude version, including the personal style-fit lists.
