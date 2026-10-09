from sharedbox.benchmarks import roundtrip


def test_roundtrip_includes_the_stream() -> None:
    """Report round-trip percentiles for SharedStream next to the other contenders."""
    results = roundtrip.run(roundtrip.Options(samples=50, warmup=10, timeout=10.0))
    stream_row = next(r for r in results if r["contender"] == "SharedStream")
    assert 0 < stream_row["p50_us"] <= stream_row["p99_us"]
