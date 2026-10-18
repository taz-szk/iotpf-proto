from gps_simulation import random_offset, make_gps_payload_fn

def test_random_offset_stays_within_max_offset():
    lat, lon = random_offset(35.0, 139.0, max_offset_deg=0.01)
    assert abs(lat - 35.0) <= 0.01
    assert abs(lon - 139.0) <= 0.01

def test_make_gps_payload_fn_returns_latitude_and_longitude():
    fn = make_gps_payload_fn(35.0, 139.0, step_deg=0.0005)
    payload = fn()
    assert set(payload.keys()) == {"latitude", "longitude"}

def test_make_gps_payload_fn_moves_gradually_not_randomly_each_call():
    fn = make_gps_payload_fn(35.0, 139.0, step_deg=0.0005)
    first = fn()
    second = fn()
    # 前回位置からstep_degを超えて飛ばないこと(ランダムウォークであり、毎回ゼロから引き直すのではない)
    assert abs(second["latitude"] - first["latitude"]) <= 0.0005
    assert abs(second["longitude"] - first["longitude"]) <= 0.0005
