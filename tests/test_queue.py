import pytest

from clipfarm.queue import Queue


def add(q, tmp_path, cid="c-1"):
    f = tmp_path / f"{cid}.mp4"
    f.write_bytes(b"video")
    return q.add(cid, f, {"channel": "c", "at": 1.0, "rate": 12.0})


def test_add_lands_in_pending_with_meta_and_moves_file(tmp_path):
    q = Queue(tmp_path / "q")
    d = add(q, tmp_path)
    assert q.list("pending") == ["c-1"]
    assert (d / "clip.mp4").read_bytes() == b"video"
    assert not (tmp_path / "c-1.mp4").exists()
    assert q.meta("c-1", "pending")["rate"] == 12.0


def test_approve_and_reject_move_between_states(tmp_path):
    q = Queue(tmp_path / "q")
    add(q, tmp_path, "a")
    add(q, tmp_path, "b")
    q.approve("a")
    q.reject("b")
    assert (q.list("pending"), q.list("approved"), q.list("rejected")) == ([], ["a"], ["b"])


def test_cannot_approve_clip_not_pending(tmp_path):
    q = Queue(tmp_path / "q")
    add(q, tmp_path)
    q.approve("c-1")
    with pytest.raises(KeyError):
        q.approve("c-1")
    with pytest.raises(KeyError):
        q.reject("missing")


def test_duplicate_id_is_refused_not_overwritten(tmp_path):
    q = Queue(tmp_path / "q")
    add(q, tmp_path)
    with pytest.raises(FileExistsError):
        add(q, tmp_path)


def test_queue_survives_restart(tmp_path):
    add(Queue(tmp_path / "q"), tmp_path)
    assert Queue(tmp_path / "q").list("pending") == ["c-1"]
