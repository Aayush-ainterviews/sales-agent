# Self-Evaluation System — poora flow (simple Hinglish)

> Sales agent ke andar ek **self-evaluation skill** — jo agent apne hi kaam ko audit
> karti hai, off-track cheezein pakadti hai, execution-galtiyan **khud sahi karke aage
> badhti hai**, aur rule-level defects human ko **propose** karti hai (kabhi auto-change
> nahi). Ye design-doc hai — abhi banana baaki; ye flow samjhata hai.
>
> Model: gemini-2.5-pro, thinking high (isliye self-critic credible hai).
> Design decisions: [architecture-decisions.md](architecture-decisions.md),
> vichaar-dhaara: memory `verification-architecture`.

---

## 0. Ek line mein

> Agent kaam karte-karte apne **key decisions ek log mein likhta** hai. Har data/action
> skill ke baad, aur run ke ant mein, wo **evaluation skill** chalata hai jo "actually kya
> hua" (files) ko "kya hona chahiye tha" (rules) se milaati hai. Clear galti mili aur pukka
> hai → **khud sahi karke us stage se aage** chala. Rule hi galat nikla → **human ko batao**,
> khud mat badlo. Baar-baar aane wali galtiyan ek **lacking-log** mein jama hoti hain.

---

## 1. Char hisse (cast)

| Hissa | Kya | Kahan |
|---|---|---|
| **Decision-log** | run ke दौरान agent ne kya-kya faisle liye + kyun | sandbox: `/home/user/runs/<runId>/decisions.md` |
| **Artifacts** | har tool ka input/output, normalized data, final output | sandbox: `/home/user/runs/<runId>/` + `outputs/` |
| **Evaluation skill** | audit karne wali skill (triage → deep audit) | sandbox: `.pi/agent/skills/evaluation/` |
| **Lacking-log** | baar-baar aane wali weaknesses (cross-run) | v1: sandbox file; future: Postgres (backend eval ke saath) |

Do fix-class (ye samajhna zaroori):
- **Execution error** — rule theek tha, agent phisal gaya → **khud fix + re-run** (bounded).
- **Systemic defect** — rule khud galat/ambiguous/missing → **human ko propose**, kabhi auto nahi.

---

## 2. Decision-log — kab banta hai, kya likhta hai

**Kab:** run ke **दौरान** (eval se pehle), sirf **key decision-points** pe — har micro-step pe nahi.
Key points: skill chuni, mode/filter chuna, enrichment call, batch banaya, send-draft, etc.

**Kya:** har entry mein 3 cheezein —
```
- Action: kya kiya (e.g. "job postings ke liye Apify skill use ki")
- Skill/Rule: kaunsa skill/rule follow kiya
- Reason: 2-3 line — kyun ye faisla liya
```

**Kyun zaroori:** eval "reasoning" ko yahin se padhta hai (live memory se nahi — file se).
Bina decision-log ke, "kyun galti hui" wala audit khokhla ho jaata hai.

**Kaun likhta:** agent khud, AGENTS.md ke rule se ("har key decision pe decisions.md mein likho").

---

## 3. Evaluation skill — trigger kab-kab, aur kaise

Do jagah chalti hai, **dono AGENTS.md ke HARD rule se** (optional nahi):

**A. Mid-run — har data/action skill ke BAAD (agli stage se pehle):**
```
Apify skill poori    → eval us skill pe → theek? → aage
Origami skill poori  → eval us skill pe → theek? → aage
submit-batch poori   → eval us skill pe → theek? → aage
```
Ye "jaldi pakdo, wahi se fix, aage badho" deta hai — galat data pe downstream banne se pehle.

**B. End — substantive result deliver hone ke BAAD (ek baar):**
Poore run pe holistic audit — goal poora hua? cross-skill coherence? overall relevance? honesty?

**Kaise trigger:** pi mein skill tab chalti hai jab agent uski SKILL.md padh ke follow kare.
AGENTS.md mein operating-rule:
> *"Kisi bhi data/action skill ke poora hone ke baad, agli stage se pehle `evaluation`
> skill chalao us step pe. Substantive result deliver karne ke baad, poore run pe
> `evaluation` skill chalao. Ye must hai, skip nahi."*

**Trigger sirf substantive run pe** — jisme tools use hue ya deliverable bana. "hi/thanks"
jaisi conversational turn pe eval nahi.

---

## 4. Eval kya karta hai — triage pehle, deep-audit baad mein

Cost bachane ke liye eval do-step:

**Step 1 — Fast triage (sasta, hamesha):** turant, halke checks —
- Sahi skill for intent? (job query → Apify use hua, Origami nahi?)
- Result khaali/malformed to nahi?
- Output shape rule se match?
- Basic relevance — jo maanga tha wahi tarah ka nikla?

Agar triage saaf → skill pass, aage badho (deep-audit nahi, cost bacha).

**Step 2 — Deep audit (sirf jab triage kuch flag kare):** us skill ki SKILL.md **fresh
re-read** + artifacts **rule-by-rule** compare, har verdict pe **rule-quote + exact artifact**.

