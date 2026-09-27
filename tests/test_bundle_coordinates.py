from inspector.bundles import _normalize_bbox


def test_organizer_bbox_y_is_converted_to_visible_page_origin():
    bbox, precision = _normalize_bbox(
        [0.35785, 0.804926, 0.688776, 0.81516], 'TEXT_EXACT'
    )

    assert precision == 'TEXT_EXACT'
    assert bbox == [0.35785, 0.164957, 0.688776, 0.214957]


def test_page_level_evidence_does_not_become_a_fragment():
    bbox, precision = _normalize_bbox(
        [0.02, 0.92, 0.98, 0.98], 'PAGE_LEVEL_ONLY'
    )

    assert bbox is None
    assert precision == 'PAGE_LEVEL_ONLY'
