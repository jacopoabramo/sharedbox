from sharedbox.benchmarks import contention


def test_contention_reports_writes_and_reads_of_every_contender() -> None:
    """Report throughput and percentiles for each contender with writers and readers."""
    results = contention.run(contention.Options(writers=(2,), readers=(1,), ops=200))

    assert [r["contender"] for r in results] == [
        c.label for c in contention.CONTENDERS.values()
    ]
    for r in results:
        assert r["write_mops"] > 0
        assert r["read_mops"] is not None
        assert r["read_mops"] > 0
        assert r["read_p99_ns"] is not None
