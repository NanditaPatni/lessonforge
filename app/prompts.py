"""All LLM prompts in one place so they can be reviewed and iterated on together.

Design rules applied throughout:
  * Each prompt has ONE job and returns a strict JSON schema (validated in code).
  * The generator may only cite evidence passages it was given; URLs are never
    written by the model — references are assembled from retrieved sources in code.
  * QC prompts never see the generator's prompt or reasoning (independent review),
    and run on QC_LLM_PROVIDER/QC_LLM_MODEL when configured (a different model).
"""

PLAN_SYSTEM = """You are a senior instructional designer and research librarian.
You turn a lesson title and learning objective into a precise, measurable plan and a research strategy.
Return only JSON."""

PLAN_USER = """Lesson title: {title}
Learning objective: {objective}
Lesson length: {minutes} minutes (self-paced or instructor-led).

Tasks:
1. Decompose the objective into 2-5 measurable sub-objectives. Each must start with an observable
   Bloom's-taxonomy verb taken from or implied by the objective (e.g. "Explain", "Distinguish", "Apply").
   Every part of the objective must be covered by some sub-objective; do not add scope beyond it.
2. State the assumed audience and prerequisites.
3. List the key concepts and the most common learner misconceptions for this topic.
4. Write search queries: 3-4 web queries that will surface authoritative sources (official docs,
   standards, university material), 1-2 book queries, 2 YouTube queries for short explainer videos.

JSON schema:
{{
  "audience": "string",
  "prerequisites": ["string"],
  "sub_objectives": [{{"id": "LO1", "text": "string", "bloom": "remember|understand|apply|analyze|evaluate|create",
                      "evidence_of_mastery": "what a learner can do when they have mastered it"}}],
  "key_concepts": ["string"],
  "common_misconceptions": ["string"],
  "search_queries": {{"web": ["string"], "books": ["string"], "videos": ["string"]}}
}}"""

GENERATE_SYSTEM = """You are an expert instructor and instructional designer writing a {minutes}-minute lesson.
You write accurate, engaging, concrete content for learners, grounded ONLY in the evidence provided.
Return only JSON."""

GENERATE_USER = """LESSON TITLE: {title}
LEARNING OBJECTIVE: {objective}
AUDIENCE: {audience}
PREREQUISITES: {prerequisites}

SUB-OBJECTIVES (every one must be taught, practised AND assessed):
{sub_objectives}

COMMON MISCONCEPTIONS to address explicitly:
{misconceptions}

SOURCES (cite by id only):
{sources}

EVIDENCE PASSAGES (the only facts you may rely on):
{evidence}

AVAILABLE VIDEOS:
{videos}

WRITING RULES
- Every factual sentence must end with a citation marker like [S1] or [S2][S3] using the SOURCE ids above.
  Never cite a source for something its passages do not say. Never write URLs.
- If the evidence does not support something important, keep the statement general rather than inventing detail.
- Use Markdown inside content_md: short paragraphs, bullet lists, tables for comparisons, fenced code blocks
  for code / raw messages. Use concrete, realistic examples.
- Lesson flow: hook -> concept sections (one per sub-objective or tightly related pair) -> worked example(s)
  -> hands-on activity -> knowledge check -> summary.
- Each activity section gives step-by-step instructions, an expected outcome, and sets activity_minutes
  (time learners spend doing it, not reading it).

TIME BUDGET (strict: the whole lesson must fit in {minutes} minutes)
- Total explanatory text across all sections: {min_words}-{max_words} words.
- Activities: {act_min}-{act_max} minutes in total.
- Knowledge check: {q_min}-{q_max} multiple-choice questions, 4 options each, exactly one correct, plausible
  distractors based on the misconceptions, each tagged with the sub-objective it assesses.
- Videos: pick 1-3 relevant videos as supplementary resources. Mark "placement": "in_lesson" ONLY for one
  short (<8 min), directly on-target video you want watched during the lesson; otherwise "optional".

JSON schema:
{{
  "title": "string",
  "objective": "string",
  "audience": "string",
  "prerequisites": ["string"],
  "sections": [{{
      "id": "sec-1",
      "type": "hook|concept|example|activity|summary",
      "heading": "string",
      "objectives": ["LO1"],
      "activity_minutes": 0,
      "content_md": "markdown with [S#] citations"
  }}],
  "quiz": [{{"id": "Q1", "question": "string", "options": ["a","b","c","d"], "answer_index": 0,
            "explanation": "why the answer is right, with [S#]", "objective": "LO1"}}],
  "videos": [{{"source_id": "S#", "placement": "optional|in_lesson", "why": "what the learner gets from it"}}],
  "instructor_notes": "facilitation tips for instructor-led delivery"
}}"""

