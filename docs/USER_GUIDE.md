# LessonForge: user guide

## Start the app
* **First time:** double-click `setup.bat` (or run the steps in README → Quick start). It asks for your API keys once.
* **Every other time:** double-click `run.bat`, or in PowerShell inside the project folder:
  `.venv\Scripts\python -m uvicorn app.main:app --port 8000`, then open http://localhost:8000.

## The screen
| Area | What it is for |
|---|---|
| **New lesson** (orange window) | Type a title and learning objective, or click a sample (**HTTP**, **SQL Joins**), then **Generate lesson**. |
| **Lessons** (violet window) | Every lesson you created. Click one to open it. The five small bars show each step's status. |
| **Lesson window** (purple) | Badges at the top: version, time estimate (green = fits 45 min), QC 1 and QC 2 verdicts. Below: the five pipeline steps, the activity log and the tabs. |
| Header chips | Which AI model writes (Gen), which one reviews (QC), the backup models (Fallback) and the research source. |

## Finding your way
Once a lesson exists, the top of the page shows a numbered path:

**1 Sources › 2 Lesson › 3 Fact check › 4 Teaching check › 5 Export**  (and a small **History** link)

Every page ends with **← Back** and **Next →** buttons, so you can simply keep clicking **Next**.
Above the path, the **Next step** bar tells you what needs attention now, with a button that takes you there.

## Typical workflow
1. **Generate.** Type a title and objective (or click a sample) → **Generate lesson**. The five steps run one by one
   (about 3–10 minutes). When they finish, they fold into one line: "✓ Researched, written and checked".
2. **1 Sources.** Every web page, book and video used, with a trust level and the reason for it.
3. **2 Lesson.** **Preview** shows what learners see. Switch to **✎ Edit** to change sections, quiz questions or
   videos; **Ctrl+S** saves a new version.
4. **3 Fact check (QC 1).** Each factual claim is checked against the sources and by an independent reviewer model.
   Every suggestion has its own **✓ Apply** and **✕ Dismiss** button. QC can be wrong too: you decide.
5. **4 Teaching check (QC 2).** Is every objective taught, practised and tested? Is it clear? Does it fit 45 minutes?
   Same Apply / Dismiss buttons.
6. **Check again.** After applying fixes, click **↻ Check again** to audit the new version.
7. **5 Export.** One button downloads the lesson (.md / .html / .json), references and both QC reports as a zip.
8. **History.** Every save and applied fix is a version: **Diff** shows what changed, **Restore** brings an old one back.

## Status colours
* Time badge: **green** fits 45 minutes · **yellow** too short · **red** too long.
* QC verdict: **pass** · **needs review** (has major/minor issues) · **fail** (at least one critical issue to look at).
* Steps: green = done · animated = running · red = failed (click **Retry**).

## When something goes wrong
| You see | Do this |
|---|---|
| A red step with an error | Click **Retry**. The app has already retried once by itself. |
| Errors mentioning 404 / 429 / 503 / "quota" | A free AI model is retired, out of quota or busy. Run `doctor.bat` (or `.venv\Scripts\python -m app.doctor`), restart, click **Retry**. |
| Yellow "Partial report" banner | One QC sub-check couldn't run. The rest is valid; click **↻ Check again** later. |
| "Pipeline already running" | Wait until the activity log stops updating. |
| Unsaved-edit warning | Your edits are kept in the browser; click **Restore draft**. |
