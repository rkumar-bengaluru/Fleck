"""Meeting reminders: a Record notification 5 minutes before events that look like calls."""
from datetime import datetime, timezone

from fleck_helper.notify import Scheduler

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)


class FakeNotifier:
    def __init__(self):
        self.posts, self.cleared = [], 0

    def clear(self):
        self.cleared += 1
        self.posts = []

    def post(self, ident, title, body, at, event_id):
        self.posts.append((ident, title, body, at, event_id))


def event(i, start, link=None, attendees=(), cancelled=False):
    return {"id": i, "title": f"Event {i}", "start_at": start, "meeting_link": link, "attendees": list(attendees),
            "cancelled": cancelled}


def test_notification_scheduled_at_t_minus_5():
    n = FakeNotifier()
    Scheduler(n, clock=lambda: NOW).schedule([
        event(1, "2026-10-31T11:00:00+00:00", link="https://meet.google.com/abc-defg-hij"),
        event(2, "2026-10-31T12:00:00+00:00"),  # no link, no attendees: not a call
        event(3, "2026-10-31T08:00:00+00:00", link="https://zoom.us/j/1"),  # already started
        event(4, "2026-10-31T13:00:00+00:00", link="https://zoom.us/j/2", cancelled=True),
    ])
    assert n.posts == [("fleck-event-1", "Event 1", "Starts in 5 min.", datetime(2026, 10, 31, 10, 55, tzinfo=timezone.utc), 1)]


def test_attendees_alone_count_and_rescheduling_replaces():
    n = FakeNotifier()
    s = Scheduler(n, clock=lambda: NOW)
    s.schedule([event(5, "2026-10-31T09:30:00+05:30", attendees=[{"email": "dana@harbourline.test"}])])
    assert n.posts == []  # 04:00 UTC: in the past
    s.schedule([event(6, "2026-10-31T15:00:00+05:30", attendees=[{"email": "dana@harbourline.test"}])])
    assert [p[0] for p in n.posts] == ["fleck-event-6"] and n.posts[0][3] == datetime(2026, 10, 31, 9, 25, tzinfo=timezone.utc)
    assert n.cleared == 2
