"""End-to-end checks for the offline demo (docs/DEMO_SCRIPT.md).

Each test builds the demo in a fresh temporary directory through the real CLI and
checks the exact outcome the presenter shows: the honest record passes, every
tampered variant fails with the expected reason code. No network, keys or model.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PUBKEY = "demo/keys/demo-signer.pub"


def cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    code = "import sys; from hackathon.cli import main; sys.argv = ['hackathon', *sys.argv[1:]]; "
    code += "raise SystemExit(main())"
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("offline-demo")
    for step in ("demo", "chain-demo"):
        result = cli(root, step)
        assert result.returncode == 0, f"{step} failed:\n{result.stdout}\n{result.stderr}"
    return root


def test_demo_builds_all_bundles(demo_dir: Path) -> None:
    for name in ("golden", "tampered", "tampered-rehashed"):
        assert (demo_dir / "demo" / name).is_dir(), name
    for name in ("golden", "tampered", "tampered-rehashed", "deleted-event"):
        assert (demo_dir / "demo" / "chain" / name).is_dir(), name
    assert (demo_dir / PUBKEY).is_file()


def test_golden_bundle_passes(demo_dir: Path) -> None:
    result = cli(demo_dir, "verify", "demo/golden", "--pubkey", PUBKEY)
    assert result.returncode == 0, result.stdout
    assert "RESULT: PASS" in result.stdout
    assert "[FAIL]" not in result.stdout


def test_changed_payee_fails(demo_dir: Path) -> None:
    result = cli(demo_dir, "verify", "demo/tampered", "--pubkey", PUBKEY)
    assert result.returncode != 0
    assert "RESULT: FAIL" in result.stdout
    for code in ("REVIEW_HASH_MISMATCH", "APPROVAL_NOT_BOUND", "SIGNED_PAYLOAD_MISMATCH"):
        assert code in result.stdout, code


def test_rehashed_forgery_is_caught_by_signature(demo_dir: Path) -> None:
    # The forger recomputed every hash; only the signature binding catches it.
    result = cli(demo_dir, "verify", "demo/tampered-rehashed", "--pubkey", PUBKEY)
    assert result.returncode != 0
    assert "RESULT: FAIL" in result.stdout
    assert "SIGNED_PAYLOAD_MISMATCH" in result.stdout
    assert "REVIEW_HASH_MISMATCH" not in result.stdout


def test_golden_chain_audit_passes(demo_dir: Path) -> None:
    result = cli(demo_dir, "audit-chain", "demo/chain/golden")
    assert result.returncode == 0, result.stdout
    assert "RESULT: PASS" in result.stdout


@pytest.mark.parametrize("variant", ["tampered", "tampered-rehashed"])
def test_tampered_chain_audit_fails(demo_dir: Path, variant: str) -> None:
    result = cli(demo_dir, "audit-chain", f"demo/chain/{variant}")
    assert result.returncode != 0
    assert "RESULT: FAIL" in result.stdout


def test_deleted_event_breaks_chain(demo_dir: Path) -> None:
    result = cli(demo_dir, "audit-chain", "demo/chain/deleted-event")
    assert result.returncode != 0
    assert "CHAIN_BROKEN" in result.stdout
    assert "TRACE_COMMITMENT_MISMATCH" in result.stdout


def test_wrong_key_fails(demo_dir: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    assert cli(other, "demo").returncode == 0
    result = cli(demo_dir, "verify", "demo/golden", "--pubkey", str(other / PUBKEY))
    assert result.returncode != 0
    assert "RESULT: FAIL" in result.stdout