FIT_BUDGET_SYSTEM = """You are an editor who resizes lessons to fit a time budget without losing coverage
of any learning objective or breaking citations. Return only JSON."""

FIT_BUDGET_USER = """The lesson below does not fit its {minutes}-minute budget.
Measured timing: self-paced {self_paced} min, instructor-led {instructor_led} min.
Per-section breakdown: {breakdown}
Required change: {direction}

Rules: keep every sub-objective taught, practised and assessed; keep [S#] citations attached to the
facts they support; keep the same JSON schema; do not add new facts without citations.

LESSON JSON:
{lesson}"""

# ----------------------------------------------------------------- QC layer 1
CLAIMS_SYSTEM = """You are a meticulous fact-checking analyst. You extract atomic, independently checkable
factual claims from educational content. Return only JSON."""

CLAIMS_USER = """Extract the factual claims from this lesson (max {max_claims}, prioritise claims learners are
most likely to remember or be tested on, including quiz answer keys).
- A claim is a single checkable statement of fact (definitions, behaviours, numbers, rules, classifications).
- Skip opinions, instructions to the learner, and clearly hypothetical example values.
- "quote" must be copied VERBATIM from the section text (a sentence or clause), so it can be located.
- For quiz answer keys, use section_id "quiz" and phrase the claim as "The correct answer to <question> is <option>".

JSON: {{"claims": [{{"id": "C1", "section_id": "sec-2", "text": "atomic claim", "quote": "verbatim text",
        "importance": "high|medium|low"}}]}}

LESSON:
{lesson}"""

VERIFY_SYSTEM = """You are a strict, independent fact-checker. You did not write this content.
For each claim you produce TWO independent signals:
  1. verdict — judged ONLY against the evidence passages supplied for that claim.
  2. knowledge_check — your own expert knowledge of the field (standards, official docs).
Be precise: a claim that overstates, over-generalises or omits a crucial qualifier is "partially_supported".
If a claim concerns something newer than your training data, answer knowledge_check "uncertain", not "incorrect".
Return only JSON."""

VERIFY_USER = """For each claim below, judge it against ITS evidence passages and against your expert knowledge.

verdict:
  supported           — passages clearly state it
  partially_supported — passages support part of it, or it overstates / lacks a qualifier
  unsupported         — passages do not address it
  contradicted        — passages say something incompatible
knowledge_check: correct | incorrect | uncertain
correction: if the claim is wrong or imprecise, a corrected replacement for the quoted text that fits
            into the same sentence; otherwise "".

JSON: {{"results": [{{"id": "C1", "verdict": "...", "evidence_ids": ["S1.p2"], "knowledge_check": "...",
         "explanation": "one or two sentences", "correction": ""}}]}}

CLAIMS WITH EVIDENCE:
{claims}"""

CONSISTENCY_SYSTEM = """You are a senior technical reviewer auditing an educational lesson for factual
integrity. You did not write it. Return only JSON."""

