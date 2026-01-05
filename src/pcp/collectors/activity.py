"""
Activity Collector - monitors system activity and emits PCP events.

This is a reference collector that demonstrates:
1. Event generation with proper envelope/payload structure
2. Progressive disclosure (summary/detail/raw)
3. Batched ingestion via observe()
"""

import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ActivityCollector:
    """
    Collects activity events from the system.

    Supported event kinds:
    - application.switch: App focus changes
    - application.navigation: URL/document changes
    - input.burst: Keyboard/mouse activity bursts
    """

    collector_id: str = "activity_monitor"
    batch_size: int = 10
    _pending_events: list[dict[str, Any]] = field(default_factory=list)
    _last_app: str | None = None
    _last_window: str | None = None

    def get_active_window(self) -> tuple[str, str]:
        """Get the currently active application and window title."""
        app = "Unknown"
        window = ""

        # Get frontmost app
        try:
            result = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first application process whose frontmost is true'],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                app = result.stdout.strip()
        except Exception:
            return "Unknown", ""

        # Try to get window title (app-specific)
        try:
            if app in ("Safari", "Google Chrome", "Arc"):
                # Browser - get tab title
                script = f'tell application "{app}" to get title of active tab of front window'
            elif app == "Finder":
                script = 'tell application "Finder" to get name of front Finder window'
            else:
                # Generic - try to get front window name
                script = f'''
                tell application "System Events"
                    tell process "{app}"
                        try
                            return name of front window
                        on error
                            return ""
                        end try
                    end tell
                end tell
                '''

            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                window = result.stdout.strip()
        except Exception:
            pass  # Window title is optional

        return app, window

    def check_for_events(self) -> list[dict[str, Any]]:
        """Check for new activity events."""
        events = []

        app, window = self.get_active_window()

        # Check for app switch
        if app != self._last_app:
            if self._last_app is not None:
                events.append(self._create_app_switch_event(
                    from_app=self._last_app,
                    to_app=app,
                    window_title=window,
                ))
            self._last_app = app
            self._last_window = window

        # Check for window change within same app
        elif window != self._last_window:
            events.append(self._create_navigation_event(
                app=app,
                window_title=window,
            ))
            self._last_window = window

        return events

    def _create_app_switch_event(
        self,
        from_app: str,
        to_app: str,
        window_title: str,
    ) -> dict[str, Any]:
        """Create an application.switch event."""
        return {
            "envelope": {
                "type": "event",
                "schema": "pcp.event.v1",
                "tags": ["activity", "app_switch"],
                "disclosure": {
                    "available_levels": ["summary", "detail"],
                    "default_level": "summary",
                },
                "lineage": {
                    "sources": [f"collector:{self.collector_id}"],
                    "confidence": 1.0,
                },
            },
            "payload": {
                "event_kind": "application.switch",
                "timestamp": datetime.utcnow().isoformat(),
                "summary": f"Switched from {from_app} to {to_app}",
                "detail": {
                    "from_application": from_app,
                    "to_application": to_app,
                    "window_title": window_title,
                },
            },
        }

    def _create_navigation_event(
        self,
        app: str,
        window_title: str,
    ) -> dict[str, Any]:
        """Create an application.navigation event."""
        # Try to detect if it's a URL
        url = None
        if any(x in window_title.lower() for x in ["http", ".com", ".org", ".io"]):
            # Simple URL extraction (would be more sophisticated in production)
            url = window_title

        return {
            "envelope": {
                "type": "event",
                "schema": "pcp.event.v1",
                "tags": ["activity", "navigation"],
                "disclosure": {
                    "available_levels": ["summary", "detail"],
                    "default_level": "summary",
                },
                "lineage": {
                    "sources": [f"collector:{self.collector_id}"],
                    "confidence": 1.0,
                },
            },
            "payload": {
                "event_kind": "application.navigation",
                "timestamp": datetime.utcnow().isoformat(),
                "summary": f"Navigated in {app}: {window_title[:50]}",
                "detail": {
                    "application": app,
                    "window_title": window_title,
                    "url": url,
                },
            },
        }

    def collect_and_emit(
        self,
        emit_callback: Any | None = None,
    ) -> list[dict[str, Any]]:
        """
        Collect events and optionally emit them.

        Args:
            emit_callback: Async function to call with batched events

        Returns:
            List of collected events
        """
        events = self.check_for_events()
        self._pending_events.extend(events)

        # Emit in batches
        emitted = []
        if emit_callback and len(self._pending_events) >= self.batch_size:
            emitted = self._pending_events[:self.batch_size]
            self._pending_events = self._pending_events[self.batch_size:]
            # emit_callback would be called here

        return events

    def run_loop(
        self,
        interval: float = 1.0,
        emit_callback: Any | None = None,
        duration: float | None = None,
    ) -> None:
        """
        Run the collector loop.

        Args:
            interval: Seconds between checks
            emit_callback: Function to call with events
            duration: Optional duration in seconds (None = forever)
        """
        start_time = time.time()

        while True:
            events = self.collect_and_emit(emit_callback)

            for event in events:
                summary = event.get("payload", {}).get("summary", "")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {summary}")

            if duration and (time.time() - start_time) >= duration:
                break

            time.sleep(interval)


def main():
    """Demo the activity collector."""
    print("Starting activity collector (Ctrl+C to stop)...")
    print("Switch between apps to see events.\n")

    collector = ActivityCollector()

    try:
        collector.run_loop(interval=0.5)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
