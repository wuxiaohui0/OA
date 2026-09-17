from datetime import datetime

import pytest

from oa.agents import deterministic_extract
from oa.domain import BusinessError, assert_transition, working_hours

NOW = datetime.fromisoformat("2026-09-15T08:00:00+08:00")


@pytest.mark.parametrize(
    "period,start,end,hours",
    [
        ("", "01:00", "10:00", 7.5),
        ("全天", "01:00", "10:00", 7.5),
        ("上午", "01:00", "04:00", 3),
        ("下午", "05:30", "10:00", 4.5),
        ("9点到18点", "01:00", "10:00", 7.5),
        ("下午2点到5点", "06:00", "09:00", 3),
    ],
)
def test_whole_day_and_explicit_periods(period, start, end, hours):
    result = deterministic_extract(f"请假是9月17号{period}，头难受", NOW)
    assert result["missingFields"] == []
    assert result["leaveType"] == "sick"
    assert result["reason"] == "头难受"
    assert result["startAt"] == f"2026-09-17T{start}:00.000Z"
    assert result["endAt"] == f"2026-09-17T{end}:00.000Z"
    assert working_hours(result["startAt"], result["endAt"]) == hours


@pytest.mark.parametrize(
    "period", ["半天", "9点开始", "时间待定", "时间我稍后告诉你", "25点到26点", "9:75到18:00", "18点到9点"]
)
def test_ambiguous_time(period):
    result = deterministic_extract(f"请假9月17号{period}，原因是去医院", NOW)
    assert result["missingFields"] == ["开始和结束时间"]
    assert result["startAt"] is None


@pytest.mark.parametrize("dates", ["9月17号到9月18号", "9月17号到18号", "9月17号和9月21号"])
def test_date_ranges_need_clarification(dates):
    result = deterministic_extract(f"请假{dates}，头难受", NOW)
    assert result["missingFields"] == ["请假日期"]
    assert result["startAt"] is None


def test_invalid_dates_and_followup_corrections():
    assert deterministic_extract("2027年2月30号，时间待定，头难受", NOW)["missingFields"] == [
        "请假日期",
        "开始和结束时间",
    ]
    result = deterministic_extract(
        "请2027年9月17号上午的假，原因是头难受需要去医院\n日期改成9月20号\n开始时间是9点到18点", NOW
    )
    assert result["startAt"] == "2027-09-20T01:00:00.000Z"
    assert result["endAt"] == "2027-09-20T10:00:00.000Z"
    assert result["reason"] == "头难受需要去医院"
    result = deterministic_extract("请9月17号的病假，原因是去医院\n改成事假，头疼", NOW)
    assert (result["leaveType"], result["reason"]) == ("personal", "头疼")
    assert (
        deterministic_extract("请1月1号的假，头疼", datetime.fromisoformat("2026-12-31T16:30:00Z"))["startAt"]
        == "2027-01-01T01:00:00.000Z"
    )


@pytest.mark.parametrize(
    "start,end,hours",
    [
        ("2026-09-18T09:00:00+08:00", "2026-09-21T18:00:00+08:00", 15),
        ("2026-09-19T09:00:00+08:00", "2026-09-20T18:00:00+08:00", 0),
        ("2026-09-17T12:00:00+08:00", "2026-09-17T13:30:00+08:00", 0),
        ("2026-09-17T01:00:00Z", "2026-09-17T10:00:00Z", 7.5),
    ],
)
def test_working_hours(start, end, hours):
    assert working_hours(start, end) == hours


def test_state_machine_blocks_skipping_review():
    with pytest.raises(BusinessError):
        assert_transition("draft", "approved")
    assert_transition("draft", "submitted")
