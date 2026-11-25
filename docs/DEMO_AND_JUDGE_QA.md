# Demo script and answers for judges

## The critique we are answering

> "This flow is too simple. Judges see a simulated phone, preset values and a dropdown of
> scenarios. It looks like we fed in values that give the answer we wanted."

That was a fair reading of the old demo: the scenario dropdown even printed the expected
decision next to each scenario (`1. Ramesh: digital arrest → HOLD`). The engine behind it was
real (two trained coercion models, an RBIH-trained mule model, rules and a fusion matrix), but
the demo gave no way to see that.

The demo now has **four independent ways to show the results aren't scripted**. Use them in this order.

| # | What the judge sees | Why it can't be scripted |
|---|---|---|
| 1 | **Live capture** on the Pay screen | Behaviour signals are *measured* from how the judge types: pauses, bursts, paste, tab switches, clock. We don't know them in advance. |
| 2 | **What-if** tab | The real pipeline re-runs with one signal removed at a time. A lookup table can't answer "what if the caller were a saved contact?" |
| 3 | **Blind Test Lab: one case** | A random row from the 30% held-out test split. The judge makes their own call, then sees the engine's decision, then the sealed ground truth. Mistakes are shown too. |
| 4 | **Blind Test Lab: stress test** | 250 to 1,000 random held-out payments scored in about a second. The confusion matrix is computed live and changes with every draw, staying in a band. |

## 5-minute demo script

1. **Open the Pay screen (Live capture is on by default).** Say: *"Nothing here is preset. The
   left panel shows what the phone measures while you use it."*
2. **Ring the phone:** Device events → Unknown caller → **Ring phone** → Accept on the phone.
   The call clock runs (10× time compression for the demo, stated on screen).
3. **Hand the keyboard to a judge.** Ask them to type any UPI ID (try `verify.desk.2291@demoupi`)
   and an amount. Tell them they can hesitate, paste, or switch tabs. The **Event log** shows each
   measured event live (`MEAS Paused 2.5 s while typing the amount`).
4. **Pay.** The decision comes back (usually WARN). The analysis panel is tagged **live-captured**.
5. **Open What-if.** *"Each row is the same models re-run with one signal removed."* (In our test run,
   removing the call dropped the coercion score by 11 points.) The amount sweep shows a continuous response.
6. **Open Inputs & sources.** Every value with its source (Measured / Emulated / User / History)
   and the Android API that supplies it in production.
7. **Blind Test Lab → Draw a random case.** Ask a judge to vote Genuine or Coerced. Show the engine,
   then reveal the truth. Then **Stress test → 1000 → Run** twice: the numbers change and the
   training-row overlap stays at 0.

Guided scenarios are still there (Signal console → Guided scenarios) for a scripted story,
but the expected outcome is **hidden until the engine decides**, and the console switches to
*Manual override* and labels the result *guided scenario*.

## Likely questions and honest answers

**"Isn't the phone call fake?"**
Yes, in the browser. A web page can't read call state, screen sharing or installed apps, so
those are *emulated* and labelled purple everywhere. In a bank's Android SDK they come from
`TelephonyManager` (call state), `ContactsContract` (is the caller saved), `MediaProjection` /
display listeners (screen share) and `PackageManager.firstInstallTime` (AnyDesk / TeamViewer
installed recently). The Inputs tab names the API for each one. Behaviour signals (typing,
pauses, paste, app switches, time on screen, clock) are *measured for real* in this demo,
because a web page can see them.

**"Your model was trained on synthetic data."**
Yes, and we say so on every screen. The Blind Test Lab uses only the held-out 30% of that data,
checks on every draw that no row was in the training split, and shows false alarms and misses
next to the hits. It proves the *design* works, not real-world accuracy. The mule model and the
payee profiles use real RBIH accounts (pseudonymised).

**"Why are there so many false alarms?"**
The stress test shows it openly: against the simple "new payee + 5× amount" rule, SafePay Shield
catches far more coerced payments (recall roughly 75–93% vs 45–60% across our draws) and can *hold*, which the rule
can't. But it interrupts more genuine payments, mostly with a WARN (a pause with questions),
not a hold. This is the known limitation in the README. The planned fix is to re-tune the
first-pass WARN cut-off on the validation split against a stated false-alarm budget.

**"What-if says calm typing makes it *riskier*. Why?"**
That's the model's honest output, not a script. Tree models learn interactions from the
synthetic data, so a single feature can push the "wrong" way in a particular context. We show it
rather than hide it. It's also a reason the decision isn't the ML score alone: severe rules
(R1) and the fusion matrix sit on top.

**"Could you have tuned on the test set?"**
Two rule changes were made after one look at the test set, and the README and model card
disclose them. Model cut-offs were set on a validation split carved from the training data.

**"How fast is it?"**
`/api/assess` responds in about 150 ms in-process (Evaluation page). The lab's stress test scores
1,000 payments in about 1 s, because the exact SHAP value for the gate is cached per held-out row
(`scripts/build_lab_cache.py`). Probabilities, rules and decisions are computed on every draw.

## What changed (engineering summary)

- `safepay/challenge.py` + `safepay/api/lab.py`: `GET /api/lab/random` (random held-out rows,
  seeded and replayable, leak check, live metrics, baseline comparison) and
  `POST /api/lab/what-if` (single-signal counterfactuals + amount sweep). Nothing is saved.
- SHAP shortcut: exact SHAP only where the payee/amount gate can change the score. A test checks
  the score and band match full SHAP.
- `static/js/pay.js`: live capture (keystroke timing, paste, visibility changes, clock), an
  incoming-call overlay with a real-time call clock, an event log, What-if and Inputs tabs, and
  hidden scenario outcomes.
- New design system (`static/css/app.css`) in the Flowmatic ERP visual language: white sidebar and top bar,
  navy #1A5276 primary, flat bordered cards, Public Sans, Lucide-style SVG icons, light and dark themes.
- Fix: `xgboost` is now in `requirements.txt`. The saved coercion models are XGBoost, so a fresh
  install previously started with the coercion model **unavailable** (one test failed).
- `tests/test_phase8_lab.py`: 14 tests (held-out guarantee, seed replay, strict JSON, what-if
  agrees with `/api/assess`, SHAP shortcut equivalence).
