# Live verification dataset

One customer, one overdue invoice, `max_calls_per_run: 1`. Used only to prove the CALL-E
integration places a real call and returns a structured result.

`customers.csv` ships with the unroutable placeholder `+910000000000`. Put a number **you
own and consent to being called on** in its place before running anything here. This folder
never carries a real person's number into version control.

Two settings differ from `../policy.json` and both are deliberate:

- `quiet_hours` is widened to 22:00–07:00 because the recipient is the operator and has
  agreed to the call time. Production data should keep the narrower default.
- `min_days_between_calls` is 0 so a re-take of the demo recording is not suppressed by the
  contact-frequency rule.

Everything else — the fail-closed capture gate, the idempotency key, masking, the on-call
boundaries — is unchanged.
