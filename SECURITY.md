# Security

Report vulnerabilities privately through GitHub's "Report a vulnerability" form on this
repository rather than in a public issue. Include steps to reproduce and the affected version.

Design notes relevant to reviewers:

- The Dhan access token is stored encrypted (AES-256-GCM) and is never passed to the scripts the
  AI assistant writes. Scripts reach Dhan only through a local gateway that holds the token and
  allows read endpoints only.
- Every script runs in a separate subprocess with a scrubbed environment, a wall-clock timeout
  and an output cap.
- The library exposes no order placement, modification or cancellation.
- The MCP endpoint is unauthenticated by design (internal host); the URL carries a secret path.
