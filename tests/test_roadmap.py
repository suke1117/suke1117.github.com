"""The roadmap is data the dashboard renders, so it is validated like data."""
from pathlib import Path

import pytest
import yaml

from src.web.export_dashboard import PRIORITY_ORDER, REQUIRED_TASK_FIELDS, load_roadmap, normalize_text

ROADMAP = Path(__file__).resolve().parents[1] / "docs" / "roadmap.yml"


@pytest.fixture(scope="module")
def roadmap():
    return load_roadmap(ROADMAP)


def test_repo_roadmap_loads_and_is_ordered_by_priority(roadmap):
    tasks = roadmap["tasks"]
    assert len(tasks) >= 10
    ranks = [PRIORITY_ORDER.index(t["priority"]) for t in tasks]
    assert ranks == sorted(ranks)
    assert sum(roadmap["counts"].values()) == len(tasks)
    assert roadmap["counts"].get("P0"), "a roadmap with nothing urgent is a roadmap nobody reads"


def test_every_task_carries_what_the_card_renders(roadmap):
    for t in roadmap["tasks"]:
        for f in REQUIRED_TASK_FIELDS:
            assert t.get(f), (t["id"], f)
        assert t["effort"] in ("S", "M", "L")
        assert t["status"] in ("todo", "doing", "done", "blocked")
        assert t["category"] in roadmap["categories"]
        if t["status"] == "blocked":
            assert t.get("blocked_by"), f"{t['id']} is blocked but does not say by what"


def test_dependencies_point_backwards_in_priority(roadmap):
    rank = {t["id"]: PRIORITY_ORDER.index(t["priority"]) for t in roadmap["tasks"]}
    for t in roadmap["tasks"]:
        for d in t.get("depends") or []:
            assert rank[d] <= rank[t["id"]], f"{t['id']} depends on lower-priority {d}"


def test_no_dependency_cycles(roadmap):
    deps = {t["id"]: list(t.get("depends") or []) for t in roadmap["tasks"]}
    seen, done = set(), set()

    def visit(node):
        assert node not in seen, f"dependency cycle through {node}"
        if node in done:
            return
        seen.add(node)
        for d in deps[node]:
            visit(d)
        seen.discard(node)
        done.add(node)

    for n in deps:
        visit(n)


def test_japanese_text_has_no_wrapped_line_gaps(roadmap):
    """YAML folded scalars join lines with a space; Japanese must not keep it."""
    assert normalize_text("測定する。　現在") is not None
    assert normalize_text("確保する。 現在の") == "確保する。現在の"
    assert normalize_text("幅が 20 ポイント") == "幅が 20 ポイント"   # ASCII neighbours keep their spaces
    for t in roadmap["tasks"]:
        for f in ("title", "why", "done"):
            assert "  " not in t[f], (t["id"], f)


def test_missing_file_is_not_fatal(tmp_path):
    assert load_roadmap(tmp_path / "nope.yml") == {}


def _write(tmp_path, tasks):
    p = tmp_path / "r.yml"
    p.write_text(yaml.safe_dump({"tasks": tasks}, allow_unicode=True), encoding="utf-8")
    return p


def _task(**kw):
    base = {"id": "A1", "title": "t", "category": "model", "priority": "P1", "effort": "S",
            "status": "todo", "impact": "i", "why": "w", "done": "d"}
    base.update(kw)
    return base


def test_malformed_roadmap_fails_loudly(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        load_roadmap(_write(tmp_path, [_task(why=None)]))
    with pytest.raises(ValueError, match="unknown priority"):
        load_roadmap(_write(tmp_path, [_task(priority="P9")]))
    with pytest.raises(ValueError, match="unknown ids"):
        load_roadmap(_write(tmp_path, [_task(depends=["ZZ"])]))
    with pytest.raises(ValueError, match="duplicate"):
        load_roadmap(_write(tmp_path, [_task(), _task()]))
