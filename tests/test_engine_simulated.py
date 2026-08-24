"""End-to-end runs through the real CALL-E SDK against a local transport.

These tests exist to prove two things a mock `CallPort` could not: that the
installed `calle` SDK really builds and sends the request, and that the
idempotency key protects a customer from a second call when a run is replayed.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import httpx
import pytest
from tests.conftest import at

from kept.calls.calle_port import CalleCallPort
from kept.calls.simulation import SIMULATED_BASE_URL, CalleSimulator, Scenario
from kept.clock import FrozenClock
from kept.config import Organisation, Policy
from kept.engine import CollectionRun, Runtime, keys_consumed, orphaned_calls
from kept.ledger import Ledger
from kept.store import EVENT_CALL_DISPATCHED, EVENT_CALL_PLACED, load_book

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
RUN_MOMENT = at("2026-08-24")


class RecordingSimulator(CalleSimulator):
    """A simulator that also keeps the raw requests the SDK produced."""

    def __init__(self, *, scenario: Scenario, today: date) -> None:
        super().__init__(scenario=scenario, today=today)
        self.requests: list[httpx.Request] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return super()._handle(request)

    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]

    def gets(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "GET"]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    destination = tmp_path / "data"
    shutil.copytree(EXAMPLES, destination)
    return destination


def _run(data_dir: Path, simulator: CalleSimulator, *, budget: int = 3, ledger: Ledger | None = None):
    ledger = ledger or Ledger(data_dir / "ledger.jsonl")
    port = CalleCallPort.with_transport(transport=simulator.transport, base_url=SIMULATED_BASE_URL)
    runtime = Runtime(
        port=port,
        ledger=ledger,
        clock=FrozenClock(RUN_MOMENT),
        policy=Policy(),
        organisation=Organisation(name="Northwind Supply Co", callback_number="+15550199000"),
    )
    summary = CollectionRun(runtime).execute(load_book(data_dir, ledger), budget)
    port.close()
    return summary


def _simulator(data_dir: Path, name: str = "week1.json") -> RecordingSimulator:
    scenario = Scenario.from_file(data_dir / "scenarios" / name)
    return RecordingSimulator(scenario=scenario, today=RUN_MOMENT.date())


def test_the_installed_calle_sdk_sends_the_request(data_dir: Path) -> None:
    simulator = _simulator(data_dir)

    _run(data_dir, simulator)

    post = simulator.posts()[0]
    body = json.loads(post.content)
    assert post.url.path == "/v1/calls"
    assert post.headers["Authorization"].startswith("Bearer ")
    assert post.headers["Idempotency-Key"].startswith("kept:INV-")
    assert body["recipients"][0]["phones"][0].startswith("+1555")
    assert body["result_schema"]["additionalProperties"] is False
    assert all(
        isinstance(field["type"], str)
        for field in body["result_schema"]["properties"].values()
    )
    assert body["metadata"]["invoice_id"].startswith("INV-")


def test_the_sdk_polls_until_the_call_is_terminal(data_dir: Path) -> None:
    simulator = _simulator(data_dir)

    _run(data_dir, simulator, budget=1)

    assert len(simulator.gets()) >= 2


def test_the_task_text_carries_the_boundaries_the_agent_must_respect(data_dir: Path) -> None:
    simulator = _simulator(data_dir)

    _run(data_dir, simulator, budget=1)

    task = json.loads(simulator.posts()[0].content)["task"]
    assert "Never accept or write down card numbers" in task
    assert "Never threaten legal action" in task
    assert "Northwind Supply Co" in task


def test_a_run_records_promises_and_disputes_separately(data_dir: Path) -> None:
    summary = _run(data_dir, _simulator(data_dir))

    assert summary.calls_placed == 3
    assert {p.invoice_id for p in summary.promises} == {"INV-1002", "INV-1007"}
    assert [d.invoice_id for d in summary.disputes] == ["INV-1001"]
    assert summary.rejections == []


def test_vague_answers_produce_no_financial_records(data_dir: Path) -> None:
    summary = _run(data_dir, _simulator(data_dir, "vague-answers.json"))

    assert summary.calls_placed == 3
    assert summary.promises == []
    assert dict(summary.rejections) == {
        "INV-1002": "no_commitment",
        "INV-1007": "wrong_party",
        "INV-1001": "unreadable_amount",
    }


def test_replaying_a_run_after_a_lost_ledger_write_does_not_dial_again(data_dir: Path) -> None:
    """The key is derived from business identity, so the retry is the same call."""
    simulator = _simulator(data_dir)
    _run(data_dir, simulator, budget=1)
    calls_after_first_run = simulator.placed_call_count

    (data_dir / "ledger.jsonl").unlink()
    _run(data_dir, simulator, budget=1)

    assert simulator.placed_call_count == calls_after_first_run == 1


def test_promises_survive_a_restart_by_replaying_the_ledger(data_dir: Path) -> None:
    _run(data_dir, _simulator(data_dir))

    reloaded = load_book(data_dir, Ledger(data_dir / "ledger.jsonl"))

    assert {p.invoice_id for p in reloaded.promises} == {"INV-1002", "INV-1007"}
    assert [d.invoice_id for d in reloaded.disputes] == ["INV-1001"]
    assert len(reloaded.contacts) == 3


def test_a_provider_failure_is_recorded_and_the_run_continues(data_dir: Path, tmp_path: Path) -> None:
    scenario_path = data_dir / "scenarios" / "one-failure.json"
    scenario_path.write_text(
        json.dumps({"INV-1002": {"status": "failed", "outcome": "no_answer"}}), encoding="utf-8"
    )
    simulator = _simulator(data_dir, "one-failure.json")

    summary = _run(data_dir, simulator, budget=2)

    assert summary.rejections[0] == ("INV-1002", "call_not_completed")
    assert summary.calls_placed == 2


def test_every_placed_call_is_written_to_the_ledger_with_a_masked_number(data_dir: Path) -> None:
    _run(data_dir, _simulator(data_dir))
    ledger = Ledger(data_dir / "ledger.jsonl")

    numbers = [payload["phone"] for payload in ledger.of_type(EVENT_CALL_PLACED)]

    assert len(numbers) == 3
    assert all(number.startswith("***") and len(number) == 5 for number in numbers)
    ledger.verify()


class KilledMidPollPort:
    """Dispatches the call, then dies exactly like an interrupted process."""

    def __init__(self, inner: CalleCallPort) -> None:
        self._inner = inner
        self.dispatched: list[str] = []

    def dispatch(self, request):
        call_id = self._inner.dispatch(request)
        self.dispatched.append(call_id)
        return call_id

    def await_result(self, call_id: str):
        raise KeyboardInterrupt("process killed while polling")


def _runtime(data_dir: Path, port, ledger: Ledger) -> Runtime:
    return Runtime(
        port=port,
        ledger=ledger,
        clock=FrozenClock(RUN_MOMENT),
        policy=Policy(),
        organisation=Organisation(name="Northwind Supply Co", callback_number="+15550199000"),
    )


def test_a_call_is_written_to_the_ledger_before_its_outcome_is_known(data_dir: Path) -> None:
    """The customer's phone has already rung; the id must survive a crash."""
    simulator = _simulator(data_dir)
    ledger = Ledger(data_dir / "ledger.jsonl")
    inner = CalleCallPort.with_transport(transport=simulator.transport, base_url=SIMULATED_BASE_URL)
    killed = KilledMidPollPort(inner)

    with pytest.raises(KeyboardInterrupt):
        CollectionRun(_runtime(data_dir, killed, ledger)).execute(load_book(data_dir, ledger), 1)

    dispatched = list(ledger.of_type(EVENT_CALL_DISPATCHED))
    assert [d["call_id"] for d in dispatched] == killed.dispatched
    assert list(ledger.of_type(EVENT_CALL_PLACED)) == []
    assert len(orphaned_calls(ledger)) == 1


