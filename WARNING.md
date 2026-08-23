# WARNING — Autonomous Agent Shell Security

## Critical Security Warning

This project gives a Gemini API model autonomous access to a `run_shell` tool.

The current implementation executes:

```bash
bash -c "<model-generated command>"
```

with the working directory set to:

```text
/storage/emulated/0/termux
```

**There is currently NO effective dangerous-command gating and NO human confirmation for `run_shell`.**

The existing `is_catastrophic_command()` circuit breaker is disabled.

Therefore, `run_shell` must be treated as **arbitrary code execution with the permissions available to the Termux process**.

---

## What This Means

The structured filesystem tools enforce:

```text
READ_ROOT  = /storage/emulated/0
WRITE_ROOT = /storage/emulated/0/termux
```

and destructive structured operations such as deletion/promotion require human confirmation.

**`run_shell` does not obey that same write restriction.**

A model-generated Bash command can therefore bypass the structured filesystem permission layer.

The security boundary is the Android/Termux sandbox, not `WRITE_ROOT`.

---

## Potential Worst-Case Impact

If the model is malicious, compromised, manipulated by hostile input, or makes a catastrophic mistake, `run_shell` could potentially:

### 1. Destroy shared-storage data

For example, arbitrary commands could delete or corrupt files anywhere accessible to the Termux process, including:

- photos
- downloads
- documents
- source code
- project files
- backups stored in accessible locations

### 2. Modify the agent itself

The model can potentially modify or delete the agent's own source, configuration, logs, and supporting files.

This could undermine safety mechanisms that exist only inside the agent's Python code.

### 3. Read accessible secrets

The shell can potentially inspect data available to the Termux process, including environment variables and files.

Potential examples include:

- `GEMINI_API_KEY`
- SSH keys
- Git credentials
- configuration files
- tokens
- other credentials stored in accessible locations

### 4. Exfiltrate accessible data

If network access is available, arbitrary shell commands may be able to send accessible files, environment variables, or other information to an external server.

This makes unrestricted shell access a **confidentiality risk**, not merely a file-deletion risk.

### 5. Execute arbitrary programs

Bash can invoke other interpreters, scripts, binaries, package managers, and installed Termux utilities.

Consequently, a blacklist of a few dangerous commands cannot reliably make unrestricted Bash safe.

---

## What Android Still Protects

This does **not** automatically mean the model has root access to Android.

The Android sandbox remains a major boundary.

Without additional privileges or an Android vulnerability, the Termux process generally cannot freely access:

- other applications' private data
- Android system-protected files
- root-only resources

The actual reachable boundary depends on the permissions and capabilities granted to the Termux installation.

---

## Why the Current Catastrophic-Command Check Is Not Sufficient

The project contains an optional `is_catastrophic_command()` check for a small set of patterns such as destructive `rm`, filesystem formatting, and certain `dd` operations.

That check is currently disabled.

More importantly, even if enabled, a Bash blacklist is not a complete security boundary.

Equivalent effects can be produced through:

- Python
- shell indirection
- other installed interpreters
- command substitution
- pipelines
- utilities such as `find`
- scripts
- binaries

Therefore:

> **A command blacklist should not be treated as proof that unrestricted Bash is safe.**

---

## Security Assumption

Until the architecture is changed:

> **Assume that giving the Gemini model `run_shell` access is equivalent to giving an untrusted autonomous program the ability to execute arbitrary commands with the Termux process's permissions.**

Do not rely on the structured filesystem permission model to contain `run_shell`.

---

## Recommended Architectural Direction

The robust solution is to constrain the execution environment itself rather than attempting to recognize every dangerous Bash command.

Possible directions include:

1. Remove unrestricted `run_shell`.
2. Replace it with narrowly scoped tools for specific operations.
3. Run commands inside a genuinely isolated sandbox with a separate restricted identity/filesystem.
4. Put explicit human confirmation around operations that cross the intended security boundary.
5. Keep secrets unavailable to the autonomous execution environment.
6. Treat network access as a separate capability that should not automatically accompany shell access.

The central principle is:

> **Security should be enforced by the execution boundary, not by trusting the model or attempting to blacklist every dangerous command.**

---

## Current Status

This warning describes the **current architecture**.

It is not claiming that the project currently has root access to Android or unrestricted access to every file on the phone.

It is specifically warning that:

```text
Gemini
  ↓
run_shell
  ↓
bash -c
  ↓
Termux process permissions
```

bypasses the agent's intended `WRITE_ROOT` restriction.

Any future security changes should update this document and explicitly re-evaluate the threat model.
