"""The floating answer panel (spec 7.5; mockups Main.dc.html, PanelStates.dc.html).

`PanelModel` turns assist events into cards (pure Python, tested); `AnswerPanel` draws the current card in a
non-activating NSPanel that floats over every app and is left out of screen captures. macOS-only imports are lazy
so Linux CI can import this module."""
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from fleck_core.fallback import question_sentences

LABELS = {"library": "Our material", "general": "General knowledge", "reference": "Reference contract",
          "not_in_material": "Not in our material", "thinking": "Thinking…"}
STRIPE = {"library": "#2FB8A8", "general": "#F2B95A", "reference": "#B39DF0", "thinking": "#6B7A88",
          "not_in_material": None}  # None: hatched grey
CHIP = {"library": ("#2FB8A8", "#08201D"), "general": ("#F2B95A", "#2A1A00"), "reference": ("#B39DF0", "#1E1240")}
SHORTCUT = "⌥⌘F"


@dataclass(frozen=True)
class PanelView:
    question: str
    text: str
    kind: str  # an AssistEvent kind, or "thinking" until the answer's kind is known
    source_text: str
    index: int  # 1-based
    total: int
    asked_ms: int = 0  # meeting time of the question's last segment

    @property
    def label(self) -> str:
        return LABELS[self.kind]


@dataclass
class _Card:
    assist_id: str
    question: str
    asked_ms: int
    sentences: dict = field(default_factory=dict)
    kind: str | None = None
    citations: list = field(default_factory=list)
    final: str | None = None  # done.text: the whole answer as shown


def _source_text(kind: str, citations) -> str:
    titles = list(dict.fromkeys(c.title for c in citations))
    if kind == "general":
        return "Not from our material"
    if kind == "reference":
        return f"Not our work: {next((c.title for c in citations if c.kind == 'reference'), titles[0] if titles else '')}"
    if kind == "library":
        return ", ".join(titles)
    return ""


class PanelModel:
    """Cards in question order; a new card's `thinking` makes it current, `prev`/`next` page through the rest."""

    def __init__(self):
        self._cards: list[_Card] = []
        self._i = -1
        self.meeting_id: str | None = None  # set: events of other meetings are ignored
        self._lock = threading.Lock()  # events come from answer threads, reads from the main thread

    def reset(self, meeting_id: str | None) -> None:
        with self._lock:
            self._cards, self._i, self.meeting_id = [], -1, meeting_id

    def apply(self, e) -> None:
        with self._lock:
            if self.meeting_id is not None and e.meeting_id != self.meeting_id:
                return  # an answer thread of an earlier meeting finishing late
            card = next((c for c in self._cards if c.assist_id == e.assist_id), None)
            if e.phase == "thinking":
                fresh = _Card(e.assist_id, " ".join(question_sentences(e.question)), _asked_ms(e.segment_id))
                if card is None:  # a new question
                    self._cards.append(fresh)
                    self._i = len(self._cards) - 1
                else:  # a newer answer for the same turn replaces the card (decision 104)
                    self._cards[self._cards.index(card)] = fresh
                return
            if card is None:
                return
            if e.phase == "delta":
                card.sentences[e.sentence_index] = card.sentences.get(e.sentence_index, "") + e.text
            elif e.phase == "sentence":
                card.sentences[e.sentence_index] = e.text
                card.kind = e.kind or card.kind
                card.citations += [c for c in e.citations if c not in card.citations]
            elif e.phase == "done":
                card.kind, card.final = e.kind, e.text
                card.citations = list(e.citations) or card.citations

    @property
    def current(self) -> PanelView | None:
        with self._lock:
            if self._i < 0:
                return None
            c = self._cards[self._i]
            kind = c.kind or "thinking"  # streamed text is unlabelled until its checks pass
            text = c.final if c.final is not None else " ".join(t for _, t in sorted(c.sentences.items()) if t)
            return PanelView(c.question, text, kind, _source_text(kind, c.citations), self._i + 1, len(self._cards), c.asked_ms)

    def prev(self) -> None:
        with self._lock:
            self._i = max(0, self._i - 1) if self._cards else -1

    def next(self) -> None:
        with self._lock:
            self._i = min(len(self._cards) - 1, self._i + 1)


def _asked_ms(segment_id: str) -> int:
    try:
        return int(segment_id.rsplit("-", 1)[1])  # "client-61000": the segment's start
    except (IndexError, ValueError):
        return 0


