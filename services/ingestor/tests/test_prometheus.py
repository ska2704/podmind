from app.prometheus import parse_response


def _vector_payload(samples):
    """Build a /api/v1/query response with the given (metric_dict, ts, val) samples."""
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": metric, "value": [ts, str(val)]}
                for metric, ts, val in samples
            ],
        },
    }


def test_parse_basic_sample():
    payload = _vector_payload(
        [
            (
                {
                    "__name__": "container_memory_working_set_bytes",
                    "pod": "gateway-7",
                    "namespace": "sh-core",
                    "container": "gateway",
                    "image": "nginx:alpine",
                },
                1714996800.0,
                123456.0,
            )
        ]
    )
    rows = list(parse_response("container_memory_working_set_bytes", payload))
    assert len(rows) == 1
    r = rows[0]
    assert r.name == "container_memory_working_set_bytes"
    assert r.value == 123456.0
    assert r.pod == "gateway-7"
    assert r.namespace == "sh-core"
    assert r.container == "gateway"
    assert r.labels == {"image": "nginx:alpine"}


def test_rate_query_falls_back_to_query_string():
    """rate(...) responses don't carry __name__, so the query string is used."""
    payload = _vector_payload(
        [
            (
                {"pod": "p", "namespace": "n"},
                1714996800.0,
                0.42,
            )
        ]
    )
    rows = list(parse_response("rate(container_cpu_usage_seconds_total[30s])", payload))
    assert rows[0].name == "rate(container_cpu_usage_seconds_total[30s])"


def test_skip_nonsuccess():
    rows = list(parse_response("x", {"status": "error", "errorType": "bad_data"}))
    assert rows == []


def test_skip_nan_inf():
    payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"pod": "p"}, "value": [1714996800.0, "NaN"]},
                {"metric": {"pod": "q"}, "value": [1714996800.0, "+Inf"]},
                {"metric": {"pod": "r"}, "value": [1714996800.0, "1.5"]},
            ],
        },
    }
    rows = list(parse_response("x", payload))
    assert [r.pod for r in rows] == ["r"]


def test_empty_result():
    payload = {"status": "success", "data": {"resultType": "vector", "result": []}}
    assert list(parse_response("x", payload)) == []
