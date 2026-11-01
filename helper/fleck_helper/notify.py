"""Meeting reminders (spec 7.5): a notification with a **Record** action 5 minutes before each event that looks like a
call (it has a meeting link or attendees). Tapping the notification's body also starts recording.
macOS-only imports are lazy so Linux CI can import this module."""
import logging
from datetime import datetime, timedelta, timezone

LEAD = timedelta(minutes=5)
CATEGORY, ACTION = "meeting", "record"
log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, notifier, clock=lambda: datetime.now(timezone.utc)):
        self.notifier, self.clock = notifier, clock

    def schedule(self, events: list[dict]) -> None:
        """Replaces all pending reminders with one per upcoming call in `events` (the api's /api/events rows)."""
        self.notifier.clear()
        now = self.clock()
        for e in events:
            at = datetime.fromisoformat(e["start_at"]) - LEAD
            if (e.get("meeting_link") or e.get("attendees")) and not e.get("cancelled") and at > now:
                self.notifier.post(f"fleck-event-{e['id']}", e["title"], "Starts in 5 min.", at, e["id"])


class UNNotifier:
    """UNUserNotificationCenter with the Record category. `on_record(event_id)` runs on the delegate's thread."""

    def __init__(self, on_record, clock=lambda: datetime.now(timezone.utc)):
        import UserNotifications as UN
        from Foundation import NSObject

        self.UN, self.clock = UN, clock

        class FleckNotifyDelegate(NSObject):
            def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, c, resp, done):
                act = str(resp.actionIdentifier())
                if act in (ACTION, str(UN.UNNotificationDefaultActionIdentifier)):  # the button, or a tap on the body
                    event_id = resp.notification().request().content().userInfo().get("event_id")
                    try:
                        on_record(int(event_id) if event_id is not None else None)
                    except Exception:
                        log.exception("Record from a notification failed")
                done()

            def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, c, n, done):
                done(UN.UNNotificationPresentationOptionBanner | UN.UNNotificationPresentationOptionList)

        self.center = UN.UNUserNotificationCenter.currentNotificationCenter()
        self._delegate = FleckNotifyDelegate.alloc().init()  # the center holds it weakly
        self.center.setDelegate_(self._delegate)
        action = UN.UNNotificationAction.actionWithIdentifier_title_options_(ACTION, "Record", UN.UNNotificationActionOptionForeground)
        self.center.setNotificationCategories_({UN.UNNotificationCategory.categoryWithIdentifier_actions_intentIdentifiers_options_(
            CATEGORY, [action], [], 0)})
        self.center.requestAuthorizationWithOptions_completionHandler_(
            UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound, lambda ok, err: None)

    def clear(self) -> None:
        self.center.removeAllPendingNotificationRequests()

    def post(self, ident: str, title: str, body: str, at: datetime, event_id: int) -> None:
        UN = self.UN
        content = UN.UNMutableNotificationContent.alloc().init()
        content.setTitle_(title)
        content.setBody_(body)
        content.setCategoryIdentifier_(CATEGORY)
        content.setUserInfo_({"event_id": event_id})
        trigger = UN.UNTimeIntervalNotificationTrigger.triggerWithTimeInterval_repeats_(
            max(1.0, (at - self.clock()).total_seconds()), False)
        self.center.addNotificationRequest_withCompletionHandler_(
            UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(ident, content, trigger), lambda err: None)
