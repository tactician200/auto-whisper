"""FloatingHUD — NSPanel-based translucent overlay, top-center of screen."""
import math
import time
import threading
import logging
import objc
from Foundation import NSObject, NSTimer as _NSTimerF
from AppKit import (
    NSPanel,
    NSScreen,
    NSColor,
    NSView,
    NSTextField,
    NSFont,
    NSVisualEffectView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectStateActive,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
    NSAnimationContext,
    NSStatusWindowLevel,
    NSWindowStyleMaskBorderless,
    NSMakeRect,
    NSTimer,
    NSAppearance,
    NSViewMinYMargin,    # anchor to top of superview
    NSViewMaxYMargin,    # anchor to bottom of superview
    NSEvent,
)
from AppKit import NSWindowStyleMaskNonactivatingPanel
from Quartz import CAMediaTimingFunction
from PyObjCTools import AppHelper

from auto_whisper.ui.tokens import (
    HUD_WIDTH, HUD_HEIGHT, HUD_TOP_INSET, HUD_RIGHT_INSET, HUD_ALPHA,
    BG_MATERIAL,
    GLASS_MATERIAL, GLASS_ALPHA, GLASS_BG_TINT_ALPHA, GLASS_BORDER_ALPHA,
    GLASS_TOP_HIGHLIGHT_ALPHA, GLASS_REFLECTION_ALPHA,
    GLASS_DROP_SHADOW_ALPHA, GLASS_DROP_SHADOW_RADIUS, GLASS_DROP_SHADOW_OFFSET_Y,
    FADE_IN_MS, FADE_OUT_MS, ARRIVAL_SLIDE,
    ACCENT, PRIVACY_ACCENT,
    PADDING_LR, TOP_ROW_HEIGHT, WAVEFORM_HEIGHT, INNER_GAP,
    BAR_COUNT, BAR_WIDTH, BAR_GAP,
    PREVIEW_MS,
)
from auto_whisper.ui.waveform_view import WaveformBarsView
from auto_whisper.ui.mode_chip import ModeChip
from auto_whisper.ui.preview_view import PreviewTextView
from auto_whisper.ui.privacy_chip import PrivacyChip

logger = logging.getLogger(__name__)

# NSEventMask for key-down events
_NSEventMaskKeyDown = 1 << 10


class _HUDTimerTarget(NSObject):
    """ObjC-compatible NSTimer target. Holds a weak reference to the FloatingHUD."""

    def initWithHUD_(self, hud):
        self = objc.super(_HUDTimerTarget, self).init()
        if self is None:
            return None
        self._hud_ref = hud
        return self

    def tick_(self, timer):
        hud = self._hud_ref
        if hud is not None:
            hud._tick()