CONSISTENCY_USER = """Audit this lesson for problems a claim-by-claim check would miss:
- internal inconsistencies (sections or quiz contradicting each other)
- misleading simplifications or outdated information
- incorrect quiz answer keys or quiz questions with more than one defensible answer
- important factual gaps: facts in the evidence a learner needs for the objective but the lesson omits
Only report real problems. For each, propose a concrete fix.
Do NOT flag a statement as outdated or wrong merely because it describes events after your training data:
the evidence passages may be newer than your knowledge. Only flag it if the evidence contradicts it.

fix.op is one of:
  replace_text   — set "find" to exact verbatim text from the section and "replace" to the corrected text
  rewrite_section — set "instruction" describing exactly what to change in that section
  rewrite_quiz   — set "instruction" describing the quiz change
  add_section    — set "instruction" describing the section to add (section_id = the section it follows)

JSON: {{"issues": [{{"category": "inconsistency|misleading|outdated|quiz_answer|factual_gap",
        "severity": "critical|major|minor", "section_id": "sec-2|quiz", "description": "...",
        "suggestion": "...", "fix": {{"op": "...", "find": "", "replace": "", "instruction": ""}}}}]}}

OBJECTIVE: {objective}
EVIDENCE SUMMARY (top passages):
{evidence}

LESSON:
{lesson}"""

# ----------------------------------------------------------------- QC layer 2
INSTRUCTION_SYSTEM = """You are an experienced instructional designer reviewing a {minutes}-minute lesson.
You did not write it. You judge it against the learning objective using evidence-based instructional
design (constructive alignment, worked examples, retrieval practice, cognitive load). Be specific and
critical — a generic review is useless. Return only JSON."""

INSTRUCTION_USER = """LEARNING OBJECTIVE: {objective}
SUB-OBJECTIVES:
{sub_objectives}

AUTOMATED CHECKS ALREADY RUN (do not repeat them, use them):
{auto}

Score each criterion 1-5 (5 = excellent) with a one-sentence justification that cites specific sections:
- objective_alignment: every sub-objective taught, practised, assessed; nothing off-scope
- clarity: plain language, terms defined before use, appropriate for the audience
- structure: logical progression, chunking, signposting, hook and summary do their jobs
- examples: concrete, realistic, worked examples for each key idea
- practical_application: hands-on activity that makes learners USE the knowledge
- assessment: quiz items test the objective at the right cognitive level with plausible distractors
- engagement: hook, questions, variety, active learning
- pacing: realistic for {minutes} minutes

Then list the specific issues (gaps in explanations, examples or practice; unclear passages; misaligned
quiz items; structure problems), each with a concrete fix.
fix.op: rewrite_section (with "instruction") | add_section (with "instruction"; section_id = the section it
should follow) | rewrite_quiz (with "instruction") | replace_text (with exact "find" and "replace").

JSON: {{"scores": {{"objective_alignment": {{"score": 4, "justification": "..."}}, "...": {{}}}},
        "strengths": ["..."],
        "issues": [{{"category": "alignment|clarity|structure|examples|practice|assessment|engagement|pacing",
                    "severity": "critical|major|minor", "section_id": "sec-3|quiz", "description": "...",
                    "suggestion": "...", "fix": {{"op": "...", "instruction": "", "find": "", "replace": ""}}}}]}}

LESSON:
{lesson}"""

# ------------------------------------------------------------- applying fixes
REWRITE_SYSTEM = """You are revising ONE part of an existing lesson to apply a reviewer's recommendation.
Change only what the recommendation requires; keep the rest of the text, tone and formatting.
Factual sentences must keep [S#] citation markers, using only the listed SOURCE ids (write [S6], never the
passage form [S6.p8]). Never write URLs.
Return only JSON."""

REWRITE_SECTION_USER = """RECOMMENDATION TO APPLY:
{instruction}

AVAILABLE SOURCES: {sources}
RELEVANT EVIDENCE:
{evidence}

CURRENT SECTION JSON:
{section}

Return the full revised section with the same schema:
{{"id": "...", "type": "...", "heading": "...", "objectives": [...], "activity_minutes": 0, "content_md": "..."}}"""

