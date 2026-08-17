from app.services.generation import compute_align_ratio


def test_align_ratio_identity():
    assert compute_align_ratio(5400.0, 5400.0) == 1.0


def test_align_ratio_frame_rate_drift():
    ratio = compute_align_ratio(5529.0, 5760.0)
    assert ratio is not None
    assert abs(ratio - 0.96) < 0.01


def test_align_ratio_rejects_different_cut():
    assert compute_align_ratio(5400.0, 2000.0) is None
    assert compute_align_ratio(2000.0, 5400.0) is None


def test_align_ratio_handles_missing_inputs():
    assert compute_align_ratio(0.0, 100.0) is None
    assert compute_align_ratio(100.0, 0.0) is None
    assert compute_align_ratio(None, 100.0) is None