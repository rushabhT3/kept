# Safety model

`kept` places outbound calls to people about money they owe. That is a real-world side
effect with legal, financial and reputational consequences, so each requirement below names
the code that enforces it and the test that pins it.

## Explicit user intent

No call is placed without a human running a command that says so.

- `kept plan` and `kept report` open no socket at all.
- `kept run` defaults to `--simulate`, which requires an explicit `--scenario` file. Omitting
  it is an error rather than a silent no-op (`test_simulate_without_a_scenario_is_an_error_not_a_silent_no_op`).
- `kept run --live` requires **two independent gates**: the environment variable
  `KEPT_LIVE_CALLS_ENABLED=true` **and** the typed flag `--confirm PLACE-REAL-CALLS`.
  Either one alone is refused (`kept/cli.py::_guard_live`, `tests/test_cli.py`).
- `--budget N` is a hard ceiling on how many calls a single run may place. It is applied
  last, after every policy rule, so an exhausted budget always means capacity ran out rather
  than a rule fired.
- `--as-of` cannot be combined with `--live`. A real call is never placed against a
  simulated date.

## Who is never called

`kept/policy.py` refuses before it selects, and every refusal is written to the ledger with
a reason. Nine reasons exist and each has a test in `tests/test_policy.py`:

| Reason | Meaning |
| --- | --- |
| `do_not_call` | Customer flagged. Checked before anything else that could override it |
| `no_phone` | No callable number on file |
| `dispute_open` | The customer contested the invoice on a previous call. Permanently human-owned |
| `promise_open` | A live commitment already covers the invoice |
| `already_settled` | The payments feed cleared it |
| `not_yet_due` | Still inside the post-due grace period |
| `quiet_hours` | Outside calling hours **in the customer's own timezone**, via `zoneinfo` |
| `contact_frequency_exceeded` | Contacted within `min_days_between_calls` |
| `call_budget_exhausted` | The run's call ceiling was reached |

## E.164 phone numbers

`Customer.__post_init__` rejects any number that does not begin with `+`, at load time, and
the error message masks the offending number. Region and locale are carried per customer and
passed to CALL-E as `recipients[0].region` / `.locale`.

Sample data uses the reserved fictional `+1555…` range only.

## Masking

Phone numbers are reduced to `***` plus the last two digits (`kept/models.py::mask_phone`)
everywhere they leave the input files:

- the terminal call plan and run summary,
- the `call_placed` ledger entry,
- error messages raised during load.

The HTML report contains **no phone number in any form**, enforced by
`test_report_never_prints_a_phone_number`.

## Credential handling

- The API key is read from `CALLE_API_KEY` at the moment a live port is constructed, and is
  held only by the `httpx.Client` the SDK owns.
- It is never logged, never written to the ledger, and never included in a report.
- `.env.example` ships placeholders; `.env` is git-ignored.
- Missing credentials produce a refusal that points at `--simulate`, not a stack trace.

## No hidden schedules, no duplicate jobs

`kept` has **no scheduler, no daemon and no background thread**. Each command runs once and
exits. There is no state that continues dialling.

Duplicate calls are prevented by a business-derived idempotency key:

```
Idempotency-Key: kept:{invoice_id}:{cycle}:{attempt}
```

`attempt` is the number of calls the ledger already records for that invoice, so the key is
stable across retries, restarts and crashes. If the process dies between placing a call and
writing the ledger, the next run rebuilds the *same* key and CALL-E returns the original
call rather than dialling again. Pinned by
`test_replaying_a_run_after_a_lost_ledger_write_does_not_dial_again`.

## Cancellation and rollback

- Between runs there is nothing to cancel — no job exists.
- If `kept` is scheduled externally (cron, Task Scheduler), removing that entry is the entire
  cancellation path.
- Setting `do_not_call` on a customer stops all future calls to them immediately.
- Deleting `ledger.jsonl` clears promise, dispute and contact history without touching
  invoices or payments. Nothing in `kept` writes to the operator's accounting data.
- A call already in progress cannot be recalled by this app; CALL-E owns the live call.

## Financial boundaries on the call itself

The task text sent to CALL-E carries these as hard rules
(`kept/calls/scripts.py`, asserted in `test_the_task_text_carries_the_boundaries_the_agent_must_respect`):

- Identify as an automated assistant and name the creditor, on every call.
- **Never accept card numbers, CVV codes, bank credentials or one-time passcodes.** There is
  no code path in this repository that accepts a payment instrument; taking one on this call
  would place the operator inside PCI scope.
- **Never threaten** legal action, credit reporting, service suspension or any other
  consequence.
- **Never offer** a discount, waiver, settlement, instalment plan or new terms. The agent may
  only record what the customer proposes; it has no authority to agree to it.
- A stated dispute ends the collection attempt on that call and permanently thereafter.
- A request not to be called again is agreed to and confirmed.
- Voicemail receives the creditor name and callback number only — never the invoice number
  or the amount.

## Medical, legal and emergency content

Out of scope by construction. The result schema has no field for any of it, and the task
text confines the conversation to one invoice. If a customer raises a legal matter it
surfaces as a dispute, which ends collection and hands the account to a person.

## Nothing the model says is trusted with money

CALL-E's structured result is a *claim*, never a record. `kept/capture.py` re-checks the
right party, an exactly parseable amount, a readable future date inside the horizon, an
amount the invoice can carry, and the completion-confidence floor. Ten named rejection
reasons exist and every one has a test in `tests/test_capture.py`. Amounts are integer minor
units parsed with `Decimal`; ambiguous grouping is refused rather than guessed.

## Audit

`ledger.jsonl` is append-only and hash-chained: each entry commits to its predecessor's
hash. `kept verify` re-hashes the chain and fails on any edited, reordered or removed entry
(`tests/test_ledger.py`). Every call **and every decision not to call** is in it.

## Consumer collections

Out of scope. The scripts, escalation ladder and tone are written for business-to-business
receivables. Consumer debt collection carries statutory requirements — disclosure scripts,
validation notices, contact-frequency caps, dispute handling timelines — that this codebase
does not implement, and it should not be used for that without them.
