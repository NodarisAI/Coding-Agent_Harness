# Laya layer: a local decision model for people without a Jev key

Date: 2026-09-30. Files: `engine/nodaris_harness/jev.py`, `cli.py`, `onboard.py`, `scripts/laya_dataset.py`,
`scripts/laya_eval.py`. Decision record: ai-os `decisions/2026-09-29-laya-for-teammates-plan.md`.

## Problem
Teammates cannot use Jev: the key is Varun's OpenRouter key and he will not share or host it. Varun asked on
2026-09-30 for Laya to be integrated so the harness still reads prompts for them.

## Options

### A. Laya as a second backend of the Jev client
`laya-serve` speaks Jev's protocol, so the existing client, privacy path, answer parser and context lines are reused.
The only differences are the address (loopback only), no key, no spend, a shorter time budget and its own breaker.

### B. Import Laya in the hook process
The engine must stay standard-library Python 3.9 and start in milliseconds; PyTorch cannot load in a hook.

### C. A new client module for Laya
Duplicates the privacy path and the parser, and the two would drift.

## Chosen
Option A, with a `decider` setting (`jev`, `laya`, `off`) and `nodaris-harness laya status|on|off`. Training data is
built by a script on Varun's machine; the model ships only after the evaluation gate passes.

## Rejected
- B and C above.
- Installing Laya and PyTorch from the installer now: Varun decided on 2026-09-29 that packaging waits until accuracy
  is known, and the dataset is still too small.
- Relabelling with Jev in this change: it is a paid run and needs its own go-ahead at run time.

## Acceptance checks
- `engine/tests/test_laya.py`: Laya answers without a key and names itself; any non-loopback address is refused;
  patient identifiers never reach it; a stopped server adds nothing within the budget and opens only Laya's breaker;
  the setting chooses the backend and switching Laya off returns to Jev; the command works; a malformed or
  instruction-shaped answer adds nothing.
- `tests/test_laya_dataset.py`: private sessions and identified prompts are dropped, names are masked, the split is
  stable, files are 0600, output is counts only, and evaluation refuses a remote address.
