<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Flowness Ledger Core Open Alpha quickstart

This public walkthrough builds the Ledger Core wheel, installs it into a fresh
virtual environment, runs the installed `flowness-ledger-demo` command, and
reopens the result with the verifier.

Run these commands from the Flowness repository root. The intended first
independent full-Open-Alpha coordinate is **Linux aarch64 with CPython 3.12**.
Ledger Core declares Python 3.11 or newer on POSIX-local filesystems, but every
coordinate remains unverified until a non-author run and fresh jury bind the
exact release bytes.

## 1. Select Python and create isolated paths

```bash
cd public-core/flowness-ledger-core

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
"$PYTHON_BIN" --version
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'

RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/flowness-ledger-alpha.XXXXXX")"
WHEEL_DIR="$RUN_ROOT/dist"
VENV_DIR="$RUN_ROOT/venv"
DEMO_DIR="$RUN_ROOT/demo"
WHEEL="$WHEEL_DIR/flowness_ledger_core-1.0.0a1-py3-none-any.whl"
```

`RUN_ROOT` and `DEMO_DIR` must be new paths. The demo refuses to overwrite a
non-empty directory.

## 2. Build the wheel

```bash
"$PYTHON_BIN" tools/build_wheel.py --output "$WHEEL_DIR"
test -f "$WHEEL"
```

The committed builder packages the current `src/flowness_ledger_core` code and
the `flowness-ledger-demo` entry point without downloading build dependencies.
Release signing and publication are separate release-sequence operations.

## 3. Install the exact wheel without an index

```bash
test ! -e "$VENV_DIR"
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --no-index "$WHEEL"
"$VENV_DIR/bin/python" -c 'import flowness_ledger_core; print(flowness_ledger_core.__file__)'
test -x "$VENV_DIR/bin/flowness-ledger-demo"
```

`--no-index` ensures this step installs the wheel just built from the checked
out source instead of resolving another Ledger Core package from a registry.

## 4. Run and verify the installed CLI

```bash
test ! -e "$DEMO_DIR"
"$VENV_DIR/bin/flowness-ledger-demo" --demo-dir "$DEMO_DIR"
"$VENV_DIR/bin/flowness-ledger-demo" --verify-demo-dir "$DEMO_DIR"
```

The first command creates the ledger, demo manifest, and recovery report. The
second command reopens and verifies them through the installed console script.
Do not substitute `PYTHONPATH=src`; the quickstart is intended to cover the
wheel-installed path.

## What this proves

This run proves that, on the machine where it is executed, the current Ledger
Core source can become an installable wheel and its installed CLI can exercise
and reverify these bounded behaviors:

- pending proposals remain invisible to the committed reader;
- accepted proposals become visible and rejected proposals remain invisible;
- conflicting terminal decisions fail;
- stale projections are rejected; and
- an incomplete final JSONL tail follows deterministic recovery.

Before release, a non-author run must separately bind the exact sealed export,
Linux aarch64 / CPython 3.12 clean-room result, fresh package-jury decision,
and artifact identities. Those records are pending and intentionally absent
from this mutable candidate; this developer quickstart cannot substitute for
them.

It also does not establish distributed consensus, exactly-once delivery,
production reliability, durability across every filesystem, performance,
security hardening, a wider platform matrix, or external adoption.
