from scripts.safety_scan import main


def test_safety_scan_passes():
    assert main([]) == 0
