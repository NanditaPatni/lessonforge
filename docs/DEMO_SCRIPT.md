# Demo video: shot-by-shot script (≈ 7 minutes)

## Before you record (off camera, ~30 min)
1. **Live models, not mock.** The header must NOT say "offline mock". Gen should be a real model (gemini-3.5-flash after the
   daily quota resets, or groq gpt-oss-120b).
2. **Prepare two finished lessons** so you never wait on camera:
   * **HTTP Requests and Responses**: generate it, then do ONE round of review (apply/dismiss) and **↻ Check again**, so
     History shows v1 → v2 and the QC badges improved. Leave 2–3 issues **open** to apply live.
   * **SQL Joins**: generate it and leave it as-is (fresh v1).
3. Write down **one fact you verified yourself** (e.g. "POST that creates a resource → 201 Created + Location", from
   RFC 9110 §15.3.2 / MDN). Open that page in a second browser tab.
4. Close other apps and notifications (Windows Focus mode on). Browser zoom 90–100%, window maximised.
5. **Recording tool:** Xbox Game Bar (**Win + Alt + R** starts/stops recording the browser window) or OBS.
   Trim the start and end afterwards in **Clipchamp** (built into Windows).
6. Rehearse once with the script below. Speak to what's on screen; it doesn't need to be word-for-word.

---

## Shot list

| # | Time | Do on screen | Say (roughly) |
|---|---|---|---|
| 1 | 0:00 | App open on the welcome window. | "This is LessonForge. You give it a lesson title and a learning objective; it researches books, websites and YouTube, writes a 45-minute lesson, and checks it with two independent QC layers before an instructor exports it." |
| 2 | 0:25 | (Optional) mention the models. | "One model writes the lesson, a different provider reviews it, so the review is independent. If a free model is busy or out of quota, the backup models take over automatically." |
| 3 | 0:45 | Click **HTTP** sample → **Generate lesson**. Let 1–2 steps run. | "Here's the sample input from the brief. The pipeline plans the objective, researches, writes, then runs QC 1 and QC 2. Every step is saved, so a failed step can be retried without redoing the others." |
| 4 | 1:15 | Click the **prepared** HTTP lesson in the Lessons list. Point at the numbered path at the top. | "This takes a few minutes, so here's one I generated earlier. The review follows five numbered steps; Next at the bottom of each page moves on." |
| 5 | 1:25 | **1 Sources**. | "These are the sources it found: web pages, a book, and YouTube videos. Each one has a credibility tier with the reason; official docs and standards rank highest. The plan above shows how the objective was broken into measurable sub-objectives." |
| 6 | 2:00 | **Next → 2 Lesson** (Preview), scroll slowly. | "The lesson: hook, concepts, a GET vs POST comparison table, a worked example, hands-on activities, a knowledge check, supplementary videos, and references at the end. Every fact is cited to a real source, and the model is never allowed to write URLs itself. The plan at the top shows it fits 45 minutes, self-paced and instructor-led." |
| 7 | 2:50 | **Next → 3 Fact check**: tiles, then the claim table, open one claim's evidence. | "QC layer one checks facts. It splits the lesson into individual claims and checks each against the research evidence AND an independent model's knowledge. Doubtful claims trigger a fresh web search the writer never saw." |
| 8 | 3:20 | Show an issue you **dismiss** and say why (e.g. a weak source or a false alarm). Click **✕ Dismiss** on it. | "QC can be wrong too, so the instructor decides. This one I'm dismissing because… [your reason: e.g. it suggests a homework-answer site as a source / the claim is actually correct]." |
| 9 | 3:45 | Click **✓ Apply** on a real issue. | "This one is valid, so I apply it. Exact text fixes are applied without AI; larger fixes edit only that one section." |
| 10 | 4:10 | **Next → 4 Teaching check**: rubric, traceability matrix, time budget. | "QC layer two checks teaching quality: every sub-objective must be taught, practised and assessed, the key terms of the objective must appear, and the timing must fit 45 minutes. Plus a rubric for clarity, structure, examples and engagement." |
| 11 | 4:45 | **2 Lesson → ✎ Edit**: change a sentence, **Ctrl+S**. | "Instructors can edit anything. Each save is a new version; nothing is ever overwritten." |
| 12 | 5:05 | **History** → **Diff**. | "Here's exactly what changed between versions, and any version can be restored." |
| 13 | 5:20 | Click **↻ Check again**; when done, show the improved badges. | "Re-running QC measures the improvement instead of assuming it." *(If it takes long, cut here and resume when finished.)* |
| 14 | 5:45 | Second browser tab with your verified fact. | "I also verified facts myself, for example [your fact], against [MDN / RFC]." |
| 15 | 6:05 | Open the **SQL Joins** lesson; quick Preview + QC badges. | "The brief asks for a second topic. Here's SQL joins: same pipeline, same two QC layers." |
| 16 | 6:30 | **5 Export** → download the bundle, open the zip. | "Export gives the lesson as Markdown, HTML and JSON, the references, and both QC reports as separate files." |
| 17 | 6:45 | Error handling: type an empty title → Generate. | "Empty inputs are rejected. Failed steps show a Retry button, partial QC results are still reported, and unsaved edits are kept in the browser." |
| 18 | 7:00 | README / DESIGN.md briefly, or just talk. | "Built with Python and FastAPI, SQLite for versioned storage, plain JavaScript UI, Gemini and Groq models through one client with automatic fallback, DuckDuckGo, Google Books and YouTube for research, and BM25 retrieval for grounding. There are 170+ automated tests, including one per requirement in the brief. Thanks for watching." |

## Tips
* Pause instead of filling silence; cut pauses in Clipchamp.
* If something fails on camera, keep going: click **Retry** and say "failed steps can be retried". That *is* a requirement.
* Don't present offline/mock output as real research: the header shows the mode.