class FloatingHUD:
    def __init__(self):
        self._panel = None
        self._mode: str = "dictate"
        self._privacy: bool = False

        self._waveform = None
        self._mode_chip = None
        self._privacy_chip = None
        self._rec_dot = None
        self._rec_label = None
        self._duration_label = None
        self._preview_view = None

        self._timer = None
        self._timer_target = None
        self._show_time: float = 0.0
        self._dot_tick: int = 0
        self._dot_state: bool = True
        self._opening: bool = False     # True while spring-bounce open animation runs

        # Preview state
        self._preview_timer = None      # NSTimer one-shot for 800ms countdown
        self._preview_callback = None   # callable(cancelled: bool)
        self._esc_monitor = None        # NSEvent global monitor ref

    def _build_panel(self) -> None:
        """Create and configure the NSPanel. Called once, lazily on first show()."""
        rect = NSMakeRect(0, 0, HUD_WIDTH, HUD_HEIGHT)

        style_mask = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style_mask, 2, False  # 2 = NSBackingStoreBuffered
        )

        # Vibrant dark pairs correctly with the HUDWindow material — same
        # appearance Apple uses on the volume/brightness HUDs. Gives the
        # characteristic dark frosted look across light/dark/auto modes.
        panel.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark"))
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary
        )
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setIgnoresMouseEvents_(True)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        # Native window drop shadow — matches the codepen .glass box-shadow
        # outer component (0 8px 32px rgba(0,0,0,0.28)). AppKit also tweaks
        # the shadow when the panel resizes, so this stays consistent through
        # the show/hide animation.
        panel.setHasShadow_(True)

        # Liquid-glass composition — five stacked layers, all under the chips
        # and waveform so they remain crisp on top. See the codepen
        # .glass base class for the visual reference; tokens are 1:1.
        #
        # z-order, bottom to top:
        #   1. content_view (rounded clip mask)
        #   2. glass_view       — NSVisualEffectView, HUDWindow material, behindWindow blend
        #   3. bg_tint_layer    — solid white α=0.12 (the --glass-white tint)
        #   4. reflection_layer — 135° diagonal gradient (white α=0.40 → 0 at 50%)
        #   5. top_highlight    — 1px white α=0.30 strip at the very top
        #   6. rim_layer        — 1px CAShapeLayer stroke α=0.25 inset 0.5px
        # Chips, REC dot, waveform, etc., sit above all of these as NSView
        # subviews of content_view.
        from AppKit import NSViewWidthSizable, NSViewHeightSizable
        from Quartz import CAGradientLayer, CALayer, CAShapeLayer, CGPathCreateWithRoundedRect
        from Quartz.CoreGraphics import CGRectMake

        content_view = NSView.alloc().initWithFrame_(rect)
        content_view.setWantsLayer_(True)
        content_view.layer().setCornerRadius_(16)
        content_view.layer().setMasksToBounds_(True)
        content_view.layer().setBackgroundColor_(NSColor.clearColor().CGColor())

        glass_view = NSVisualEffectView.alloc().initWithFrame_(rect)
        glass_view.setMaterial_(GLASS_MATERIAL)
        glass_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        glass_view.setState_(NSVisualEffectStateActive)
        glass_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        glass_view.setAlphaValue_(GLASS_ALPHA)
        content_view.addSubview_(glass_view)

        # --- Liquid-glass overlay stack on the content_view's layer ---
        cv_layer = content_view.layer()

        # 3. white tint (the --glass-white characteristic body of the glass)
        bg_tint_layer = CALayer.layer()
        bg_tint_layer.setFrame_(rect)
        bg_tint_layer.setBackgroundColor_(
            NSColor.whiteColor().colorWithAlphaComponent_(GLASS_BG_TINT_ALPHA).CGColor()
        )
        cv_layer.addSublayer_(bg_tint_layer)

        # 4. 135° diagonal reflection — top-left bright, fades to nothing by 50%
        reflection_layer = CAGradientLayer.layer()
        reflection_layer.setFrame_(rect)
        reflection_layer.setColors_([
            NSColor.whiteColor().colorWithAlphaComponent_(GLASS_REFLECTION_ALPHA).CGColor(),
            NSColor.whiteColor().colorWithAlphaComponent_(0.0).CGColor(),
        ])
        reflection_layer.setLocations_([0.0, 0.5])
        # CSS 135deg = top-left → bottom-right. In CALayer coords that's
        # startPoint=(0,1) → endPoint=(1,0) (y is bottom-up).
        reflection_layer.setStartPoint_((0.0, 1.0))
        reflection_layer.setEndPoint_((1.0, 0.0))
        cv_layer.addSublayer_(reflection_layer)

        # 5. top highlight — 1px white strip at the very top edge (inset 0 1px 0)
        top_highlight = CALayer.layer()
        top_highlight.setFrame_(NSMakeRect(0, HUD_HEIGHT - 1, HUD_WIDTH, 1))
        top_highlight.setBackgroundColor_(
            NSColor.whiteColor().colorWithAlphaComponent_(GLASS_TOP_HIGHLIGHT_ALPHA).CGColor()
        )
        cv_layer.addSublayer_(top_highlight)

        # 6. rim border — 1px hairline stroke around the rounded rect
        rim_layer = CAShapeLayer.layer()
        rim_layer.setFrame_(rect)
        # Inset 0.5px so the 1px stroke sits exactly on the edge after the
        # corner-radius clip; otherwise half the stroke gets clipped.
        rim_path = CGPathCreateWithRoundedRect(
            CGRectMake(0.5, 0.5, HUD_WIDTH - 1.0, HUD_HEIGHT - 1.0),
            15.5, 15.5, None,
        )
        rim_layer.setPath_(rim_path)
        rim_layer.setFillColor_(NSColor.clearColor().CGColor())
        rim_layer.setStrokeColor_(
            NSColor.whiteColor().colorWithAlphaComponent_(GLASS_BORDER_ALPHA).CGColor()
        )
        rim_layer.setLineWidth_(1.0)
        cv_layer.addSublayer_(rim_layer)

        # Save handles in case future code needs to retint or animate them.
        self._glass_view = glass_view
        self._bg_tint_layer = bg_tint_layer
        self._reflection_layer = reflection_layer
        self._top_highlight_layer = top_highlight
        self._rim_layer = rim_layer

        panel.setContentView_(content_view)
        self._panel = panel
        # All later subview adds target the content_view (kept under the old
        # name to minimise churn in the rest of this method).
        effect_view = content_view

        # -- Cluster-centered layout --
        # The full visual cluster (top row + gap + waveform) is centered
        # vertically in the HUD so it doesn't look top-heavy.
        cluster_height = TOP_ROW_HEIGHT + INNER_GAP + WAVEFORM_HEIGHT
        cluster_top_pad = (HUD_HEIGHT - cluster_height) / 2.0

        # -- Privacy chip (top-left, only when privacy mode is on) --
        chip_y = HUD_HEIGHT - cluster_top_pad - TOP_ROW_HEIGHT
        mode_chip_x = PADDING_LR
        if self._privacy:
            self._privacy_chip = PrivacyChip.chip()
            privacy_w = self._privacy_chip.width()
            self._privacy_chip.setFrame_(
                NSMakeRect(PADDING_LR, chip_y, privacy_w, TOP_ROW_HEIGHT)
            )
            self._privacy_chip.setAutoresizingMask_(NSViewMinYMargin)
            effect_view.addSubview_(self._privacy_chip)
            mode_chip_x = PADDING_LR + privacy_w + 6

        # -- Mode chip (shifted right when privacy is on) --
        accent = PRIVACY_ACCENT if self._privacy else ACCENT
        self._mode_chip = ModeChip.chipWithMode_color_(self._mode, accent)
        chip_frame = self._mode_chip.frame()
        self._mode_chip.setFrame_(NSMakeRect(mode_chip_x, chip_y, chip_frame.size.width, TOP_ROW_HEIGHT))
        self._mode_chip.setAutoresizingMask_(NSViewMinYMargin)  # anchored to TOP
        effect_view.addSubview_(self._mode_chip)

        # -- REC dot (red circle, 9x9 with inset specular ring + soft glow) --
        chip_right = mode_chip_x + self._mode_chip.width() + 6
        dot_y = chip_y + (TOP_ROW_HEIGHT - 9) / 2.0
        self._rec_dot = NSView.alloc().initWithFrame_(NSMakeRect(chip_right, dot_y, 9, 9))
        self._rec_dot.setAutoresizingMask_(NSViewMinYMargin)
        self._rec_dot.setWantsLayer_(True)
        dot_layer = self._rec_dot.layer()
        red = NSColor.colorWithRed_green_blue_alpha_(1.0, 0.27, 0.27, 1.0)
        dot_layer.setBackgroundColor_(red.CGColor())
        dot_layer.setCornerRadius_(4.5)
        # Soft glow — same red, halates outward. Free since layer-backed.
        dot_layer.setShadowColor_(red.CGColor())
        dot_layer.setShadowOpacity_(0.6)
        dot_layer.setShadowRadius_(4.0)
        dot_layer.setShadowOffset_((0, 0))
        dot_layer.setMasksToBounds_(False)
        # Inset specular highlight — 0.5px white border ring (Apple liquid cue).
        from Quartz import CALayer as _CALayer
        ring = _CALayer.layer()
        ring.setFrame_(NSMakeRect(0.5, 0.5, 8, 8))
        ring.setCornerRadius_(4.0)
        ring.setBorderWidth_(0.5)
        ring.setBorderColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.35).CGColor())
        dot_layer.addSublayer_(ring)
        effect_view.addSubview_(self._rec_dot)

        # -- REC label --
        rec_x = chip_right + 10
        self._rec_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(rec_x, chip_y, 30, TOP_ROW_HEIGHT)
        )
        self._rec_label.setEditable_(False)
        self._rec_label.setBezeled_(False)
        self._rec_label.setDrawsBackground_(False)
        self._rec_label.setSelectable_(False)
        self._rec_label.setFont_(
            NSFont.systemFontOfSize_weight_(10, 0.30)  # ~NSFontWeightSemibold
        )
        self._rec_label.setTextColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.85))
        self._rec_label.setStringValue_("REC")
        self._rec_label.setAutoresizingMask_(NSViewMinYMargin)  # anchored to TOP
        effect_view.addSubview_(self._rec_label)

        # -- Duration label (top-right) --
        dur_w = 36
        dur_x = HUD_WIDTH - PADDING_LR - dur_w
        self._duration_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(dur_x, chip_y, dur_w, TOP_ROW_HEIGHT)
        )
        self._duration_label.setEditable_(False)
        self._duration_label.setBezeled_(False)
        self._duration_label.setDrawsBackground_(False)
        self._duration_label.setSelectable_(False)
        self._duration_label.setFont_(
            NSFont.monospacedSystemFontOfSize_weight_(10, 0.23)  # 10pt medium mono
        )
        self._duration_label.setTextColor_(NSColor.whiteColor().colorWithAlphaComponent_(0.85))
        self._duration_label.setStringValue_("0:00")
        self._duration_label.setAutoresizingMask_(NSViewMinYMargin)  # anchored to TOP
        effect_view.addSubview_(self._duration_label)

        # -- Waveform — positioned by the cluster math, not by a magic constant.
        # cluster = top_row + INNER_GAP + WAVEFORM_HEIGHT; cluster is centered
        # vertically in the HUD, waveform sits at the bottom of the cluster.
        wave_h = WAVEFORM_HEIGHT
        wave_w = HUD_WIDTH - 2 * PADDING_LR
        wave_y = HUD_HEIGHT - cluster_top_pad - TOP_ROW_HEIGHT - INNER_GAP - wave_h
        self._waveform = WaveformBarsView.alloc().initWithFrame_(
            NSMakeRect(PADDING_LR, wave_y, wave_w, wave_h)
        )
        self._waveform.set_color(accent)
        self._waveform.setAutoresizingMask_(NSViewMaxYMargin)  # anchored to BOTTOM
        # Clip drawing to view bounds — earlier the bars were drawing
        # well outside the wave view's frame because the dirty rect
        # passed to drawRect_ was larger than self.bounds.
        self._waveform.setWantsLayer_(True)
        self._waveform.layer().setMasksToBounds_(True)
        effect_view.addSubview_(self._waveform)

        # -- Preview text view (same frame as waveform, hidden initially) --
        self._preview_view = PreviewTextView.alloc().initWithFrame_(
            NSMakeRect(PADDING_LR, wave_y, wave_w, wave_h)
        )
        self._preview_view.setAutoresizingMask_(NSViewMaxYMargin)
        self._preview_view.setHidden_(True)
        effect_view.addSubview_(self._preview_view)

    def _position_panel(self) -> None:
        """Position the panel top-right of main screen, respecting menubar and notch."""
        screen = NSScreen.mainScreen()
        if screen is None:
            return
        frame = screen.visibleFrame()
        top_inset = HUD_TOP_INSET
        try:
            top_inset += screen.safeAreaInsets().top
        except Exception:
            pass
        x = frame.origin.x + frame.size.width - HUD_WIDTH - HUD_RIGHT_INSET
        y = frame.origin.y + frame.size.height - HUD_HEIGHT - top_inset
        self._panel.setFrameOrigin_((x, y))

    def _start_timer(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        self._timer_target = _HUDTimerTarget.alloc().initWithHUD_(self)
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / 60.0,
            self._timer_target,
            "tick:",
            None,
            True,
        )

    def _tick(self) -> None:
        if self._waveform is not None:
            self._waveform.drain_queue()

        # Update duration label
        if self._duration_label is not None and self._show_time:
            elapsed = int(time.monotonic() - self._show_time)
            m = elapsed // 60
            s = elapsed % 60
            self._duration_label.setStringValue_(f"{m}:{s:02d}")

        # Pulse rec dot — sinusoid (breathing) instead of binary blink.
        # Period 1.1s feels alive without being distracting.
        if self._rec_dot is not None:
            t = time.monotonic() - self._show_time
            opacity = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(2.0 * math.pi * t / 1.1))
            self._rec_dot.layer().setOpacity_(opacity)

        # Audio-reactive HUD pulse — subtle alpha breathe with voice.
        # Skipped during the open animation so it doesn't fight the spring.
        if not self._opening and self._waveform is not None and self._panel is not None:
            recent = self._waveform.recent_level()
            pulse = min(recent * 5.0, 0.06)            # cap at +0.06 alpha boost
            target = HUD_ALPHA + pulse                  # idle = HUD_ALPHA, loud ≈ HUD_ALPHA + 0.06
            current = self._panel.alphaValue()
            self._panel.setAlphaValue_(current * 0.7 + target * 0.3)

    def show(self, mode: str = "dictate", privacy: bool = False) -> None:
        """Show the HUD with fade-in."""
        self._mode = mode
        self._privacy = privacy

        def _show_main():
            # If privacy state changed since last build, tear down so the
            # layout (chip presence + mode_chip x-offset) gets rebuilt.
            if self._panel is not None and bool(self._privacy_chip) != bool(privacy):
                self._panel.orderOut_(None)
                self._panel = None
                self._waveform = None
                self._mode_chip = None
                self._privacy_chip = None
                self._rec_dot = None
                self._rec_label = None
                self._duration_label = None
                self._preview_view = None

            if self._panel is None:
                self._build_panel()
            else:
                if self._mode_chip is not None:
                    self._mode_chip.set_mode(mode)

            # Reset state for new recording session (preview subviews + timers)
            self._reset_preview_state()
            if self._duration_label is not None:
                self._duration_label.setStringValue_("0:00")
            if self._waveform is not None:
                self._waveform.reset()
            self._dot_tick = 0
            self._dot_state = True
            self._show_time = time.monotonic()

            # Position offset slightly above the final spot, then animate
            # alpha + an 8px slide-down with ease-out cubic. Gives the HUD a
            # sense of arriving from the menubar rather than blinking in.
            self._position_panel()
            final_x, final_y = self._panel.frame().origin.x, self._panel.frame().origin.y
            self._panel.setFrameOrigin_((final_x, final_y + ARRIVAL_SLIDE))
            self._panel.setAlphaValue_(0.0)
            self._panel.orderFrontRegardless()

            self._opening = True
            NSAnimationContext.beginGrouping()
            NSAnimationContext.currentContext().setDuration_(FADE_IN_MS / 1000.0)
            NSAnimationContext.currentContext().setTimingFunction_(
                CAMediaTimingFunction.functionWithControlPoints____(0.2, 0.0, 0.0, 1.0)
            )
            self._panel.animator().setAlphaValue_(HUD_ALPHA)
            self._panel.animator().setFrameOrigin_((final_x, final_y))
            NSAnimationContext.endGrouping()

            def _open_done():
                self._opening = False
            threading.Timer(FADE_IN_MS / 1000.0 + 0.05, _open_done).start()

            self._start_timer()

        AppHelper.callAfter(_show_main)

    def hide(self, animated: bool = True) -> None:
        """Hide the HUD and stop the timer."""
        def _hide_main():
            if self._timer is not None:
                self._timer.invalidate()
                self._timer = None
                self._timer_target = None

            if self._panel is None:
                return
            if animated:
                NSAnimationContext.beginGrouping()
                NSAnimationContext.currentContext().setDuration_(FADE_OUT_MS / 1000.0)
                self._panel.animator().setAlphaValue_(0.0)
                NSAnimationContext.endGrouping()
                import threading
                threading.Timer(FADE_OUT_MS / 1000.0 + 0.05, _order_out).start()
            else:
                _order_out()

        def _order_out():
            def _do():
                if self._panel is not None:
                    self._panel.orderOut_(None)
            AppHelper.callAfter(_do)

        AppHelper.callAfter(_hide_main)

    def push_level(self, rms: float) -> None:
        """Thread-safe — callable from audio callback."""
        if self._waveform is not None:
            self._waveform.push_level(rms)

    # ------------------------------------------------------------------
    # Preview (Phase C)
    # ------------------------------------------------------------------

    def show_preview(self, text: str, processed: bool = False, on_done=None) -> None:
        """Show transcribed text in place of the waveform for PREVIEW_MS ms.

        Called from daemon thread; marshals to main thread via callAfter.
        - processed=False: raw Whisper text; starts 800ms timer.
        - processed=True: LLM output; cancels existing timer, restarts 800ms.
        on_done(cancelled: bool) is called when timer fires OR Esc is pressed.
        """
        def _main():
            if self._panel is None:
                return

            # Store latest callback
            if on_done is not None:
                self._preview_callback = on_done

            # Cancel any running preview timer
            self._cancel_preview_timer()

            # Swap waveform → preview view
            if self._waveform is not None:
                self._waveform.setHidden_(True)
            if self._preview_view is not None:
                self._preview_view.setHidden_(False)
                self._preview_view.set_text(text, processed=processed)

            # Register Esc monitor (idempotent — only registers once per session)
            self._register_esc_monitor()

            # Start one-shot NSTimer
            target = _PreviewTimerTarget.alloc().initWithHUD_(self)
            self._preview_timer_target = target
            self._preview_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                PREVIEW_MS / 1000.0,
                target,
                "fire:",
                None,
                False,
            )

        AppHelper.callAfter(_main)

    def _cancel_preview_timer(self) -> None:
        if self._preview_timer is not None:
            self._preview_timer.invalidate()
            self._preview_timer = None
            self._preview_timer_target = None

    def _on_preview_timer_fire(self) -> None:
        """Called from main thread when 800ms elapses without Esc."""
        self._cancel_preview_timer()
        self._unregister_esc_monitor()
        cb = self._preview_callback
        self._preview_callback = None
        if cb is not None:
            cb(cancelled=False)

    def _on_preview_cancelled(self) -> None:
        """Called from main thread when Esc is pressed during preview."""
        self._cancel_preview_timer()
        self._unregister_esc_monitor()
        cb = self._preview_callback
        self._preview_callback = None
        logger.info("[preview] Cancelled by Esc")
        if cb is not None:
            cb(cancelled=True)

    def _register_esc_monitor(self) -> None:
        if self._esc_monitor is not None:
            return  # already registered
        hud_ref = self

        def _key_handler(event):
            if event.keyCode() == 53:  # Esc
                AppHelper.callAfter(hud_ref._on_preview_cancelled)
                return None  # consume event
            return event

        # Use global monitor because the app is an accessory (NSApp not active)
        self._esc_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            _NSEventMaskKeyDown,
            _key_handler,
        )

    def _unregister_esc_monitor(self) -> None:
        if self._esc_monitor is not None:
            NSEvent.removeMonitor_(self._esc_monitor)
            self._esc_monitor = None

    def _reset_preview_state(self) -> None:
        """Reset preview subviews to initial recording state. Called at show() start."""
        self._cancel_preview_timer()
        self._unregister_esc_monitor()
        self._preview_callback = None
        if self._preview_view is not None:
            self._preview_view.setHidden_(True)
        if self._waveform is not None:
            self._waveform.setHidden_(False)


class _PreviewTimerTarget(NSObject):
    """ObjC-compatible one-shot NSTimer target for the 800ms preview countdown."""

    def initWithHUD_(self, hud):
        self = objc.super(_PreviewTimerTarget, self).init()
        if self is None:
            return None
        self._hud_ref = hud
        return self

    def fire_(self, timer):
        hud = self._hud_ref
        if hud is not None:
            hud._on_preview_timer_fire()


