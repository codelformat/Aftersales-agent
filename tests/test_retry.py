import pytest

from app.retry import backoff_delay, retry_async

pytestmark = pytest.mark.anyio


def test_backoff_doubles_and_caps():
    assert [backoff_delay(n, 0.2, 2.0, 1.0) for n in (1, 2, 3, 4, 5)] == [0.2, 0.4, 0.8, 1.6, 2.0]


def test_backoff_jitter_range():
    assert backoff_delay(1, 1.0, 10.0, 0.0) == 0.5
    assert backoff_delay(1, 1.0, 10.0, 1.0) == 1.0


class Flaky:
    def __init__(self, failures, exc=TimeoutError):
        self.failures, self.exc, self.calls = failures, exc, 0

    async def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc("boom")
        return "ok"


async def test_retries_then_succeeds_with_backoff():
    delays = []

    async def fake_sleep(d):
        delays.append(d)

    fn = Flaky(2)
    result = await retry_async(
        fn, attempts=3, base_delay=0.2, max_delay=2.0, retry_on=(TimeoutError,),
        sleep=fake_sleep, rand=lambda: 1.0,
    )
    assert result == "ok"
    assert fn.calls == 3
    assert delays == [0.2, 0.4]


async def test_raises_last_error_when_exhausted():
    async def fake_sleep(d):
        pass

    fn = Flaky(5)
    with pytest.raises(TimeoutError):
        await retry_async(fn, attempts=3, base_delay=0.1, max_delay=1.0,
                          retry_on=(TimeoutError,), sleep=fake_sleep)
    assert fn.calls == 3


async def test_does_not_retry_other_errors():
    async def fake_sleep(d):
        raise AssertionError("must not sleep")

    fn = Flaky(1, exc=ValueError)
    with pytest.raises(ValueError):
        await retry_async(fn, attempts=3, base_delay=0.1, max_delay=1.0,
                          retry_on=(TimeoutError,), sleep=fake_sleep)
    assert fn.calls == 1


async def test_single_attempt_never_sleeps():
    async def fake_sleep(d):
        raise AssertionError("must not sleep")

    fn = Flaky(1)
    with pytest.raises(TimeoutError):
        await retry_async(fn, attempts=1, base_delay=0.1, max_delay=1.0,
                          retry_on=(TimeoutError,), sleep=fake_sleep)


@pytest.mark.parametrize("failures, expected", [(0, []), (2, [1, 2]), (5, [1, 2])])
async def test_on_retry_counts_actual_retries_before_sleep(failures, expected):
    events = []

    def on_retry(number, exc):
        assert isinstance(exc, TimeoutError)
        events.append(("retry", number))

    async def fake_sleep(d):
        events.append(("sleep", d))

    fn = Flaky(failures)
    kwargs = dict(attempts=3, base_delay=0.2, max_delay=2.0, retry_on=(TimeoutError,),
                  on_retry=on_retry, sleep=fake_sleep, rand=lambda: 1.0)
    if failures >= 3:
        with pytest.raises(TimeoutError):
            await retry_async(fn, **kwargs)
    else:
        assert await retry_async(fn, **kwargs) == "ok"
    assert [value for event, value in events if event == "retry"] == expected
    assert events == [(event, value) for n in expected
                      for event, value in [("retry", n), ("sleep", 0.2 * 2 ** (n - 1))]]
    assert fn.calls == len(expected) + 1


async def test_should_retry_false_raises_immediately():
    fn = Flaky(3)

    async def fake_sleep(d):
        raise AssertionError("must not sleep")

    def on_retry(number, exc):
        raise AssertionError("must not retry")

    with pytest.raises(TimeoutError):
        await retry_async(fn, attempts=3, base_delay=0.1, max_delay=1,
                          retry_on=(TimeoutError,), should_retry=lambda exc: False,
                          on_retry=on_retry, sleep=fake_sleep)
    assert fn.calls == 1


async def test_should_retry_can_retry_outside_retry_on():
    fn = Flaky(1, exc=ConnectionError)

    async def fake_sleep(d):
        pass

    result = await retry_async(fn, attempts=3, base_delay=0.1, max_delay=1,
                               should_retry=lambda exc: isinstance(exc, ConnectionError), sleep=fake_sleep)
    assert result == "ok" and fn.calls == 2
