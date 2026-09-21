# nju.ehall — supervised ehall transactions

This extension implements the business half of F07: application discovery,
transaction inspection, material/field mapping and preview planning for one
confirmed low-risk transaction.  It never touches a browser itself.

* Read-only work uses the host `host.browser.*` capability (bounded structured
  snapshots only; no cookies, storage state, passwords, screenshots or raw HTML).
* `ehall.fill_form` and `ehall.submit` are host-executed Tool Gateway actions.
  They require an R2 approval and are routed by tool capability, never by
  extension id.
* `ehall.submit` only clicks after the host's `PA_BROWSER_SUBMIT_ENABLED` gate
  and a fresh, unconsumed approval; a lost response becomes `UNKNOWN` and is
  resolved by the read-only `ehall.reconcile` flow tracking.
* The deployment origin is non-secret host configuration
  (`config.browser_origin`); it must also be allow-listed in
  `PA_BROWSER_ALLOWED_ORIGINS`.
* `adapters/proof.json` pins the human-verified page fingerprint(s) of the
  transaction page.  A live page that does not match is
  `UNKNOWN_PAGE_VERSION`/R3 and is never filled or clicked.  The value shipped
  in this repository was captured against the deterministic local test fixture
  (the extension worker tests); it must be replaced with the fingerprint of the
  real `ehall.nju.edu.cn` transaction page during the user-run real acceptance,
  which is still `NOT_RUN`.
* The final action declares its real network target (`method`/`target_path`,
  origin injected from the deployment origin).  The host only allows the single
  matching write request during the submit critical section; delayed autosaves,
  second POSTs and rewritten form actions are aborted and counted.
