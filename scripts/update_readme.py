from pathlib import Path
from datetime import datetime
import os
import re


START = "<!-- REPORT_UPDATE_START -->"
END = "<!-- REPORT_UPDATE_END -->"


def _repo_root() -> Path:
    # scripts/update_readme.py -> repo root is parents[1]
    return Path(__file__).resolve().parents[1]


def _format_time() -> str:
    """
    Prefer the GitHub Actions run time (UTC) if available, otherwise local time.
    """
    # Actions provides an ISO8601 string like 2025-12-23T19:58:12Z in GITHUB_RUN_STARTED_AT
    iso = os.getenv("GITHUB_RUN_STARTED_AT", "").strip()
    if iso:
        # Keep it simple and readable
        # Example output: 2025-12-23 19:58 UTC
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")

    # Local fallback
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def update_readme() -> None:
    root = _repo_root()
    readme = root / "README.md"
    if not readme.exists():
        raise FileNotFoundError(f"README.md not found at: {readme}")

    text = readme.read_text(encoding="utf-8")

    if START not in text or END not in text:
        raise ValueError(
            f"Markers not found. Make sure README contains:\n{START}\n...\n{END}"
        )

    now_str = _format_time()
    replacement_block = f"{START}\n" f"🕒 Last report update: **{now_str}**\n" f"{END}"

    # Replace everything between markers (including markers) with replacement_block
    pattern = re.compile(
        re.escape(START) + r".*?" + re.escape(END),
        flags=re.DOTALL,
    )
    new_text, n = pattern.subn(replacement_block, text, count=1)

    if n != 1:
        raise RuntimeError(f"Expected to replace 1 block, replaced {n} blocks.")

    if new_text != text:
        readme.write_text(new_text, encoding="utf-8")
        print(f"README updated: {now_str}")
    else:
        print("README already up to date (no changes).")


if __name__ == "__main__":
    update_readme()
