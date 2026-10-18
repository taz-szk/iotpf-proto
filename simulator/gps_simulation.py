import random


def random_offset(base_lat: float, base_lon: float, max_offset_deg: float = 0.01) -> tuple[float, float]:
    return (
        base_lat + random.uniform(-max_offset_deg, max_offset_deg),
        base_lon + random.uniform(-max_offset_deg, max_offset_deg),
    )


def make_gps_payload_fn(start_lat: float, start_lon: float, step_deg: float = 0.0005):
    state = {"lat": start_lat, "lon": start_lon}

    def fn():
        state["lat"] += random.uniform(-step_deg, step_deg)
        state["lon"] += random.uniform(-step_deg, step_deg)
        return {"latitude": round(state["lat"], 6), "longitude": round(state["lon"], 6)}

    return fn
