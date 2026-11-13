import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from licence_check import is_banned  # noqa: E402


def test_bans_agpl_and_gpl_variants():
    for text in ("AGPL-3.0", "GNU Affero General Public License v3",
                 "GPL-2.0-only", "GNU General Public License v3 (GPLv3)"):
        assert is_banned(text), text


def test_allows_permissive_and_lgpl():
    for text in ("MIT", "Apache-2.0", "BSD-3-Clause", "LGPL-2.1",
                 "GNU Lesser General Public License v3 (LGPLv3)",
                 "GNU Library or Lesser General Public License (LGPL)"):
        assert not is_banned(text), text