ADD_SECTION_USER = """RECOMMENDATION TO APPLY (write a NEW section for the lesson "{title}"):
{instruction}

Keep it short (aim for 120-250 words, or an activity of at most 8 minutes) so the lesson stays within
its time budget.
SUB-OBJECTIVES: {sub_objectives}
AVAILABLE SOURCES: {sources}
RELEVANT EVIDENCE:
{evidence}

Return: {{"id": "sec-new", "type": "concept|example|activity", "heading": "...", "objectives": ["LO1"],
          "activity_minutes": 0, "content_md": "markdown with [S#] citations"}}"""

REWRITE_QUIZ_USER = """RECOMMENDATION TO APPLY to the knowledge check:
{instruction}

SUB-OBJECTIVES: {sub_objectives}
AVAILABLE SOURCES: {sources}
RELEVANT EVIDENCE:
{evidence}

CURRENT QUIZ JSON:
{quiz}

Return the full revised quiz: {{"quiz": [{{"id": "Q1", "question": "...", "options": ["a","b","c","d"],
  "answer_index": 0, "explanation": "...", "objective": "LO1"}}]}}"""

# ------------------------------------------------- chunked generation (low-TPM models)
OUTLINE_USER = """LESSON TITLE: {title}
LEARNING OBJECTIVE: {objective}
AUDIENCE: {audience}

SUB-OBJECTIVES (every one must be taught, practised AND assessed):
{sub_objectives}

COMMON MISCONCEPTIONS: {misconceptions}

SOURCES (cite by id only): 
{sources}

EVIDENCE PASSAGES:
{evidence}

AVAILABLE VIDEOS:
{videos}

Design the lesson OUTLINE for a {minutes}-minute lesson (section content is written later, in a separate step).
Flow: hook -> concept sections (one per sub-objective or tightly related pair) -> worked example(s) -> hands-on
activity -> summary. 6-9 sections. Activities total {act_min}-{act_max} minutes.
Also write the complete knowledge check now: {q_min}-{q_max} multiple-choice questions, 4 options, exactly one
correct, distractors based on misconceptions, explanation ending with [S#] citations, tagged with an objective.
Pick 1-3 videos ("in_lesson" only for one short on-target video, else "optional").

JSON:
{{"title": "...", "objective": "...", "audience": "...", "prerequisites": ["..."],
  "sections": [{{"id": "sec-1", "type": "hook|concept|example|activity|summary", "heading": "...",
                "objectives": ["LO1"], "activity_minutes": 0,
                "brief": "2-3 sentences: exactly what this section must cover, which misconception it fixes, which example it uses"}}],
  "quiz": [{{"id": "Q1", "question": "...", "options": ["a","b","c","d"], "answer_index": 0, "explanation": "... [S1]", "objective": "LO1"}}],
  "videos": [{{"source_id": "S#", "placement": "optional", "why": "..."}}],
  "instructor_notes": "..."}}"""

SECTIONS_USER = """You are writing part of the lesson "{title}" (objective: {objective}; audience: {audience}).

FULL OUTLINE (for context and continuity — do not repeat what other sections cover):
{outline}

SOURCES (cite by id only): {sources}
EVIDENCE PASSAGES:
{evidence}

Write the content for ONLY these sections:
{targets}

RULES
- Markdown in content_md: short paragraphs, bullet lists, tables for comparisons, fenced code blocks for raw
  messages/code, concrete realistic examples.
- Every factual sentence ends with a citation like [S1] using the SOURCE ids above (write [S6], not the
  passage id [S6.p8]). Never write URLs.
- Activities: numbered steps + "Expected outcome".
- Respect the target word counts (they keep the lesson within {minutes} minutes).

JSON: {{"sections": [{{"id": "sec-1", "content_md": "..."}}]}}"""
