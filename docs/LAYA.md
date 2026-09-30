# Laya: the local decision model

Laya reads each prompt and answers the same questions as Jev: what kind of work it is, how much effort it deserves,
whether it is security or verification work, and how the reply should be shaped. It runs on your own machine, so it
needs no Jev key and costs nothing per prompt. Laya is an Apache-2.0 project (github.com/NandhaKishorM/laya); its
server, `laya-serve`, speaks Jev's wire protocol (`POST /v1/systemone`), and the harness reuses its Jev client for it.

## Status (1.1.0)
- **In the harness:** the `decider` setting (`jev`, `laya` or `off`), the Laya backend with its own failure breaker,
  and `nodaris-harness laya status|on|off`. Laya is reached only on 127.0.0.1, localhost or ::1; any other address is
  refused. Prompts go through the same redaction, name masking and credential masking as Jev.
- **Training data:** `scripts/laya_dataset.py` builds it on Varun's machine from Jev's logged answers (153 usable
  rows on 2026-09-30: 124 for training, 29 held out). That is too few; Laya's own fine-tune used about 2,000.
- **Not done yet:** the Jev relabel run that grows the dataset (approved on 2026-09-29 for under $1 of OpenRouter
  credit), the fine-tune, the accuracy gate, and packaging the checkpoint for teammates. Until the gate passes,
  `laya on` works only with a Laya server you run yourself.

## Run a Laya server on your machine
These steps install software, so run them yourself. They create an isolated environment and change nothing
system-wide.

```bash
uv venv ~/.nodaris-harness/laya/env --python 3.12
uv pip install --python ~/.nodaris-harness/laya/env/bin/python "laya[serve]"
```

Start the server so it listens on this machine only. `laya-serve` binds 0.0.0.0:8000 by default, which would expose
it to your network; bind it to the loopback address instead. Check `laya-serve --help` for a host option first. If
it has none, do not run it on a shared network.

Then run `nodaris-harness laya on` and `nodaris-harness laya status`. The status command sends one fixed synthetic
question and reports whether the server answered and how fast.

## Train and check it (maintainers)
1. `python3 scripts/laya_dataset.py`: build `train.jsonl` and `holdout.jsonl` under `~/.nodaris-harness/laya/data`
   (mode 0600, this machine only; counts are printed, text never is).
2. Fine-tune from the English checkpoint with the Laya project's fine-tuning recipe on the training file.
3. Serve the fine-tuned checkpoint, then run `python3 scripts/laya_eval.py`. It reports agreement with Jev and the
   majority-class baseline for each question. It exits 0 only when type, effort and reply shape each reach at least
   0.75 agreement and beat the baseline.
4. Only then publish the checkpoint for teammates, with its SHA-256 recorded in the harness.

## What is never sent or kept
The dataset holds redacted, masked prompt text and Jev's answers; it never leaves the machine and is never committed.
Sessions from lifeos-local, the EAD case, job search, careerdesk and the Becoming Project are excluded, and so is any
prompt the redactor refuses. Team sync sends Laya counts only, never prompt text.
