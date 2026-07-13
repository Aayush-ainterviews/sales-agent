# System Flow — poora naksha (simple Hinglish)

> Ye wahi cheez hai jo `system-flow.md` mein hai, bas aasaan bhaasha mein. Postgres
> aa jaane ke BAAD ka flow — request aane se le kar jawaab tak, har step. Angrezi
> technical wala version: [system-flow.md](system-flow.md).

---

## 0. Kaun-kaun hai is system mein

```
Frontend  ──►  Backend (FastAPI)  ──►  E2B Sandbox (har user ka apna)
 (browser)         │      │                    │
                   │   Postgres           pi daemon (agent yahan chalta hai)
                   │  (durable memory)          │
                   │                        Gemini ko call karta hai
              (ye sab backend ke andar:)
```

Backend ke andar ke hisse (har ek ka ek kaam):

| Hissa | Kaam |
|---|---|
| **app.py** | 4 darwaze (endpoints) — messages, steer, abort, reset |
| **db.py** | Postgres se connection pool + `sessions` table banata hai |
| **registry.py** | Postgres mein padhta/likhta hai — "kaun user, kaunsa sandbox" + poora log |
| **sandbox_manager.py** | user ka sandbox dhoondo ya naya banao |
| **daemon_client.py** | sandbox ke andar pi daemon start/sambhal, secrets daalo |
| **daemon_pipe.py** | ek sandbox ke daemon se baat karne ka live "phone" (PTY) |
| **turn_runner.py** | ek turn ka poora lifecycle + "ek user, ek turn" wala rule |
| **session_backup.py** | turn ke baad sandbox se conversation ka log nikaalta hai |
| **template/** | ek baar bana hua frozen image (node 22 + pi + skills) |

**Teen jagah data rehta hai — teeno ka kaam alag:**
- **Postgres `sessions` table** — pakki memory: "kaun user → kaunsa sandbox" + poori conversation ka log. Backend restart ho to bhi bacha rehta hai.
- **Sandbox ki disk** — pi ki conversation file (JSONL). Jab tak sandbox zinda hai, ye asli source of truth.
- **Backend ki RAM** — live daemon connections (`_pipes`), aur "kaun abhi busy hai" (`_busy`). Ye disposable hai — restart pe udd jaata hai, par dobara ban jaata hai.

---

## 1. Sabse pehle — template ready hona chahiye (ek baar ka kaam)

`template/build_template.py` chala ke E2B pe ek image banti hai:
- base image → **node 22** (base ka node 20 pi ke liye purana tha) → **pi install** → hamari `pi-config/` (settings, AGENTS.md, 3 skills) + `GOAL.md` copy.
- E2B account mein naam se save: **`sales-agent-v1`**.
- **Isme koi secret nahi** (keys baad mein daemon start pe aati hain).

Iske baad `Sandbox.create("sales-agent-v1")` ~1-2 second mein ready sandbox de deta hai — request ke waqt kuch install nahi hota.

---

## 2. MAIN FLOW — ek purana user message bhejta hai

Rohan (jiska sandbox pehle se hai, abhi paused) message bhejta hai:

```
POST /users/rohan/messages   { "message": "20 retail leads nikaalo" }
Header: Authorization: Bearer <rohan ka token>
```

### Step 1 — Auth (kaun hai ye?)
- Token se pata karo user kaun hai. Token galat → **401**. Token kisi aur ka, par URL mein `rohan` → **403**.
- Pehchaan **token se** aati hai, URL pe blindly bharosa nahi.

### Step 2 — Turn ka slot pakdo (409 wala rule)
- `try_claim("rohan")` turant chalta hai (koi slow kaam nahi).
- Agar rohan ki ek turn **already chal rahi** hai → **409** ("busy, pehle wali khatam hone do")।
- Warna rohan ko "busy" mark karo, aage badho.
- Yahi "pehle slot pakdo, fir kaam" wala tareeka do requests ke race ko rokta hai.

### Step 3 — Sandbox lao (Postgres se padho)
- Postgres se: `rohan → sbx_abc123`.
- `Sandbox.connect("sbx_abc123")` → paused sandbox ~1s mein **resume**.
- (Agar connect fail — sandbox mar chuka tha — to wahi code naya bana ke Postgres row update kar deta hai. Alag "recovery" code nahi.)

### Step 4 — Daemon chalu hai ya nahi, pakka karo
- Kya is sandbox ka live daemon-phone (`_pipes` mein) hai? Ek "hello?" (probe) bhejo.
  - **Zinda** → wahi use karo.
  - **Mara/nahi** (resume ke baad daemon hamesha mara hota hai — pi pause mein zinda nahi rehti) → purana saaf karo (`pkill -x pi`) → naya PTY kholo → `pi --mode rpc -c` start karo, **secrets daal ke** (`GEMINI_API_KEY`, `ORIGAMI_API_KEY`, `APOLLO_API_KEY`, `APIFY_TOKEN`) → "hello?" ka jawaab aane tak ruko.
  - `-c` ka matlab: purani conversation continue karo → agent ko pichhli baatein yaad rehti hain.

### Step 5 — Message bhejo aur stream shuru karo
- Message PTY ke through daemon ke andar chala jaata hai.
- `turn_start` event frontend ko jaata hai.
- Fir loop: daemon se jo bhi event aaye, use turant frontend ko bhej do —
  - `message_update` (agent "type" kar raha), `tool_execution_*` (agent bash/tool chala raha), ... `agent_end` tak.
  - Har event SSE se **live** frontend pe dikhta hai.
- **Do timer saath mein chalte hain:**
  - Har 5 min: sandbox ka idle-timer 15 min pe reset (taaki kaam ke beech pause na ho).
  - 20 min ki hard limit: paar hui to turn abort + `turn_error`.

### Step 6 — Sandbox ke andar pi actually kya karti hai
- pi context banati hai (system prompt + purani baat + skills) → **Gemini** ko call → model faisla karta hai ("Origami se retail leads laao") → pi use tool ki tarah chalati hai (bash → skill script) → result model ko wapas → agla faisla → ... → final jawaab.
- **Har event pi apni disk-file (JSONL) mein khud likhti jaati hai** — real time. Ye hamara kaam nahi, pi ka built-in.
- Gemini ki key daemon ke env mein hai, isliye LLM call **sandbox ke andar se** jaati hai.

### Step 7 — Turn khatam
- `agent_end` pe: **slot turant chhodo** (`_release`) — taaki rohan ki agli turn foran shuru ho sake (DB likhne ka intezaar na kare).
- Fir save: sandbox se poora log padho → Postgres mein daalo: `UPDATE sessions SET log=... WHERE user_id='rohan'`. **Yehi backup hai** — koi R2/S3 nahi. (Fail ho to bhi turn nahi tootti — best-effort.)
- SSE stream khatam. Frontend ke paas poora jawaab.

### Step 8 — Khaali baithe → pause
- 15 min koi request nahi → E2B sandbox **pause** → **₹0**, sab kuch disk pe safe. Agli request pe wapas resume (Step 3).

---

## 3. Naya user (pehli baar) — sirf 2 farak

§2 jaisa hi, bas Step 3-4 mein:
- Postgres mein **koi row nahi** → `Sandbox.create(...)` se naya sandbox → row likho.
- Sandbox mein **koi session nahi** → daemon **bina `-c`** ke start (fresh). "Session hai ya nahi" disk dekh ke tay hota hai — andaaza nahi.

---

## 4. Ek saath kai users — isolation kaise bachti hai

3 users ek saath `/messages` bhejte hain:
- Har ek ka apna thread, apna slot, apna sandbox, apna daemon-phone (`_pipes` mein `sandbox_id` se alag).
- User A ke events sirf A ke phone se; A ka `/steer` sirf A ke daemon tak. **Ek doosre ko chhute tak nahi** (multiuser test 14/14 se verified).
- **Postgres pe ab manual lock nahi chahiye** — pool har thread ko apna connection deta hai (Phase-3 wali shared-connection wali dikkat khatam).
- Isolation teen cheezon pe tiki: alag VM + `sandbox_id`-keyed phone + per-user slot.

---

## 5. Turn ke beech — steer aur abort

**Steer** — `POST /users/rohan/steer { "message": "Tesco chhod do" }`
- rohan ka live sandbox `_active` se dhoondo → us phone pe steer bhejo.
- pi ke andar: message ek queue mein jaata hai. pi **current tool khatam** hone deti hai, fir agli LLM call se pehle message daal deti hai → agent plan badal leta hai.
- (Interrupt nahi: agar 30s ki bash chal rahi hai, steer uske baad lagta hai.)
- Us user ki koi turn hi nahi chal rahi → **409**.

**Abort** — `POST /users/rohan/abort`
- Turn saaf-suthre band → daemon zinda rehta hai.

---

## 6. Kuch toot jaaye to — system khud theek kaise hota hai

| Kya toota | Kaise pata chala | Kya hota hai |
|---|---|---|
| **Daemon crash** | agli baar probe fail | `pkill -x pi` → `-c` se restart → conversation bachi (file mein) |
| **Sandbox pause ho gaya** | agli request connect karti hai | resume ke baad daemon mara → `-c` se restart (normal hai) |
| **Sandbox E2B ne saaf/kill kar diya** | connect error deta hai | naya bana → **Postgres ka log us naye sandbox mein wapas likho** (daemon start se pehle) → `pi -c` continue → history bachi → row update |
| **Turn atak gayi** (tool loop) | 20-min watchdog | abort + error; ab tak ka kaam file mein safe |
| **Backend redeploy** | daemons ko EOF | Postgres bacha rehta hai; RAM khaali, par agli request sab dobara bana leti hai |
| **Mare hue daemon ko probe** | send fail | pakad ke "zinda nahi" maano → restart (request crash nahi) |

**Ek line ka nichod:** sandbox ki JSONL file hi asli anchor hai. RAM ki cheezein (phone, slot) khoyein to dobara ban jaati hain; conversation isliye bachti hai kyunki wo ek file hai, jise `-c` se continue kar lete hain.

---

## 7. Do zaroori sach jo code mein baithe hain

1. **Chalte daemon ka process-naam sirf `pi` hai** (pi apna naam badal leti hai) — isliye maarne ko `pkill -x pi`, dhoondhne ko `pgrep -x pi`.
2. **pi pause/resume mein zinda nahi rehti** — resume ke baad hamesha `-c` se restart; purana PTY reconnect kabhi mat karo (wo mara hua bhi "connect" ho jaata hai — dhoka).

---

## 8. Ek zaroori deployment niyam — sirf 1 backend instance

Live daemon-phones (`_pipes`), slots (`_busy`) sab **backend ki RAM** mein hain, ek process ke andar.
Agar Railway pe 2 instances chalein, to rohan ki stream instance-1 pe ho aur uska `/steer`
instance-2 pe pahunche (jahan uska phone hai hi nahi) → steer fail. Isliye **abhi 1 instance**
(5-10 users ke liye kaafi). Postgres shared aur durable hai; RAM wali daemon-state nahi — yahi
single-instance ki wajah. 2+ instances baad ka kaam, abhi nahi.

---

## 9. Kya abhi nahi bana

- **Ye Postgres migration khud** — upar ka doc target hai; code abhi bhi SQLite pe. Yehi agla kadam.
- **Outreach path (Phase 4)** — `submit_batch` tool, approval queue, send karne wala hissa. Agent research + draft kar sakta hai, par **bhej nahi sakta** (na tool, na creds, na rasta). Bhejna backend ka kaam hoga, human approval ke baad.
- **Ops (Phase 5)** — secrets store, proper logs, Railway pe deploy.
- **Deferred** (design ready, trigger noted): verifier/monitor, stage evals, scoped tokens, web-search, real frontend.

---

## 10. Ek paragraph mein poora system

> Request token se apni pehchaan deti hai, us user ka ek-turn-slot pakadti hai (nahi to 409), fir us user ka apna E2B sandbox connect-ya-banati hai aur uske andar `pi` daemon ko user ke secrets ke saath chalu karti hai. Message PTY se daemon ke andar jaata hai; pi **sandbox ke andar** LLM loop chalati hai aur har event ek file mein likhti jaati hai, jabki backend wahi events frontend ko live (SSE) bhejta hai aur sandbox ko jagaaye rakhta hai. Turn khatam hone pe slot chhoot jaata hai aur poora log user ki Postgres row mein likh diya jaata hai. Alag users poori tarah parallel aur alag VM se isolated; beech mein user steer ya abort kar sakta hai. RAM ki koi cheez marey to Postgres + session file se `-c` lagaake dobara ban jaati hai. Poora system = ek backend process (ek instance) jo N per-user sandboxes se baat karta hai, aur Postgres uski pakki yaaddaasht hai.
