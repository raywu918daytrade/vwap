# Codex Mobile Development SSH Setup

This file intentionally stores only public key material and setup notes.
Do not paste private SSH keys into this repository, ChatGPT files, or chat messages.

## Existing Public Keys

### Oracle / Codex VWAP

Fingerprint:

```text
SHA256:iEhAL3pJx1I5PRyoDTocLUMB9O0pm3SENpr/egO6cFo codex-cloud-vwap-20260909 (ED25519)
```

Public key:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO1Tz/GrjIQbriHmAW/YUeqtEWD5R3eQmsiwoWenrEeO codex-cloud-vwap-20260909
```

Local private key path on this Mac:

```text
~/.ssh/oracle_codex_vwap
```

### Personal Mac Key

Fingerprint:

```text
SHA256:iuHZjb2VgL5DQeBk5IcFf3QRv0xHSFG85xEM5jqjEmo raywu@mac (ED25519)
```

Public key:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMBkrBNBb5fhtfyU35ClimXXP1n8IH53K5g/qfvntqE7 raywu@mac
```

Local private key path on this Mac:

```text
~/.ssh/id_ed25519
```

## Recommended Mobile Development Setup

For mobile development while this Mac is powered off:

1. Use Codex cloud with the GitHub repository:

```text
https://github.com/raywu918daytrade/vwap.git
```

2. Configure the Codex cloud environment setup script as:

```bash
bash scripts/codex_cloud_setup.sh
```

3. Do normal development in Codex cloud by editing the repo and opening a PR or pushing a branch. After changes land on `main`, GitHub Actions deploys to Oracle with the repository secret `ORACLE_SSH_PRIVATE_KEY`.

This is the preferred path. Codex cloud does not need direct SSH access to Oracle for ordinary code changes.

## Optional Direct Oracle SSH From Codex Cloud

Only use this if you explicitly need Codex cloud to inspect production logs or run Oracle diagnostics.

1. Add only the Oracle / Codex VWAP public key to the Oracle server user's `~/.ssh/authorized_keys`.

2. Store the matching private key as a Codex cloud environment secret, not as a repository file.

Suggested secret name:

```text
ORACLE_CODEX_VWAP_SSH_KEY
```

3. `scripts/codex_cloud_setup.sh` will write the secret to a temporary SSH key file with strict permissions:

```sh
mkdir -p ~/.ssh
chmod 700 ~/.ssh
printf '%s\n' "$ORACLE_CODEX_VWAP_SSH_KEY" > ~/.ssh/oracle_codex_vwap
chmod 600 ~/.ssh/oracle_codex_vwap
```

4. Connect with:

```bash
ssh vwap-oracle
```

5. Prefer a dedicated Oracle user with limited permissions for Codex cloud work. If the key is ever exposed, remove the public key from `authorized_keys` and create a new dedicated key.
