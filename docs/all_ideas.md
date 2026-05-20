# Shokti MCQ System — All Ideas

## Core Architecture

**File Search API** — primary knowledge base for semantic search, topic relationships, MCQ retrieval, grounding
**SQLite** — persistent storage for usage counters, student state, answer history
**graphify** — NOT used for scanned PDFs (no AST possible on images)

---

## Data Sources

| Source | Format | Contains |
|---|---|---|
| `books/chapters/*.pdf` | Scanned PDFs (Hasan Sir's book) | Source content for MCQ generation |
| `question_bank/chapter_06.json` | JSON | Existing MCQs for Bryophyta + Pteridophyta |
| `question_bank/chapter_08.json` | JSON | Existing MCQs for Tissue System |

### MCQ JSON Format (current)

```json
{
  "subject": "Biology",
  "source": "মেডিকেল মাস্টার প্রশ্নব্যাংক",
  "store_name": "উন্মেষ (Unmesh)",
  "chapters": [
    {
      "chapter_id": "06",
      "chapter_name": "ব্রায়োফাইটা ও টেরিডোফাইটা",
      "book_page_range": "62-66",
      "source_file": "medical_qbank.pdf",
      "topics": [
        {
          "topic_id": "1",
          "topic_name": "ব্রায়োফাইটা",
          "mcqs": [
            {
              "mcq_id": 1,
              "question": "মস (Bryophytes) এর স্ত্রী জননাঙ্গের নাম কী? [MAT: 19-20]",
              "options": { "A": "...", "B": "...", "C": "...", "D": "..." },
              "correct_answer": { "option": "A", "text": "Archegonium" }
            }
          ]
        }
      ]
    }
  ]
}
```

---

## Idea 1: Distractor Entropy

**What:** Analyze which wrong options in the question bank get chosen most. That reveals the most common student misconceptions in Bangladeshi medical exams.

**How:** For each MCQ, track which wrong option (A/B/C/D) students pick most frequently. Build a misconception frequency map per topic.

**Use:** Design better distractors — not random wrong answers, but wrong answers that actually mislead based on real student mistakes.

---

## Idea 2: MCQ Paraphrasing

**What:** Take an existing MCQ on a topic and rephrase it 5 ways. Same answer, different wording.

**How:** Use File Search to find the topic's content in Hasan Sir PDFs. Use that content to generate paraphrased versions of the same MCQ.

**Why:** The concept is tested from multiple angles in real exams. Students who practice paraphrased MCQs handle unexpected wording better.

---

## Idea 3: Cross-Chapter Integration MCQs

**What:** Generate MCQs that bridge two chapters. Example: "Which tissue type is most affected in a bryophyte undergoing water stress?" — connects Tissue + Bryophyta.

**How:** File Search on Hasan Sir PDFs finds overlapping concepts between chapter_06 and chapter_08. Derive bridge MCQs from that content.

**Why:** Cross-chapter MCQs are where top students separate from the rest. Most MCQ banks only test single topics.

---

## Idea 4: Option Pattern Analysis

**What:** Analyze the structure of distractors in your bank.

**Questions to answer:**
- Are wrong options mostly "almost right but wrong for one reason" or "completely unrelated"?
- Is there a bias toward certain option letters (C, D)?
- Do harder MCQs have more plausible distractors?

**Use:** Understand what exam designers value. Use the pattern to guide new MCQ generation.

---

## Idea 5: Question Difficulty Heuristics

**What:** Auto-classify MCQs as easy/medium/hard without human labeling.

**Features to measure:**
- Option length (longer = harder)
- Negative framing ("which is NOT true")
- Technical term density
- Number of similar-sounding options (A vs B confusion)
- Presence of exception patterns ("all except", "never")

**Use:** Build difficulty into the MCQ generation process automatically.

---

## Idea 6: Memory Anchor Questions

**What:** Find MCQs that appear in multiple sources in the question bank — year after year, across different exam sets.

**How:** Hash question text → find duplicates across JSON files. The duplicates are high-yield MCQs students must master.

**Use:** Flag these as PRIORITY topics. High-appearance MCQs = high-yield content.

---

## Idea 7: Negative MCQ Generation

**What:** Instead of "which is correct?", generate "which is INCORRECT?"

**How:** Take a correct MCQ. Introduce one wrong option among the correct ones. The student must find which option is NOT true.

**Why:** Different cognitive skill. Tests deeper understanding. Most question banks lack these.

---

## Idea 8: Spaced Repetition Scheduling

**What:** Schedule WHEN a student should review each topic based on difficulty and frequency.

**Formula:** `review_priority = topic_frequency × (1 - student_accuracy_on_topic)`

High frequency + high accuracy = review less
Low frequency + low accuracy = review more

**Use:** Personalized study calendar. Don't just practice — practice at the right intervals.

---

## Idea 9: Confusion Cluster Mapping

**What:** When a student gets multiple MCQs wrong, drill down to the specific misconception, not just the topic label.

**Examples:**
- "Archegonium vs Antheridium" confusion
- "Gametophyte vs Sporophyte" confusion
- "Bryophyta vs Pteridophyta" confusion

**How:** File Search query for all MCQs on similar concepts → check if student got multiple confused concepts wrong → build a misconception map.

**Use:** Students don't just see "weak in Bryophyta" — they see "you confuse gametophyte with sporophyte."

---

## Idea 10: Foundation Gaps

**What:** A student fails "Pteris reproduction" because they don't know "Bryophyta reproduction" first.

**How:** File Search finds prerequisite topic. Show student: "Study Bryophyta before Pteris." Build the path backward from failure to root cause.

**Use:** Personalized study order. Don't start where the student is weak — start where they need foundation first.

---

## Idea 11: Option Elimination Training

**What:** When a student can't find the correct answer, train them to eliminate TWO options first.

**How:** Practice sessions that specifically train elimination. Track: which options students eliminate vs guess.

**Result:** Even without knowing the answer, odds go from 25% to 50% by eliminating two obvious wrong answers.

**Use:** Especially for struggling students who pick randomly.

---

## Idea 12: The 80/20 Topic Filter

**What:** 20% of topics produce 80% of exam questions.

**How:** Count MCQ frequency per topic in the question bank. Rank by frequency. High-frequency topics = high-yield.

**Use:** Struggling students should master high-yield topics first, not get lost in obscure details. Focus effort where it counts most.

---

## Idea 13: "Read the Source Again" Trigger

**What:** When a student fails a topic 3+ times, flag it.

**Trigger:** "You've missed Tissue System MCQs 3 times. Read pages 235-240 from the textbook, then come back."

**Why:** Repetition without source review is wasted effort. The trigger forces active re-engagement with the source.

---

## Idea 14: Speed-Pressure Drills

**What:** Good students know content but panic under time.

**How:** Timed MCQ sessions with decreasing time per question: 60s → 45s → 30s. Track accuracy vs time.

**Result:** Identify if speed is the bottleneck, not knowledge. If accuracy drops as time decreases, it's a timing issue.

**Use:** For strong students who underperform in timed exams.

---

## Idea 15: Edge Case Exposure

**What:** Exam designers add 2-3 edge case MCQs that trip up even good students.

**How:** File Search identifies which textbook content appears RARELY in MCQs — those are the edge cases.

**Use:** Students who can handle edge cases stand out. Practice sessions should include low-frequency high-difficulty content.

---

## Idea 16: Negative Marking Strategy

**What:** In some exam systems, wrong answers cost marks. Teach good students when to skip vs guess.

**How:** Analyze the bank: "how many MCQs have 4 plausible options" vs "how many have 2 obvious wrong answers."

**Rule:** The more plausible distractors, the riskier a guess. Train students to calculate expected value before guessing.

---

## Idea 17: Peer Comparison Heatmap

**What:** Show each student how they compare on each topic vs other students.

**Example:** "You scored 90% on Bryophyta, but the average is 65% — you're above 90% of students."

**For weak topics:** "You're in the bottom 20% on Tissue System — here's what the top students studied."

**Use:** Context for performance. Students feel either motivated (above average) or focused (below average).

---

## Idea 18: "Tomorrow's Exam" Simulation

**What:** Generate a full-length mock from the question bank with balanced topic coverage, timed, negative marking rules applied.

**Compare:** Score vs study hours. Find efficiency ratio.

**Result:** Some students ace it with 20 hours, others fail with 100 hours. The gap is study method, not hours.

**Use:** Diagnostic to show students their actual efficiency, not just effort.

---

## Idea 19: Confidence Calibration

**What:** After each MCQ, student rates confidence (1-5). Track: do they score well when confident?

**Patterns:**
- Score well when confident = accurate self-awareness
- Score poorly when confident = OVERCONFIDENT
- Score well when uncertain = UNDERCONFIDENT (know more than they think)

**Use:** Help each student understand their own calibration pattern. Correct overconfidence before it costs them marks.

---

## Idea 20: Weakness-to-Strength Timeline

**What:** Graph of topic performance over time.

**Example:** "Tissue System: you were 40% in Week 1, 55% in Week 3, now 75% in Week 6."

**Why:** Visual progress beats static scores. Struggling students need proof that effort leads to measurable improvement.

---

## Idea 21: Active Recall Prompts

**What:** Before showing the MCQ, show just the topic: "Bryophyte reproduction" → student tries to recall what MCQs could appear → then see actual MCQs.

**Why:** Fights passive re-reading. Students who actively recall before checking remember longer than those who just read and re-read.

---

## Idea 22: The "Explain It Wrong" Test

**What:** After getting an MCQ wrong, ask the student to write why the wrong answer they picked is wrong.

**Why:** Most students can't. If they can explain the misconception clearly, they've truly understood it.

**Scoring:** Use explanations as diagnostic — high explanation quality = real understanding. Low quality = still confused on the concept.

---

## Idea 23: Sleep-Spaced Study

**What:** Track time-of-day when students study and correlate with retention rate.

**Pattern hypothesis:** Students who study at night and sleep retain less than those who study morning and sleep.

**Use:** Suggest optimal study windows per student based on their own data.

---

## Idea 24: Topic Semantic Clustering

**What:** Use File Search to embed all topics in the question bank, find which topics cluster together semantically.

**Use:**
- If student is weak in "Gametophyte", also target related "Sporophyte" — they semantically overlap
- Build study paths through topic clusters, not just individual topics

---

## Idea 25: MCQ Bloom's Taxonomy Analyzer

**What:** Classify each MCQ by Bloom's Taxonomy level:
- Remember: "Which of the following is..."
- Understand: "Which statement about X is TRUE..."
- Apply: "If X happens, what is the result..."
- Analyze: "Which of the following is NOT an example..."
- Evaluate: "Which answer best demonstrates..."

**Use:** Most MCQs in the bank are likely Remember/Understand. Good students need practice at Analyze/Evaluate levels.

---

## Idea 26: Source Attribution Per Topic

**What:** For each topic, attribute it to the specific textbook pages that content comes from.

**How:** File Search grounding per MCQ → link back to page numbers in Hasan Sir PDFs.

**Use:** "You've missed 5 MCQs on Bryophyta reproduction — read pages 198-205 in your textbook." Precise source, not vague "study the chapter."

---

## Idea 27: Wrong Answer Tagging

**What:** Tag each wrong answer in the bank with WHY it's wrong.

**Examples:**
- "Too broad" — option is true but doesn't answer the question
- "Too narrow" — true but incomplete
- "Concept confused" — confuses two related concepts
- "Memory error" — fact is simply wrong

**Use:** When student picks a wrong answer, show them WHY it's wrong, not just that it's wrong.

---

## Idea 28: "Similar but Different" MCQ Linking

**What:** When showing an MCQ, also show 2-3 MCQs that look similar but test different angles.

**Why:** Exam questions that look the same but differ in answer is a known trap. Train students to read carefully.

---

## Idea 29: Student Study Efficiency Score

**What:** Composite metric per student:

```
efficiency = (accuracy_score × topic_difficulty_weight) / study_hours_spent
```

**Use:** Show students they're not studying smart, just studying long. Compare efficiency ratios between students.

---

## Idea 30: Adaptive Difficulty Progression

**What:** As a student improves on a topic, automatically increase MCQ difficulty on that topic.

**How:** Accuracy > 80% → next session offers medium difficulty. Accuracy > 90% → next session offers hard questions.

**Use:** Students don't plateau because the system keeps challenging them at the edge of their ability.

---

## Priority Order

| Priority | Idea | Why |
|---|---|---|
| 1 | Confusion Cluster Mapping | Diagnoses the real problem, not just the symptom |
| 2 | 80/20 Topic Filter | Prioritizes effort where it counts most |
| 3 | Distractor Entropy | Improves MCQ quality, benefits generation |
| 4 | MCQ Paraphrasing | Directly increases question bank coverage |
| 5 | Active Recall Prompts | Fights passive study — biggest time waster |
| 6 | Memory Anchor Questions | Identifies high-yield must-know content |
| 7 | Confidence Calibration | Self-awareness prevents exam disasters |
| 8 | Speed-Pressure Drills | Solves a specific good-student bottleneck |
| 9 | Cross-Chapter Integration | Where strong students separate from rest |
| 10 | Wrong Answer Tagging | Turns mistakes into actual learning |

---

## Implementation Plan Priority (revised for question bank focus)

1. **Parse JSON → SQLite** — index existing question bank
2. **MCQ Paraphrasing + new MCQ generation** — File Search on Hasan PDFs + existing bank MCQs as examples
3. **Coverage gap analysis** — which topic_ids have few MCQs → generate more
4. **Distractor entropy** — analyze existing wrong options → apply to new MCQ generation
5. **Confusion cluster mapping** — for student diagnostics
6. **Topic clustering via File Search** — build semantic relationships between topics
7. **Active recall system** — before showing MCQ, prompt student to recall first
8. **Speed-pressure drills + confidence calibration** — per-student tracking
9. **Peer comparison heatmap** — comparison vs other students in the bank
10. **Adaptive difficulty progression** — auto-adjust based on performance