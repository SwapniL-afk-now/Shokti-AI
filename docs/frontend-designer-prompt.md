# Prompt for Frontend Engineer & UI/UX Designer

---

## Project Name: Shokti

**Tagline:** "Power your learning. Master the forgotten."

**What it does:** An intelligent study platform that schedules the right questions at the right time so students never forget what they learn.

---

## The Problem We Solve

Students fail not because they're unintelligent — they fail because learning is scheduled wrong.

- Re-reading feels productive but is mostly passive
- Students spend equal time on what they know and what they don't
- Most apps show questions randomly or by static syllabus order
- No system tracks what you've *forgotten* and brings it back at the right moment

**Our insight:** Retention is a scheduling problem. Answer the right question at the right interval and you can't forget.

---

## Who Uses This

| Persona | Context |
|---------|---------|
| HSC Biology students (Bangladesh) | Self-study at home, phone + desktop |
| Medical exam candidates | High-stakes, need precise retention |
| Gap fillers | Students who missed topics in class |

**Design for mobile-first. They practice on their phones.**

---

## The Core Algorithm

1. **SM-2 Spaced Repetition** — If you answer a question correctly, the next review interval doubles (1 day → 3 days → 7 days → ...). Get it wrong? Interval resets to 1 day.

2. **Three-Signal Sampling** — Every question is chosen because of a weighted combination of:
   - Your weak areas (answered wrong recently)
   - Your SM-2 intervals (due for review now)
   - Exam trend data (topics that appear in real exams frequently)

3. **Self-Evolution Loop** — The system automatically tunes these weights over time based on what actually works for the student.

**Design Implication:** The student should feel like the app is *smart* — not random. They should notice that it keeps surfacing exactly what they were about to forget.

---

## Key Screens

### 1. Welcome / Auth
- Clean minimal login/signup
- Option: guest mode (just practice, no account needed)

### 2. Diagnostic Complete Screen
- Shown after 30-question diagnostic test
- "Here's what we found about you" — topic strength map
- Visual: colored heat map of all topics (green = strong, red = weak)
- CTA: "Start Your First Session"

### 3. Dashboard (Home)
- Top: Current streak + total questions answered
- Middle: "Your weakest areas" — 3-5 topics with lowest retention
- Bottom: "Continue where you left off" — saves current SM-2 session state
- No clutter. This is not a dashboard for stats nerds — it's a decision screen: "What should I study right now?"

### 4. Practice Session (The Most Critical Screen)
- Full focus mode — no distractions
- Question displayed prominently with 4 answer options
- Timer optional (hidden by default, shown in exam mode)
- After answer: immediate reveal — green checkmark or red X
- Reveal shows: correct answer + 2-sentence explanation + topic context
- Subtle: "Next review: 3 days" or "Review tomorrow" shown after correct answer
- Swipe or tap to continue. No "Submit" button — just tap the answer and it auto-advances

### 5. Session Complete Screen
- "You answered 12 questions. 8 correct, 4 need review."
- List of missed topics → "These will appear in your next session"
- Streak update
- "Practice Again" or "Back to Dashboard"

### 6. Analytics / Progress
- Retention score per topic (not just % correct — weighted by question difficulty and time since last review)
- Learning curve chart (accuracy over last 30 days)
- Heat map of all topics (same as diagnostic, but updated live)

### 7. Exam Mode
- Timed. Fixed question count.
- No feedback mid-exam — submit all at once
- Results page: score + per-topic breakdown + "Add missed topics to practice queue"

---

## Design Direction

### Visual Philosophy
**"Calm precision."** — This is a learning tool, not a game. Don't make it look like Duolingo. No confetti, no cartoon mascot. Clean, confident, slightly serious.

### Color Palette
- **Background:** Near-black (#0F1117) for dark mode default
- **Surface:** Dark gray (#1C1F26) for cards and containers
- **Primary accent:** Teal/cyan (#00C9A7) — used sparingly for CTAs and progress indicators
- **Success:** Muted green (#2ECC71)
- **Error:** Muted red (#E74C3C)
- **Text:** Off-white (#F5F6FA) primary, muted gray (#8B8FA3) for secondary

### Typography
- **Primary font:** Inter or similar clean sans-serif
- **Question text:** 20-22px, high line-height (1.6) for readability
- **Answer options:** 16-18px, generous tap targets (min 48px height on mobile)
- **No decorative fonts.** Serious academic context.

### Layout Principles
- **Mobile-first** — everything works on 375px width
- **Max content width on desktop:** 680px
- **Spacing:** generous whitespace, sections clearly separated
- **Navigation:** bottom tab bar on mobile (Dashboard, Practice, Analytics, Profile)
- **No sidebar navigation on mobile** — hamburger menu on desktop only

### Micro-interactions
- Correct answer: soft green pulse, smooth slide to next question
- Wrong answer: subtle red flash, answer shakes once, then shows correct answer
- Progress bar: smooth fill animation (not jumpy)
- Topic strength map: cells fade from red to green as score improves

### Animations
- Keep it subtle. 200-300ms transitions max.
- No loading spinners — use skeleton screens for content loading
- Pull-to-refresh on dashboard

---

## Critical UX Rules

1. **Never lose session state.** If the student closes the app mid-session, they return exactly where they left off. SM-2 intervals are saved per question, not per session.

2. **Instant feedback always.** Every answer reveals the correct answer and explanation immediately. No ambiguity.

3. **The app should feel smart, not random.** If a student says "I got a question about Cell Division which I hadn't seen in 2 weeks — how did it know?" That's the system working correctly. Show "Scheduled for review" after each answer.

4. **No negative framing.** Don't say "You got this wrong." Say "Let's review this topic." The language is always encouraging.

5. **Minimize taps to start practicing.** From dashboard to first question shown: maximum 2 taps.

---

## Technical Constraints

- **Frontend:** React or Next.js (FE choice)
- **Backend:** FastAPI (Python) — we own the API
- **Database:** SQLite (question bank + student progress)
- **No real-time requirements** — polling every 30s is fine for session state
- **Mobile-first responsive** — must work on iPhone 12 and Android mid-range (2020+)

---

## How to Know You're Doing It Right

| Checkpoint | Signal |
|-----------|--------|
| After first diagnostic | Student understands their weak areas without us explaining |
| During practice | Student feels the questions are "smart" — not random |
| After wrong answer | Student immediately knows why and doesn't need to search |
| After correct answer | Student feels rewarded and sees clear progress indication |
| On return after 3 days | Student sees questions they were about to forget — "how did it know?" |
| On mobile | Practice session is comfortable, no accidental taps, readable text |

---

## Deliverable

Please produce:
1. **Design concept deck** — 5-8 screens, annotated with interaction details
2. **Component inventory** — all UI components with states (default, hover, active, disabled, loading)
3. **Interactive prototype** — Figma or similar — showing the critical practice session flow end-to-end
4. **Responsive specs** — how layout adapts from 375px to 1440px

Start with the **practice session screen** — it's the most important and most complex. Iterate on that first before moving to the outer shell (dashboard, analytics).