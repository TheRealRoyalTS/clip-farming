from clipfarm.config import DetectorConfig
from clipfarm.detector import SpikeDetector

CFG = DetectorConfig(min_signals=1)  # these tests cover the chat-rate gate alone


def quiet_chat(det: SpikeDetector, start: float, seconds: int, per_second: float = 0.5):
    """Feed steady background chat; return any triggers."""
    out, t, step = [], start, 1 / per_second
    while t < start + seconds:
        if trig := det.feed(t, "hello there"):
            out.append(trig)
        t += step
    return out


def burst(det: SpikeDetector, start: float, n: int, text: str = "wow"):
    return [r for i in range(n) if (r := det.feed(start + i * 0.1, text))]


def test_steady_chat_never_triggers():
    det = SpikeDetector("c", CFG)
    assert quiet_chat(det, 0, 300) == []


def test_burst_after_quiet_triggers_once():
    det = SpikeDetector("c", CFG)
    quiet_chat(det, 0, 200)
    trigs = burst(det, 200, 40)
    assert len(trigs) == 1
    assert trigs[0].channel == "c"
    assert trigs[0].rate >= CFG.min_messages


def test_cooldown_blocks_second_trigger_then_allows_after_expiry():
    det = SpikeDetector("c", CFG)
    quiet_chat(det, 0, 200)
    assert len(burst(det, 200, 40)) == 1
    assert burst(det, 210, 40) == []  # inside cooldown
    quiet_chat(det, 215, 200)  # baseline recovers well past cooldown
    assert len(burst(det, 420, 40)) == 1


def test_small_burst_below_min_messages_is_ignored():
    det = SpikeDetector("c", CFG)
    quiet_chat(det, 0, 200)
    assert burst(det, 200, 4) == []


def test_constantly_busy_chat_is_not_a_spike():
    # 8 msg/s all the time: high absolute rate, but it is the baseline.
    det = SpikeDetector("c", CFG)
    assert quiet_chat(det, 0, 300, per_second=8) == []


def test_hype_terms_count_more_than_plain_messages():
    det = SpikeDetector("c", CFG)
    assert det.weight("KEKW") == CFG.hype_weight
    assert det.weight("that was Pog!") == CFG.hype_weight  # punctuation stripped, case-insensitive
    assert det.weight("nice play") == 1.0


def test_hype_burst_triggers_where_plain_burst_of_same_size_does_not():
    plain, hype = SpikeDetector("c", CFG), SpikeDetector("c", CFG)
    for d in (plain, hype):
        quiet_chat(d, 0, 200)
    # background chat already puts ~2.5 weighted messages in the window
    assert burst(plain, 200, 4, "ok") == []  # 2.5 + 4 < min_messages=8
    assert len(burst(hype, 200, 4, "KEKW")) == 1  # 2.5 + 4 * 2.0 >= 8


def test_old_events_are_discarded():
    det = SpikeDetector("c", CFG)
    quiet_chat(det, 0, 1000)
    assert len(det._events) < 400  # bounded by the baseline horizon, not total history


def test_no_trigger_during_warmup_even_for_a_huge_burst():
    det = SpikeDetector("c", CFG)
    assert burst(det, 0, 100) == []  # stream just started: no baseline to compare with
    assert burst(det, 60, 100) == []  # still inside baseline_seconds
