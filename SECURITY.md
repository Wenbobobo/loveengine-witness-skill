# Security policy

## Reporting

Do not open a public issue for a suspected vulnerability, leaked credential, or
private operational detail. Use GitHub's private vulnerability reporting for
this repository when it is available, or contact the repository owner through
an already trusted private channel.

Public issues are appropriate for ordinary bugs, documentation gaps, and
questions that contain no secrets.

## Supported scope

The current source is an experimental candidate. Security fixes target the
latest `main` branch; historical tags and archived M0-M6 compatibility readers
are retained for verification but are not production deployment claims.

## Never include

- private keys, mnemonics, keystores, passwords, or RPC credentials;
- Pilot write tokens or signer endpoint credentials;
- private hostnames, SSH keys, known-hosts contents, or non-public evidence;
- full logs or transcripts before running the repository secret scanner.

Before sharing a diagnostic artifact, run:

```powershell
uv run python .\tools\scan_secrets.py --root <artifact-directory>
```
