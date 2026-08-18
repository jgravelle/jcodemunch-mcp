"""#491: the two exclusion resolvers read global config only.

``security.py``'s ``exclude_skip_directories`` and ``exclude_secret_patterns``
called ``_config.get()`` without ``repo=``, which skips the project overlay
entirely. A project declaring either key in its own ``.jcodemunch.jsonc`` got no
effect, no warning and a successful index - the files were simply absent.

The keys themselves were sound: the same values in global config worked. That is
what isolates the defect to the missing keyword rather than to the key, the walk
or the parser.

Antecedents: #300, #187 and #304 are this shape on other keys; #301 audited about
40 call sites and lists ``get_extra_ignore_patterns`` as fixed without either of
these two; v1.108.197 fixed the three ``max_*`` resolvers and did not touch them.
"""

import ast
import json
import pathlib
import re
import subprocess

import pytest

from jcodemunch_mcp import config as cfg
from jcodemunch_mcp import security as sec
from jcodemunch_mcp.tools.get_file_content import get_file_content
from jcodemunch_mcp.tools.index_folder import index_folder

FIXTURE_REL = "tests/fixtures/fake_server.py"


@pytest.fixture
def project(tmp_path):
    """A project shipping a real ``tests/fixtures/`` module, plus a store."""
    proj = tmp_path / "proj"
    (proj / "tests" / "fixtures").mkdir(parents=True)
    store = tmp_path / "store"
    store.mkdir()
    (proj / "main.py").write_text("def main():\n    return 1\n")
    (proj / FIXTURE_REL).write_text(
        "class FakeServer:\n    def serve(self):\n        return 3\n"
    )
    for args in (
        ("init", "-q"),
        ("config", "user.email", "repro@example.invalid"),
        ("config", "user.name", "repro"),
        ("add", "-A"),
        ("commit", "-q", "-m", "A"),
    ):
        subprocess.run(["git", *args], cwd=str(proj), check=True, capture_output=True)

    class Project:
        path = proj
        store_path = str(store)

        @staticmethod
        def declare(**keys):
            (proj / ".jcodemunch.jsonc").write_text(json.dumps(keys, indent=2))
            cfg.load_project_config(str(proj))

        @staticmethod
        def index():
            return index_folder(
                path=str(proj), identity_mode="local", use_ai_summaries=False,
                storage_path=str(store),
            )

    return Project


class TestTheProjectOptOutApplies:
    """The two acceptance criteria that describe the reported failure."""

    def test_exclude_skip_directories_un_skips_the_directory(self, project):
        project.declare(exclude_skip_directories=["fixtures"])

        result = project.index()
        got = get_file_content(
            repo=result["repo"], file_path=FIXTURE_REL,
            storage_path=project.store_path,
        )

        assert got.get("content"), (
            "the project's documented opt-out did not un-skip tests/fixtures/; "
            f"skip counts were {result.get('discovery_skip_counts')}"
        )

    def test_exclude_secret_patterns_applies_from_project_config(self, project):
        project.declare(exclude_secret_patterns=["tests/fixtures/*"])

        assert sec.is_secret_file(
            "tests/fixtures/credentials.json", repo=str(project.path)
        ) is False

    def test_the_same_path_is_still_secret_without_the_opt_out(self, project):
        """Non-vacuity for the test above: the path must be judged secret when
        nothing excludes it, or the assertion proves nothing about the key."""
        assert sec.is_secret_file(
            "tests/fixtures/credentials.json", repo=str(project.path)
        ) is True


class TestWhatMustNotChange:
    """Constraints on the fix.

    ⚠ Unlike the usual both-sides controls, these are red against the pre-fix
    tree too - but only because ``repo=`` is not a parameter there, so they
    raise ``TypeError`` before asserting anything. They are constraints, not
    evidence of the defect. Each therefore also asserts the no-argument form,
    which runs identically on both sides and is the half that carries meaning.
    """

    def test_the_default_skip_list_is_unchanged(self, project):
        """With neither key set anywhere, `fixtures` stays skipped."""
        assert "fixtures" in sec.get_skip_directories()
        assert "fixtures/" in sec.get_skip_patterns()
        assert "fixtures" in sec.get_skip_directories(repo=str(project.path))
        assert "fixtures/" in sec.get_skip_patterns(repo=str(project.path))

    def test_a_global_value_still_applies(self, project, tmp_path):
        """The path the key SHIPPED with (#209) must not regress. A fix that
        moved the read from global to project would satisfy the class above and
        break every existing user."""
        store = tmp_path / "gstore"
        store.mkdir()
        (store / "config.jsonc").write_text(
            json.dumps({"exclude_skip_directories": ["fixtures"]}, indent=2)
        )
        cfg.load_config(storage_path=str(store))

        assert "fixtures" not in sec.get_skip_directories()
        assert "fixtures" not in sec.get_skip_directories(repo=str(project.path))

    def test_omitting_repo_still_resolves_global_only(self, project):
        """The parameter is optional and defaults to today's behaviour, so a
        caller with no path to offer is not broken."""
        project.declare(exclude_skip_directories=["fixtures"])
        assert "fixtures" in sec.get_skip_directories()
        assert "fixtures" not in sec.get_skip_directories(repo=str(project.path))


class TestEveryReadIsThreaded:
    """#301 enumerated ~40 call sites for this bug shape and these two were
    missed. A ratchet is cheaper than a second audit."""

    @pytest.mark.parametrize(
        "key", ["exclude_skip_directories", "exclude_secret_patterns"]
    )
    def test_no_unthreaded_read_of_either_key(self, key):
        root = pathlib.Path(sec.__file__).resolve().parent
        pattern = re.compile(
            r"_config\.get\(\s*[\"']" + key + r"[\"'][^)]*\)", re.DOTALL
        )
        offenders = []
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in pattern.finditer(text):
                if "repo=" not in match.group(0):
                    line = text[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(root)}:{line}")
        assert not offenders, (
            f"{key} read without repo=, so the project overlay is skipped: "
            + ", ".join(offenders)
        )

    @pytest.mark.parametrize(
        "func", ["is_secret_file", "get_skip_directories", "get_skip_patterns",
                 "_excluded_skip_directories"],
    )
    def test_the_resolver_accepts_repo(self, func):
        import inspect

        assert "repo" in inspect.signature(getattr(sec, func)).parameters, (
            f"security.{func} cannot honour a project value it is never given"
        )

    def test_every_local_walk_call_site_passes_repo(self):
        """⚠ Signature-only would be a false green: adding the parameter and
        leaving the callers bare changes nothing observable.

        ``index_repo`` is exempt BY NAME rather than by omission - a GitHub tree
        has no local checkout, so there is no ``.jcodemunch.jsonc`` to walk up
        to and no path to pass.
        """
        root = pathlib.Path(sec.__file__).resolve().parent / "tools"
        watched = {"is_secret_file", "get_skip_directories", "get_skip_patterns",
                   "_build_skip_dirs_regex"}
        exempt = {"index_repo.py"}
        offenders = []
        for path in sorted(root.rglob("*.py")):
            if path.name in exempt:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                if name not in watched:
                    continue
                if not any(kw.arg == "repo" for kw in node.keywords):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno} {name}")
        assert not offenders, (
            "these call a project-overridable resolver without repo=: "
            + ", ".join(offenders)
        )
