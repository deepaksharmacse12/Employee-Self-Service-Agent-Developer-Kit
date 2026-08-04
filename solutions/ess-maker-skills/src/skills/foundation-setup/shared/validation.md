<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Validation Adapter

After each FlightCheck run, translate its result into the setup validation contract.

1. Show the check description, result, and remediation to the maker.
2. Map `Passed` to `status=pass`.
3. Map `Failed`, `Warning`, `NotConfigured`, `Error`, and an unavailable result to
   `status=fail` unless the playbook explicitly permits manual attestation.
4. For permitted fallback, ask for explicit confirmation after showing the manual
   verification steps. Record `mode=manual-attested` only on a positive answer.
5. Persist the result immediately:

```text
python scripts/setup_state.py record-check --check-id {CHECK_ID} \
  --status {pass|fail} --mode {automated|manual-attested} \
  --evidence-json "{JSON_OBJECT}" [--cause-code {CODE}]
```

Never include secrets in evidence. Evidence should contain stable facts such as the
environment identifier, selected capacity model, solution identifier, starter name,
attestor role, and timestamp.
