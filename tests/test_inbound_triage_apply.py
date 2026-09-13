"""The triage plan can never exceed POLICY section 2 for its category and
confidence, whatever the model returned (DESIGN section 2).

Red arms: a `medium` question producing a draft; a `low` anything
producing more than unknown + needs-human; a security result with any
comment or human label; a duplicate without `duplicate_of`; the owner's own
issue getting a draft; a malformed result being acted on as if classified.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INBOUND = ROOT / ".github" / "inbound"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, INBOUND / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


at = _load("apply_triage")


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch):
    # A test here must never reach the real `gh` with the developer's
    # credentials. The first #670 red run did exactly that (`gh` answered
    # "Could not resolve to a Repository with the name 'o/r'", by luck of the
    # placeholder name): a test that forgot to stub
    # fails loudly instead. Tests that record calls re-patch `_gh` over this.
    def refuse(args, repo):
        raise AssertionError(f"unstubbed gh call: {args} -R {repo}")

    monkeypatch.setattr(at, "_gh", refuse)


def _r(**kw):
    base = {
        "issue": 42,
        "category": "question",
        "confidence": "high",
        "evidence": ["how do I x?"],
    }
    base.update(kw)
    return base


def test_high_question_from_a_stranger_drafts_and_labels_only():
    p = at.plan(
        _r(draft="See docs/CONFIGURATION.md section 3."), "someone", "jgravelle"
    )
    assert p["add"] == ["inbound:question", "question"]
    assert p["remove"] == ["inbound:queued"]
    assert p["comment"] is None
    assert p["draft"]["body"].startswith("See docs/")


@pytest.mark.parametrize("conf", ["medium", "low"])
def test_below_high_never_drafts_or_comments(conf):
    p = at.plan(_r(confidence=conf, draft="an answer"), "someone", "jgravelle")
    assert p["comment"] is None and p["draft"] is None
    assert "needs-human" in p["add"]
    if conf == "low":
        assert p["add"] == ["inbound:unknown", "needs-human"]
    else:
        assert p["add"] == ["inbound:question", "needs-human"]


@pytest.mark.parametrize("conf", ["high", "medium", "low"])
def test_security_is_label_and_needs_human_only_at_every_confidence(conf):
    p = at.plan(
        _r(category="security", confidence=conf, draft="do not post me"),
        "someone",
        "jgravelle",
    )
    assert p["add"] == ["inbound:security", "needs-human"]
    assert p["comment"] is None and p["draft"] is None


def test_duplicate_high_is_the_one_unattended_comment():
    p = at.plan(
        _r(
            category="duplicate",
            duplicate_of=7,
            evidence=["first sentence", "second sentence", "third"],
        ),
        "someone",
        "jgravelle",
    )
    assert p["comment"]["issue_to"] == 42
    assert "#7" in p["comment"]["body"] and "third" not in p["comment"]["body"]
    assert "close" in p["comment"]["body"].lower() and p["draft"] is None
    assert "duplicate" not in p["add"], (
        "the human `duplicate` label implies a verdict and is never applied"
    )


def test_duplicate_without_a_target_is_a_schema_error():
    with pytest.raises(at.SchemaError):
        at.plan(_r(category="duplicate"), "someone", "jgravelle")


def test_owner_issues_get_labels_only():
    p = at.plan(_r(draft="an answer"), "JGravelle", "jgravelle")
    assert p["draft"] is None and p["comment"] is None
    assert p["add"] == ["inbound:question", "question"]


def test_unknown_high_adds_needs_human():
    p = at.plan(_r(category="unknown"), "someone", "jgravelle")
    assert p["add"] == ["inbound:unknown", "needs-human"]


@pytest.mark.parametrize(
    "bad",
    [
        {"issue": 1, "category": "bug", "confidence": "high", "evidence": []},
        {"issue": 1, "category": "feature", "confidence": "certain", "evidence": []},
        {
            "issue": 1,
            "category": "feature",
            "confidence": "high",
            "evidence": ["a", "b", "c", "d"],
        },
        {"category": "feature", "confidence": "high", "evidence": []},
    ],
)
def test_schema_violations_are_refused(bad):
    with pytest.raises(at.SchemaError):
        at.plan(bad, "someone", "jgravelle")


def _main(tmp_path, result_path, *extra):
    return at.main(
        [
            str(result_path),
            "--author",
            "x",
            "--owner",
            "o",
            "--repo",
            "o/r",
            "--drafts-dir",
            str(tmp_path / "d"),
            "--run-id",
            "1",
            *extra,
        ]
    )


ESCALATION = ["inbound:unknown", "needs-human"]


@pytest.mark.parametrize(
    "content",
    [
        "{not json",  # JSONDecodeError
        '{"missing": true}',  # what the classify step writes when the model failed
        None,  # no file at all: OSError
    ],
    ids=["unparseable", "classify-failed-placeholder", "absent"],
)
def test_malformed_result_escalates_ON_THE_ISSUE_THE_WORKFLOW_NAMED(
    tmp_path, capsys, monkeypatch, content
):
    # #670: this test used to assert `called == []` under the name
    # "escalates_and_applies_nothing". Escalating IS a write; a function that
    # applies nothing has not escalated, and the issue stayed queued forever.
    # The model's CLASSIFICATION still never reaches gh -- the escalation is
    # our fixed response to its failure and carries nothing it said.
    calls = []
    monkeypatch.setattr(at, "_gh", lambda args, repo: calls.append((args, repo)))
    f = tmp_path / "r.json"
    if content is not None:
        f.write_text(content, encoding="utf-8")
    rc = _main(tmp_path, f, "--issue", "667", "--apply")
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["add"] == ESCALATION and "error" in out
    assert calls == [
        (["issue", "edit", "667", "--add-label", "inbound:unknown", "--add-label", "needs-human"], "o/r"),
        (["issue", "edit", "667", "--remove-label", "inbound:queued"], "o/r"),
    ]


def test_a_result_naming_a_different_issue_escalates_the_named_one(
    tmp_path, capsys, monkeypatch
):
    # The model's `issue` field is never the write target: a well-formed
    # result about #42 while the workflow processed #667 is not a
    # classification of #667, and #42 is never touched.
    calls = []
    monkeypatch.setattr(at, "_gh", lambda args, repo: calls.append((args, repo)))
    f = tmp_path / "r.json"
    f.write_text(json.dumps(_r(issue=42, category="spam")), encoding="utf-8")
    rc = _main(tmp_path, f, "--issue", "667", "--apply")
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["add"] == ESCALATION and "different issue" in out["error"]
    # the model's value is not echoed into a log that is public
    assert "42" not in out["error"]
    assert [c[0][2] for c in calls] == ["667", "667"]
    assert not any("inbound:spam" in c[0] for c in calls)


def test_a_valid_result_applies_its_classification_to_the_named_issue(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(at, "_gh", lambda args, repo: calls.append((args, repo)))
    f = tmp_path / "r.json"
    f.write_text(json.dumps(_r(issue=667, category="feature")), encoding="utf-8")
    assert _main(tmp_path, f, "--issue", "667", "--apply") == 0
    assert calls[0][0] == [
        "issue", "edit", "667", "--add-label", "inbound:feature", "--add-label", "enhancement",
    ]
    assert calls[1][0] == ["issue", "edit", "667", "--remove-label", "inbound:queued"]


def test_without_apply_nothing_reaches_gh_even_on_escalation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(at, "_gh", lambda args, repo: calls.append((args, repo)))
    f = tmp_path / "r.json"
    f.write_text("{not json", encoding="utf-8")
    assert _main(tmp_path, f, "--issue", "667") == 0
    assert calls == []


def test_the_issue_argument_is_required(tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(_r()), encoding="utf-8")
    with pytest.raises(SystemExit):
        _main(tmp_path, f, "--apply")


def test_the_workflow_passes_the_issue_it_is_processing():
    wf = (ROOT / ".github" / "workflows" / "inbound-triage.yml").read_text(encoding="utf-8")
    call = wf[wf.index("python .github/inbound/apply_triage.py") :]
    call = call[: call.index("> \"$RUNNER_TEMP/plan.json\"")]
    assert '--issue "$ISSUE"' in call


def test_draft_file_carries_approval_fields_and_the_original(tmp_path):
    p = at.write_draft(
        {"issue": 5, "category": "feature", "body": "Assessment.\n"}, tmp_path, "99"
    )
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\nissue: 5\n")
    assert "approved: false" in text and "edited: false" in text
    assert text.count("Assessment.") == 2 and "<!-- original -->" in text
    assert p.name == "5-99.md"


APPLIERS = sorted(p.name for p in INBOUND.glob("apply_*.py"))


def test_the_applier_roster_is_read_off_disk():
    # A literal roster leaves the third applier unchecked on arrival.
    assert {"apply_triage.py", "apply_depeval.py"} <= set(APPLIERS)


@pytest.mark.parametrize("script", APPLIERS)
def test_every_inbound_applier_writes_only_to_the_target_the_workflow_named(script):
    # #670's mechanism: an applier that reads its target out of the model's
    # JSON has no target exactly when the model failed. `apply_depeval.py`
    # always took `--pr`; `apply_triage.py` read `result["issue"]`.
    # Two halves, because a declared flag that the write ignores is the
    # defect with a parameter added (Standing lesson 08-19, #508):
    #  1. a REQUIRED `--issue` or `--pr` argument, parsed from the argparse
    #     call so a docstring mentioning the flag cannot pass;
    #  2. every call in `main` to a module function that calls `_gh` passes
    #     `args.issue`/`args.pr` itself, not a value derived from the result.
    import ast

    tree = ast.parse((INBOUND / script).read_text(encoding="utf-8"))
    targets = {}
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) == "add_argument"
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and n.args[0].value in ("--issue", "--pr")
        ):
            kw = {k.arg: k.value for k in n.keywords}
            if isinstance(kw.get("required"), ast.Constant) and kw["required"].value is True:
                targets[n.args[0].value.lstrip("-")] = n
    assert targets, f"{script} takes no required --issue/--pr from the workflow"

    def _is_args(node, attr):
        return (
            isinstance(node, ast.Attribute)
            and node.attr == attr
            and isinstance(node.value, ast.Name)
            and node.value.id == "args"
        )

    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    writers = {
        name
        for name, fn in funcs.items()
        if name != "_gh"
        and any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "_gh"
            for c in ast.walk(fn)
        )
    }
    writes = [
        c
        for c in ast.walk(funcs["main"])
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in writers
    ]
    assert writes, f"{script}: main calls no function that writes through _gh"
    for c in writes:
        assert any(_is_args(a, t) for a in c.args for t in targets), (
            f"{script}:{c.lineno}: a write to args.repo does not pass the workflow's target"
        )


@pytest.mark.parametrize(
    "content",
    [
        '{"issue": 667, "category": ["x"], "confidence": "high", "evidence": []}',  # TypeError
        '{"issue": 667, "category": "question", "confidence": "high", "evidence": [], "draft": 5}',  # AttributeError
        b"\xff\xfe{not utf-8",  # UnicodeDecodeError
        "[" * 100_000 + "]" * 100_000,  # RecursionError
    ],
    ids=["unhashable-category", "non-string-draft", "invalid-utf8", "deeply-nested"],
)
def test_a_result_that_raises_anything_still_escalates(tmp_path, capsys, monkeypatch, content):
    # The first version of the #670 fix caught a LIST of exceptions; each of
    # these escaped it with no gh call and left the item queued.
    calls = []
    monkeypatch.setattr(at, "_gh", lambda args, repo: calls.append((args, repo)))
    f = tmp_path / "r.json"
    if isinstance(content, bytes):
        f.write_bytes(content)
    else:
        f.write_text(content, encoding="utf-8")
    assert _main(tmp_path, f, "--issue", "667", "--apply") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["add"] == ESCALATION and "error" in out
    assert [c[0] for c in calls] == [
        ["issue", "edit", "667", "--add-label", "inbound:unknown", "--add-label", "needs-human"],
        ["issue", "edit", "667", "--remove-label", "inbound:queued"],
    ]


def test_the_duplicate_comment_goes_to_the_named_issue(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(at, "_gh", lambda args, repo: calls.append((args, repo)))
    f = tmp_path / "r.json"
    f.write_text(
        json.dumps(_r(issue=667, category="duplicate", duplicate_of=7, evidence=["a", "b"])),
        encoding="utf-8",
    )
    assert _main(tmp_path, f, "--issue", "667", "--apply") == 0
    comment = [c[0] for c in calls if c[0][:2] == ["issue", "comment"]]
    assert len(comment) == 1 and comment[0][2] == "667"


def test_a_plan_addressing_another_issue_is_retargeted_not_trusted(tmp_path, monkeypatch):
    # The cross-check makes the model's `issue` equal `--issue` today, so the
    # retarget after `plan` is a second guard; this pins it on its own, in
    # case `plan` ever derives a target from anything else in the result.
    calls = []
    monkeypatch.setattr(at, "_gh", lambda args, repo: calls.append((args, repo)))
    real_plan = at.plan

    def plan_elsewhere(result, author, owner):
        p = real_plan(result, author, owner)
        p["comment"] = {"issue_to": 42, "body": "x"}
        p["draft"] = {"issue": 42, "category": "question", "body": "y"}
        return p

    monkeypatch.setattr(at, "plan", plan_elsewhere)
    f = tmp_path / "r.json"
    f.write_text(json.dumps(_r(issue=667)), encoding="utf-8")
    assert _main(tmp_path, f, "--issue", "667", "--apply") == 0
    assert [c[0][2] for c in calls] == ["667", "667", "667"]
    assert (tmp_path / "d" / "667-1.md").exists() and not (tmp_path / "d" / "42-1.md").exists()