def test_recover_finishes_an_orphaned_call_without_dialling_again(data_dir: Path) -> None:
    simulator = _simulator(data_dir)
    ledger = Ledger(data_dir / "ledger.jsonl")
    inner = CalleCallPort.with_transport(transport=simulator.transport, base_url=SIMULATED_BASE_URL)
    with pytest.raises(KeyboardInterrupt):
        CollectionRun(_runtime(data_dir, KilledMidPollPort(inner), ledger)).execute(
            load_book(data_dir, ledger), 1
        )
    calls_after_crash = simulator.placed_call_count

    summary = CollectionRun(_runtime(data_dir, inner, ledger)).recover(load_book(data_dir, ledger))

    assert simulator.placed_call_count == calls_after_crash == 1
    assert [p.invoice_id for p in summary.promises] == ["INV-1002"]
    assert orphaned_calls(ledger) == []
    ledger.verify()


def test_recover_is_a_no_op_when_nothing_was_left_hanging(data_dir: Path) -> None:
    ledger = Ledger(data_dir / "ledger.jsonl")
    _run(data_dir, _simulator(data_dir), budget=1, ledger=ledger)
    port = CalleCallPort.with_transport(
        transport=_simulator(data_dir).transport, base_url=SIMULATED_BASE_URL
    )

    summary = CollectionRun(_runtime(data_dir, port, ledger)).recover(load_book(data_dir, ledger))

    assert summary.promises == [] and summary.rejections == []


def test_a_dialled_call_burns_its_key_even_if_the_outcome_is_lost(data_dir: Path) -> None:
    """Counting only completed calls regenerated a spent key and earned a 409."""
    from kept.models import CallCycle

    simulator = _simulator(data_dir)
    ledger = Ledger(data_dir / "ledger.jsonl")
    inner = CalleCallPort.with_transport(transport=simulator.transport, base_url=SIMULATED_BASE_URL)
    with pytest.raises(KeyboardInterrupt):
        CollectionRun(_runtime(data_dir, KilledMidPollPort(inner), ledger)).execute(
            load_book(data_dir, ledger), 1
        )

    assert keys_consumed(ledger, "INV-1002", CallCycle.FIRST_CONTACT) == 1


def test_a_refused_duplicate_also_burns_the_key(data_dir: Path) -> None:
    from kept.models import CallCycle

    ledger = Ledger(data_dir / "ledger.jsonl")
    ledger.append(
        event_type="call_failed",
        payload={"invoice_id": "INV-1002", "cycle": "first_contact", "code": "idempotency_conflict"},
        at=RUN_MOMENT,
    )

    assert keys_consumed(ledger, "INV-1002", CallCycle.FIRST_CONTACT) == 1


def test_a_key_burned_before_the_cycle_was_recorded_still_counts(data_dir: Path) -> None:
    from kept.models import CallCycle

    ledger = Ledger(data_dir / "ledger.jsonl")
    ledger.append(
        event_type="call_failed",
        payload={"invoice_id": "INV-1002", "code": "idempotency_conflict"},
        at=RUN_MOMENT,
    )

    assert keys_consumed(ledger, "INV-1002", CallCycle.FIRST_CONTACT) == 1