def place(saved, screens, width: int, gap: int = 16) -> tuple[float, float]:
    """The panel's top-left: `saved` if it lies on one of `screens` (visibleFrames as ((x, y), (w, h))), pulled in
    horizontally to fit; else top-centre of the first screen, `gap` below the menu bar."""
    for (x, y), (w, h) in screens:
        if saved and x <= saved[0] < x + w and y < saved[1] <= y + h:
            return (float(min(max(saved[0], x), x + w - width)), float(saved[1]))
    (x, y), (w, h) = screens[0]
    return (x + (w - width) / 2, y + h - gap)


def clock(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


# ---- AppKit ----------------------------------------------------------------------------------------------------

WIDTH, PAD_L, PAD_R, STRIPE_W = 540, 16, 18, 6
DEFAULTS_KEY = "FleckPanelOrigin"


def _color(hex_: str, alpha: float = 1.0):
    import Quartz  # noqa: F401  (registers CGColorRef, so .CGColor() is a real colour, not a raw pointer)
    from AppKit import NSColor
    h = hex_.lstrip("#")
    return NSColor.colorWithSRGBRed_green_blue_alpha_(int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, alpha)


def _hatch():
    """The 'don't quote a number' stripe: #8A97A4 / #3B4652 diagonal bands, 4 px each."""
    from AppKit import NSBezierPath, NSColor, NSImage, NSMakeRect
    img = NSImage.alloc().initWithSize_((8, 8))
    img.lockFocus()
    _color("#3B4652").set()
    NSBezierPath.fillRect_(NSMakeRect(0, 0, 8, 8))
    _color("#8A97A4").set()
    for off in (-8, 0, 8):
        p = NSBezierPath.bezierPath()
        p.moveToPoint_((off, 0)), p.lineToPoint_((off + 4, 0)), p.lineToPoint_((off + 12, 8)), p.lineToPoint_((off + 8, 8))
        p.closePath(), p.fill()
    img.unlockFocus()
    return NSColor.colorWithPatternImage_(img)


class AnswerPanel:
    """Shown while recording (`show`/`hide`); ⌥⌘F or the Hide button hides it until pressed again (`toggle`).
    Thread-safe: `apply` may be called from any thread; drawing happens on the main thread."""

    def __init__(self, model: PanelModel | None = None, on_visibility=None):
        self.model = model or PanelModel()
        self.on_visibility = on_visibility or (lambda visible: None)
        self.active = self.user_hidden = False
        self._panel = None

    # -- the Recorder's interface
    def apply(self, e) -> None:
        self.model.apply(e)
        self._main(self._render)

    def show(self, meeting_id: str | None = None) -> None:
        """Starts a meeting's panel: empty until its first question."""
        self.model.reset(meeting_id)
        self.active = True
        self._main(self._update_visibility)

    def hide(self) -> None:
        self.active = False
        self._main(self._update_visibility)

    def toggle(self) -> None:
        self.user_hidden = not self.user_hidden
        self._main(self._update_visibility)

    @property
    def visible(self) -> bool:
        return self.active and not self.user_hidden

    # -- AppKit, main thread only
    @staticmethod
    def _main(fn) -> None:
        from Foundation import NSThread
        from PyObjCTools import AppHelper
        if NSThread.isMainThread():
            fn()
        else:
            AppHelper.callAfter(fn)

    def install_shortcut(self) -> None:
        """⌥⌘F anywhere (the global monitor needs Accessibility permission) and while Fleck's menu is open."""
        from AppKit import NSEvent, NSEventMaskKeyDown, NSEventModifierFlagCommand, NSEventModifierFlagOption
        want = NSEventModifierFlagCommand | NSEventModifierFlagOption

        def hit(ev) -> bool:
            return ev.keyCode() == 3 and (ev.modifierFlags() & 0xFFFF0000 & ~0x00010000) == want  # 3 = F; caps lock ignored

        def on_global(ev):
            if hit(ev):
                self.toggle()

        def on_local(ev):
            if hit(ev):
                self.toggle()
                return None
            return ev
        self._monitors = (NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_global),
                          NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_local))

    def _update_visibility(self) -> None:
        if self.visible:
            self._render()
            self._panel.orderFrontRegardless()  # never makeKeyWindow: the call app keeps focus
        elif self._panel is not None:
            self._panel.orderOut_(None)
        self.on_visibility(self.visible)

    def _build(self):
        import objc
        from AppKit import (NSBackingStoreBuffered, NSColor, NSPanel, NSStatusWindowLevel, NSView,
                            NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
                            NSWindowSharingNone, NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel)
        from Foundation import NSNotificationCenter, NSObject, NSUserDefaults
        p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (WIDTH, 120)), NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        p.setLevel_(NSStatusWindowLevel)
        p.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary)
        p.setSharingType_(NSWindowSharingNone)  # left out of screen shares and recordings
        p.setBecomesKeyOnlyIfNeeded_(True)
        p.setFloatingPanel_(True)
        p.setHidesOnDeactivate_(False)
        p.setMovableByWindowBackground_(True)
        p.setOpaque_(False)
        p.setHasShadow_(True)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setAccessibilityLabel_("Fleck answer")
        root = NSView.alloc().initWithFrame_(((0, 0), (WIDTH, 120)))
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(_color("#11181F", 0.94).CGColor())
        root.layer().setCornerRadius_(14)
        root.layer().setMasksToBounds_(True)
        root.layer().setBorderWidth_(1)
        root.layer().setBorderColor_(_color("#FFFFFF", 0.10).CGColor())
        p.setContentView_(root)
        self._root, self._panel = root, p
        self._hatch = _hatch()

        saved = NSUserDefaults.standardUserDefaults().stringForKey_(DEFAULTS_KEY)
        self._origin = tuple(float(v) for v in saved.split(",")) if saved else None
        owner = self

        class _Mover(NSObject):
            def moved_(self, note):
                o = owner._panel.frame().origin
                owner._origin = (o.x, o.y + owner._panel.frame().size.height)  # top-left: the height changes per card
                NSUserDefaults.standardUserDefaults().setObject_forKey_(f"{owner._origin[0]},{owner._origin[1]}", DEFAULTS_KEY)
        self._mover = _Mover.alloc().init()
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self._mover, objc.selector(self._mover.moved_, signature=b"v@:@"), "NSWindowDidMoveNotification", p)

    def _render(self) -> None:
        from AppKit import NSButton, NSFont, NSFontWeightBold, NSFontWeightMedium, NSFontWeightRegular, NSImage, NSScreen, NSTextField, NSView
        if self._panel is None:
            self._build()
        for v in list(self._root.subviews()):
            v.removeFromSuperview()
        v = self.model.current
        inner = WIDTH - STRIPE_W - PAD_L - PAD_R
        x0 = STRIPE_W + PAD_L
        rows = []  # (view, height, gap above), laid out top-down

        def label(text, size, color, weight=NSFontWeightRegular, wrap=True, width=inner):
            f = NSTextField.wrappingLabelWithString_(text) if wrap else NSTextField.labelWithString_(text)
            f.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
            f.setTextColor_(_color(color))
            f.setSelectable_(False)
            h = f.cell().cellSizeForBounds_(((0, 0), (width, 10_000))).height
            f.setFrameSize_((width, h))
            return f, h

        kind = v.kind if v else "thinking"
        head, hh = label(f"Client asked at {clock(v.asked_ms)}" if v else "Fleck is listening", 14, "#A9B6C3", wrap=False, width=inner - 80)
        rows.append((head, hh, 14))
        if v:
            count, _ = label(f"{v.index} of {v.total}", 13, "#A9B6C3", wrap=False, width=80)
            count.setAlignment_(2)  # right
            q, qh = label(v.question, 16, "#D5DEE7", NSFontWeightMedium)
            rows.append((q, qh, 8))
            if v.text:
                a, ah = label(v.text, 20, "#FFFFFF", NSFontWeightMedium)
            else:
                a, ah = label("Thinking…" if kind == "thinking" else "", 16, "#D5DEE7")
            if ah and a.stringValue():
                a.setAccessibilityLabel_(v.text or "Thinking…")
                rows.append((a, ah, 8))
        foot_h = 32
        height = sum(h + g for _, h, g in rows) + 10 + foot_h + 12
        y = height
        for view, h, gap in rows:
            y -= gap + h
            view.setFrameOrigin_((x0, y))
            self._root.addSubview_(view)
        if v:
            count.setFrameOrigin_((WIDTH - PAD_R - 80, rows[0][0].frame().origin.y))
            self._root.addSubview_(count)

        # footer: source chip and text on the left, Previous / Next / Hide on the right
        fy = 12
        bx = WIDTH - PAD_R

        def button(title, name, action, symbol=None, enabled=True, w=32):
            nonlocal bx
            b = NSButton.alloc().initWithFrame_(((0, 0), (w, 32)))
            b.setBezelStyle_(1)
            b.setBordered_(False)
            b.setWantsLayer_(True)
            b.layer().setCornerRadius_(6)
            b.layer().setBorderWidth_(1)
            b.layer().setBorderColor_(_color("#FFFFFF", 0.18 if enabled else 0.08).CGColor())
            if symbol:
                b.setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, name))
                b.setTitle_("")
            else:
                b.setTitle_(title)
            b.setContentTintColor_(_color("#E3E9EF" if enabled else "#5E6B78"))
            b.setAccessibilityLabel_(name)
            b.setEnabled_(enabled)
            b.setTarget_(self._actions)
            b.setAction_(action)
            bx -= w
            b.setFrameOrigin_((bx, fy))
            bx -= 4
            self._root.addSubview_(b)
        self._actions = self._actions if hasattr(self, "_actions") else _Actions.alloc().initWithPanel_(self)
        button(f"Hide {SHORTCUT}", "Hide panel (Option Command F)", "hide:", w=78)
        if v:
            button("", "Next answer", "next:", "chevron.right", enabled=v.index < v.total)
            button("", "Previous answer", "prev:", "chevron.left", enabled=v.index > 1)
            if kind != "thinking":
                chip, ch = label(v.label, 13, CHIP.get(kind, ("", "#E3E9EF"))[1], NSFontWeightBold, wrap=False, width=200)
                chip.sizeToFit()
                ch = chip.frame().size.height
                cw = chip.frame().size.width + 16
                box = NSView.alloc().initWithFrame_(((x0, fy + 5), (cw, ch + 6)))
                box.setWantsLayer_(True)
                box.layer().setCornerRadius_(5)
                if kind in CHIP:
                    box.layer().setBackgroundColor_(_color(CHIP[kind][0]).CGColor())
                else:  # shortcut: solid 1.5 px outline where the mockup dashes it; a CAShapeLayer if it matters
                    box.layer().setBorderWidth_(1.5)
                    box.layer().setBorderColor_(_color("#A9B6C3").CGColor())
                chip.setFrameOrigin_((8, 3))
                box.addSubview_(chip)
                box.setAccessibilityLabel_(v.label)
                self._root.addSubview_(box)
                if v.source_text:
                    src, sh = label(v.source_text, 14, "#D5DEE7", wrap=False, width=max(40, bx - (x0 + cw + 8) - 8))
                    src.cell().setLineBreakMode_(4)  # truncate tail
                    src.setFrameOrigin_((x0 + cw + 8, fy + 7))
                    self._root.addSubview_(src)

        stripe = NSView.alloc().initWithFrame_(((0, 0), (STRIPE_W, height)))
        stripe.setWantsLayer_(True)
        stripe.layer().setBackgroundColor_((self._hatch if STRIPE[kind] is None else _color(STRIPE[kind])).CGColor())
        self._root.addSubview_(stripe)

        self._origin = place(self._origin, [((f.origin.x, f.origin.y), (f.size.width, f.size.height))
                                            for f in (sc.visibleFrame() for sc in NSScreen.screens())], WIDTH)
        self._panel.setFrame_display_(((self._origin[0], self._origin[1] - height), (WIDTH, height)), True)
        self._root.setFrame_(((0, 0), (WIDTH, height)))
        if snap := os.environ.get("FLECK_PANEL_SNAPSHOT"):
            self._snapshot(Path(snap))

    def _snapshot(self, folder: Path) -> None:
        """Writes the panel as drawn to a PNG (FLECK_PANEL_SNAPSHOT=<folder>): the panel is left out of screen
        captures, so `screencapture` cannot show it."""
        from AppKit import NSBitmapImageFileTypePNG
        self._root.display()
        rep = self._root.bitmapImageRepForCachingDisplayInRect_(self._root.bounds())
        self._root.cacheDisplayInRect_toBitmapImageRep_(self._root.bounds(), rep)
        folder.mkdir(parents=True, exist_ok=True)
        self._snaps = getattr(self, "_snaps", 0) + 1
        rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(
            str(folder / f"panel-{self._snaps:04d}.png"), True)


def _actions_class():
    from Foundation import NSObject

    class FleckPanelActions(NSObject):
        def initWithPanel_(self, panel):
            self = self.init()
            self.panel = panel
            return self

        def hide_(self, sender):
            self.panel.toggle()

        def prev_(self, sender):
            self.panel.model.prev()
            self.panel._render()

        def next_(self, sender):
            self.panel.model.next()
            self.panel._render()
    return FleckPanelActions


class _LazyActions:
    """The NSObject subclass is defined on first use (AppKit is imported lazily) and only once per process."""
    _cls = None

    def alloc(self):
        if _LazyActions._cls is None:
            _LazyActions._cls = _actions_class()
        return _LazyActions._cls.alloc()


_Actions = _LazyActions()
