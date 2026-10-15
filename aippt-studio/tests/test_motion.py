from __future__ import annotations

from pathlib import Path

from aippt_studio import motion
from aippt_studio.models import SlideSpec


def test_motion_plan_maps_titles_and_points_to_stable_shape_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"pptx")
    nodes = {
        "/slide[1]": {
            "path": "/slide[1]",
            "children": [
                {"path": "/slide[1]/shape[@id=3]", "type": "shape", "text": "Cover title"},
            ],
        },
        "/slide[2]": {
            "path": "/slide[2]",
            "children": [
                {"path": "/slide[2]/shape[@id=4]", "type": "shape", "text": "Decision"},
                {"path": "/slide[2]/shape[@id=5]", "type": "shape", "text": "First point"},
                {"path": "/slide[2]/shape[@id=6]", "type": "shape", "text": "Second point"},
            ],
        },
    }

    def fake_json(_executable: Path, arguments: list[str]):
        return {"matches": 1, "results": [nodes[arguments[2]]]}

    monkeypatch.setattr(motion, "_officecli", lambda: tmp_path / "officecli.exe")
    monkeypatch.setattr(motion, "_json_command", fake_json)
    closed: list[Path] = []
    monkeypatch.setattr(
        motion,
        "_run_officecli",
        lambda _executable, arguments, *, timeout=30: closed.append(Path(arguments[1])) or "closed",
    )
    operations, warnings = motion.build_motion_operations(
        deck,
        [
            SlideSpec(id="slide-01", title="Cover title", purpose="cover", layout="cover", motion="fade"),
            SlideSpec(
                id="slide-02",
                title="Decision",
                purpose="body",
                layout="content",
                points=["First point", "Second point"],
                motion="reveal",
            ),
        ],
    )

    assert warnings == []
    assert len(operations) == 6
    assert operations[1].target == "/slide[1]/shape[@id=3]"
    assert operations[3].target == "/slide[2]/shape[@id=4]"
    assert operations[4].properties["trigger"] == "afterPrevious"
    assert operations[5].target == "/slide[2]/shape[@id=6]"
    assert closed == [deck]
