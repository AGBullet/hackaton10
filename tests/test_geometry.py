import pytest
from inspector.geometry import measure_segment

def test_measurement_uses_page_aspect_and_calibration():
    r=measure_segment(1000,500,[0,0,.1,0],1000,[0,0,0,.2]);assert r['measured_mm']==1000
def test_measurement_rejects_missing_or_degenerate_scale():
    with pytest.raises(ValueError):measure_segment(1000,500,[0,0,0,0],1000,[0,0,.2,.2])
    with pytest.raises(ValueError):measure_segment(1000,500,[0,0,.1,0],0,[0,0,.2,.2])