**Basis (core principle):**
> "**artifacts (jo hua)** vs **fresh-read rules (jo hona tha)**", har verdict evidence-cited,
> **adversarial default** ("maano kuch galat hai; har step ko cited-rule ke against sahi
> साबित karo, warna flag"), aur **relevance** har jagah ek dimension.

**Dimensions jo check hote:**
skill-selection · step-adherence · output-accuracy · tool-calls (inputs/field-mapping/handling)
· **relevance** · goal-completion · reasoning (off-track points pe) · honesty (decision-log ka
reason artifacts se match karta hai?).

---

## 5. Gadbad mili to — kaise sahi hota hai

Eval ne issue pakda → do raaste (fix-class pe):

```
issue mila
 ├─ SYSTEMIC defect? (rule khud galat/ambiguous/missing)
 │     → human ko PROPOSE karo (problem → fix → impact). Rule kabhi auto nahi badlo. Ruk jao.
 │
 └─ EXECUTION error? (rule theek, agent phisla)
       ├─ high-confidence + evidence-cited + retry-budget bacha hai?
       │     → KHUD FIX: us skill/stage ko sahi karke re-run → us stage se aage sab dobara
       │       → phir eval → theek to continue
       └─ uncertain / budget khatam / baar-baar wahi
             → human ko FLAG karo ("khud fix nahi kar pa raha, dekho")
```

**Do zaroori baatein is correction mein:**

1. **"Us stage se aage sab dobara"** — stages inter-dependent hain. Agar collection galat
   thi, uske aage (enrichment, output) us galat data pe bana → failed stage + **saara
   downstream** redo. Par **jaldi pakadne ka faayda:** agar Origami-error uske turant baad
   pakda, downstream abhi bana hi nahi hoga → redo karne ko kuch hai hi nahi. **Aur jo
   pehle sahi tha (Apify) wo dobara nahi chalta.**

2. **Auto-fix sirf high-confidence pe** — kyunki re-run **asli paise** kharch karta hai
   (Origami/Apify dobara call). Agar eval ne **galti se** flag kiya (false positive), to ek
   poora paid re-run bekaar. Isliye: pukka galti → auto-fix; **shaq → human ko flag**
   (maybe pe paisa mat jalao).

3. **Loop bounded** — auto-fix → re-run → phir eval → phir shayad flag... infinite ho
   sakta. To **max 2 auto-correction rounds**; uske baad bhi issue → **human ko escalate**.
   (Yehi cheez pichle prototype ko le dubi thi — isliye bound zaroori.)

---

## 6. Lacking-log — kab banta/badhta hai

**Kya:** cross-run tracker — agent ki **baar-baar** aane wali weaknesses. Ek entry **per
recurring pattern** (deduped, count ke saath).

```
Pattern: "company/person lookup ko Apify pe route karta hai"
Runs: run-12, run-19, run-27   Count: 3
Severity: blocker
Status: proposed (systemic fix human ke paas)
```

**Kab likhta/update hota:**
- Jab eval koi flag banaata hai → lacking-log padho: **ye pattern pehle bhi aaya?**
  - **Naya pattern** → nayi entry (count 1).
  - **Repeat pattern** → count++ **aur escalate** — execution-slip se **systemic-defect**
    mein badal do (do-teen baar wahi galti = rule mein kami, agent ki nahi) → rule-change
    ke liye prioritise.

**Kahan (v1 vs future):**
- **v1 (abhi, backend eval defer):** eval sandbox ke andar chalti hai, jiske paas DB access
  nahi — to lacking-log **sandbox file** mein (`/home/user/.pi/agent/eval/lacking-log.md`).
  Ye sandbox ke pause/resume ke paar bachta hai, **par sandbox kill/reap pe kho sakta** hai
  (session-backup ise cover nahi karta). Aur per-sandbox alag hota hai. **v1 limitation.**
- **Future (backend eval ke saath):** lacking-log **Postgres** mein — global (sab users pe
  ek), durable (sandbox death ke paar). Tab ye proper cross-run learning banta hai.

---

## 7. Scenarios — alag-alag haalat

### Scenario A — sab theek (common case)
```
user: "retail companies jo hiring kar rahi" → Apify collect → Origami enrich → batch draft
har skill ke baad: eval triage → saaf → aage
end: holistic eval → goal poora, relevant, honest → koi flag
→ output deliver, koi correction nahi, lacking-log unchanged
```

### Scenario B — execution error mid-run (Origami-for-jobs)
```
user: "kaunsi companies SDR hire kar rahi" (job query)
agent: galti se Origami skill use kar leta hai (jobs ke liye)
   decisions.md: "job data ke liye Origami — wo company-data deta hai"
Origami skill poori → EVAL (mid-run):
   triage: "job query → Apify hona chahiye tha, Origami hua" → FLAG
   deep audit: AGENTS.md quote "job postings → Apify (only job source)"
             + artifact origami-input.json → clear violation
   fix-class: EXECUTION (rule theek tha), high-confidence, budget bacha
   → AUTO-FIX: Apify se collection dobara → us stage se aage
   → downstream abhi bana hi nahi tha, to bas Apify se sahi shuru
   → phir eval → theek → continue
   → lacking-log: naya pattern "Origami-for-jobs" count 1
```

### Scenario C — systemic defect
```
eval ko lagta hai galti isliye hui ki skill ka ek rule ambiguous hai
   (e.g. "enrichment" ki definition do tarah padhi ja sakti)
fix-class: SYSTEMIC
→ auto-fix NAHI. Human ko PROPOSE:
   "Problem: origami skill ka step 3 ambiguous. Fix: ye line aise likho. Impact: ye
    confusion aage nahi hoga." → ruk jao, human decide kare
```

### Scenario D — uncertain (shaq, pukka nahi)
```
eval ko output thoda off lagta hai par pukka nahi (rule clearly violate nahi)
→ auto-fix NAHI (maybe pe paid re-run mat jalao)
→ human ko FLAG: "ye result shaqi lagta hai, evidence ye, tum dekho"
```

### Scenario E — recurring → escalate
```
"Origami-for-jobs" teesri baar aaya (lacking-log count 2 tha → ab 3)
→ escalate: execution-slip se SYSTEMIC-defect
   "3 baar wahi galti = ye agent ka nahi, rule/skill ka problem"
→ human ko rule-change propose (prioritised)
```

### Scenario F — retry budget khatam
```
execution error → auto-fix → re-run → phir eval → phir wahi/naya issue → auto-fix (round 2)
→ phir bhi issue → budget khatam (max 2 rounds)
→ human ko ESCALATE: "2 baar khud sahi karne ki koshish ki, ab bhi ye issue, tum dekho"
```

### Scenario G — conversational turn
```
user: "thanks!" → koi tool/skill nahi, koi deliverable nahi
→ substantive run NAHI → eval trigger hi nahi hota
```

---

## 8. Poora end-to-end (ek nazar)

```
user request (substantive)
      ↓
agent kaam karta hai — har key decision decisions.md mein, har tool I/O artifacts mein
      ↓
── har data/action skill ke baad ──►  EVAL (mid-run)
      │                                triage → (flag?) deep-audit
      │                                ├─ execution + pukka → auto-fix + us stage se re-run → continue
      │                                ├─ systemic → propose to human, ruk
      │                                └─ uncertain / budget-out → flag to human
      │                                (recurring? → lacking-log escalate)
      ↓
result DELIVER
      ↓
── result ke baad, ek baar ──►  EVAL (end, holistic)
      │                          goal-completion, cross-skill, relevance, honesty
      │                          flags → lacking-log update
      ↓
ASK (jab human-input chahiye): "corrections apply + re-run, ya satisfied?"
```

---

## 9. Hamare architecture mein kahan baithta hai

- Ye sab **sandbox ke andar** hota hai — agent, decision-log, artifacts, eval-skill, sab
  `/home/user/` ke andar. Backend ko iske liye kuch nahi karna (v1).
- **decision-log + artifacts** pi ki session JSONL ka hissa nahi — ye alag files hain
  sandbox disk pe. (Session-backup sirf JSONL leta hai; runs/ folder alag.)
- **Template mein** ye jaayega: `evaluation` skill (`pi-config/skills/evaluation/`), aur
  AGENTS.md ke naye trigger-rules → **template rebuild `sales-agent-v3`**.

## 10. v1 limitations (jaan-boojh ke, future mein fix)

1. **Trigger agent pe depend** — eval-skill agent hi invoke karta hai (AGENTS.md rule se);
   backend guarantee **abhi nahi** (backend eval future). Pro + high thinking rule-following
   mein reliable hai, par 100% guarantee backend aane pe milegi.
2. **Lacking-log sandbox file mein** — per-sandbox, sandbox-kill pe risk. Global + durable
   (Postgres) tab jab backend eval aayega.
3. **Same-model blind-spot** — kam hai (pro + high) par zero nahi; independent backend-judge
   future mein iska pukka ilaaj.

## 11. Ek paragraph mein poora system

> Agent substantive kaam karte waqt apne key faisle `decisions.md` mein aur har tool ka
> input/output artifacts mein likhta jaata hai. Har data/action skill khatam hone pe — aur
> run ke ant mein — wo `evaluation` skill chalata hai (AGENTS.md ka must-rule): pehle sasti
> triage, phir zaroorat pade to us skill ke rules ka fresh-read + artifacts ka rule-by-rule,
> evidence-cited, adversarial audit — relevance samet. Clear execution-galti aur pukka →
> khud us stage se sahi karke aage (bounded, high-confidence, kyunki re-run paisa kharchta);
> shaq → human ko flag; rule hi galat → human ko propose (kabhi auto nahi). Baar-baar aane
> wali galtiyan lacking-log mein escalate hoti hain. Sab sandbox ke andar; backend ka
> guaranteed independent eval future mein.
