import sys
import os
import json
import feedparser
import webbrowser
import calendar
import ctypes
import socket
from PySide6.QtCore import (Qt,
                            Signal,
                            QTimer,
                            QThread)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication,
                               QWidget,
                               QLabel,
                               QScrollArea,
                               QMainWindow,
                               QPushButton,
                               QMessageBox,
                               QHBoxLayout,
                               QFrame,
                               QVBoxLayout)
from concurrent.futures import (ThreadPoolExecutor,
                                as_completed)
from datetime import datetime
from ctypes import wintypes

APP_ID = 'AG95.pyAggregatedRssFeedReader'
ICON_FILE = 'icon.ico'

# Constants for taskbar flashing
FLASHW_STOP = 0x0
FLASHW_CAPTION = 0x1
FLASHW_TRAY = 0x2
FLASHW_ALL = 0x3

# feedparser applies no timeout of its own, so a single unresponsive server can
# block a whole refresh for minutes and leave the window looking frozen. Feed
# failures are caught per feed, so timing one out only skips that feed.
FEED_TIMEOUT = 20
socket.setdefaulttimeout(FEED_TIMEOUT)

class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD)]

# Load user32.dll for the FlashWindowEx function
user32 = ctypes.windll.user32

def flash_taskbar_icon(window_handle, flashing):
    """Flashes the taskbar icon when there are unread entries.

    ``flashing=False`` sends FLASHW_STOP so a previously started flash ends.
    """
    # Note: FLASHW_TIMER/FLASHW_TIMERNOFG make the icon flash forever until an
    # explicit FLASHW_STOP, so they are deliberately not used here.
    fInfo = FLASHWINFO(
        cbSize=ctypes.sizeof(FLASHWINFO),
        hwnd=window_handle,
        dwFlags=FLASHW_ALL if flashing else FLASHW_STOP,
        uCount=3 if flashing else 0,  # Number of flashes
        dwTimeout=0
    )
    user32.FlashWindowEx(ctypes.byref(fInfo))

def get_entry_date_parsed(entry):
    """Return a parsed time struct for an entry, falling back to updated/created dates."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, attr, None)
        if value is not None:
            return value
    # FeedParserDict also supports dict-style access.
    getter = getattr(entry, "get", None)
    if callable(getter):
        for key in ("published_parsed", "updated_parsed", "created_parsed"):
            value = getter(key)
            if value is not None:
                return value
    return None

class FeedFetcher(QThread):
    finished = Signal(list)  # Signal to emit the fetched entries once the thread finishes

    def __init__(self, feeds):
        super().__init__()
        self.feeds = feeds

    def run(self):
        entries = []

        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(self.fetch_feed, url): name for name, url in self.feeds.items()}
            for future in as_completed(futures):
                feed_name = futures[future]
                try:
                    feed = future.result()
                    for entry in feed.entries:
                        entries.append((feed_name, entry))
                except Exception as e:
                    print(f"Failed to fetch {feed_name}: {e}")

        def _sort_key(item):
            parsed = get_entry_date_parsed(item[1])
            if parsed is None:
                return 0
            try:
                return calendar.timegm(parsed)
            except (TypeError, ValueError, OverflowError):
                return 0

        entries.sort(key=_sort_key, reverse=True)
        self.finished.emit(entries)  # Emit the signal with fetched entries

    def fetch_feed(self, url):
        return feedparser.parse(url)

def get_running_path(relative_path):
    """Return a bundled file's path in both source and PyInstaller builds."""
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def set_windows_app_id():
    """Give the app its own Windows taskbar identity.

    When run as python.exe, Windows groups the window under Python's taskbar
    entry and uses Python's icon. An explicit AppUserModelID makes the taskbar
    use the window icon instead, matching the compiled executable.
    """
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass

def boostrap_rss_feeds():
    if not os.path.isfile('rss_feeds.json'):
        with open('rss_feeds.json', 'w') as file_out_handle:
            file_out_handle.write(json.dumps({"New On Steam": "https://store.steampowered.com/feeds/newreleases.xml"},
                                             indent=2))

class RSSReader(QMainWindow):
    def __init__(self):
        boostrap_rss_feeds()

        super().__init__()
        self.setWindowTitle("RSS Feed Reader V"+open(get_running_path('version.txt'), 'r').read())
        self.resize(1600, 600)
        self.setWindowIcon(QIcon(get_running_path(ICON_FILE)))

        # Set the file path to store viewed entries
        self.viewed_entries_file = "viewed_entries.json"
        self.viewed_entries = self.load_viewed_entries()  # Load viewed entries from file

        # Load feeds from JSON
        self.rss_feeds_file = 'rss_feeds.json'
        self.feeds = self.load_rss_feeds()

        # Setup main layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)

        # Add labels for total/ unread entries and last refresh datetime at the top (outside scroll area)
        self.last_refresh_label = QLabel("Last refresh done at: TBA")
        self.total_label = QLabel("Total Entries: 0")
        self.unread_label = QLabel("Unread Entries: 0")
        self.last_refresh_label.setAlignment(Qt.AlignCenter)
        self.total_label.setAlignment(Qt.AlignCenter)
        self.unread_label.setAlignment(Qt.AlignCenter)

        # Prepare the horizontal are for total_label and unread_label
        label_layout = QHBoxLayout()
        label_layout.addWidget(self.total_label)
        label_layout.addWidget(self.unread_label)

        # Add last_refresh_label and label_layout to the main layout
        self.main_layout.addWidget(self.last_refresh_label)
        self.main_layout.addLayout(label_layout)

        # Button layout for Refresh and Mark All as Read buttons
        button_layout = QHBoxLayout()

        # Add "Mark All as Read" button
        self.mark_all_button = QPushButton("Mark All as Read")
        self.mark_all_button.clicked.connect(self.mark_all_as_read)
        button_layout.addWidget(self.mark_all_button)

        # Add "Refresh Feeds" button
        self.refresh_button = QPushButton("Refresh Feeds")
        self.refresh_button.clicked.connect(self.refresh_feeds)
        button_layout.addWidget(self.refresh_button)

        # Add button layout to the main layout
        self.main_layout.addLayout(button_layout)

        # Scroll area for feed display
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.main_layout.addWidget(self.scroll_area)

        # Container widget within scroll area
        self.feed_container = QWidget()
        self.feed_layout = QVBoxLayout(self.feed_container)
        self.feed_layout.setSpacing(50)
        self.scroll_area.setWidget(self.feed_container)

        # Strong references to the entry widgets currently on screen, stored as
        # (container, title, toggle_button) tuples. They are released in
        # clear_feed_entries(); keeping them in one place is what makes teardown
        # deterministic instead of relying on deferred deletion.
        self.entry_widgets = []
        self.fetching = False
        self.flashing = False

        # Timer to simulate refresh the RSS feed entries
        self.timer1 = QTimer(self)
        self.timer1.timeout.connect(self.refresh_feeds)
        self.timer1.start(30*60*1000)  # Update every 30 minutes

        # Timer to flash the taskbar icon if there are unread entries
        self.unread_entries = 0
        self.timer2 = QTimer(self)
        self.timer2.timeout.connect(self.update_unread_entries)
        self.timer2.start(5000)  # Update every 5 seconds

        # Display the feeds
        self.refresh_feeds()

    def update_unread_entries(self):
        if self.unread_entries > 0:
            # Flash the taskbar icon if unread entries exist
            flash_taskbar_icon(self.winId(), flashing=True)
            self.flashing = True
        elif self.flashing:
            flash_taskbar_icon(self.winId(), flashing=False)
            self.flashing = False

    def load_rss_feeds(self):
        """Load RSS feed URLs from a JSON file."""
        try:
            with open(self.rss_feeds_file, "r") as file:
                return json.load(file)
        except FileNotFoundError:
            QMessageBox.warning(self, "Warning", f"File {self.rss_feeds_file} not found.")
            return {}
        except json.JSONDecodeError:
            QMessageBox.critical(self, "Error", f"Error reading {self.rss_feeds_file}.")
            return {}

    def load_viewed_entries(self):
        """Load viewed entries from a JSON file."""
        try:
            with open(self.viewed_entries_file, "r") as file:
                return set(json.load(file))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def save_viewed_entries(self):
        """Save viewed entries to a JSON file."""
        with open(self.viewed_entries_file, "w") as file:
            json.dump(list(self.viewed_entries), file, indent=2)

    def apply_entry_read_style(self, entry_container, read):
        """Update an entry's border color to reflect its read state."""
        border_color = "grey" if read else "blue"
        entry_container.setStyleSheet(f"""
            QFrame {{
                background-color: #f5f5f5;
                border: 2px solid {border_color};
                border-radius: 10px;
                padding: 10px;
            }}
        """)

    @staticmethod
    def configure_read_toggle(toggle_button, read):
        """Set a per-entry button's label and tooltip for the read state."""
        toggle_button.setText("Mark as Unread" if read else "Mark as Read")
        toggle_button.setToolTip(
            "Mark this entry as unread" if read
            else "Mark this entry as read without opening it")

    def update_unread_count(self):
        """Recount unread entries from the entries currently displayed."""
        self.unread_entries = sum(
            1 for _, title, _ in self.entry_widgets
            if title not in self.viewed_entries
        )
        self.unread_label.setText(f"Unread Entries: {self.unread_entries}")

    def set_entry_read(self, title, read, persist=True):
        """Set every displayed entry with this title to the given read state."""
        if read:
            self.viewed_entries.add(title)
        else:
            self.viewed_entries.discard(title)

        for container, entry_title, toggle_button in self.entry_widgets:
            if entry_title == title:
                self.apply_entry_read_style(container, read)
                self.configure_read_toggle(toggle_button, read)

        if persist:
            self.save_viewed_entries()
            self.update_unread_count()

    def toggle_entry_read(self, title):
        """Toggle a displayed entry between read and unread."""
        self.set_entry_read(title, title not in self.viewed_entries)

    def fetch_feed(self, url):
        """Fetch and parse a single RSS feed."""
        return feedparser.parse(url)

    def update_feed_entries(self, entries):
        """Update the feed entries in the GUI."""
        self.fetching = False

        # Clear existing feed entries
        self.clear_feed_entries()

        # Count unread entries and update the labels
        total_entries = len(entries)
        self.unread_entries = sum(1 for _, entry in entries if entry.get("title") not in self.viewed_entries)

        self.last_refresh_label.setText(f"Last refresh done at: {datetime.now().strftime("%d-%b-%Y %H:%M:%S")}")
        self.total_label.setText(f"Total Entries: {total_entries}")
        self.unread_label.setText(f"Unread Entries: {self.unread_entries}")

        # Display each entry in the GUI. Widgets that need updating later are
        # tracked in self.entry_widgets; everything else stays local.
        for feed_name, entry in entries:
            is_read = entry.title in self.viewed_entries
            container = QFrame()
            self.apply_entry_read_style(container, is_read)
            container_layout = QVBoxLayout(container)
            container_layout.setSpacing(1)

            entry_widget = QLabel(f"<b>{feed_name}</b>: <a href='{entry.link}'>{entry.title}</a>")
            entry_widget.setTextFormat(Qt.RichText)
            entry_widget.setTextInteractionFlags(Qt.TextBrowserInteraction)
            entry_widget.setOpenExternalLinks(False)

            # Only strings are captured here, so the lambda cannot keep the
            # entry widgets alive after a refresh destroys them.
            entry_widget.linkActivated.connect(
                lambda _,
                       title=entry.title,
                       link=entry.link:
                self.on_entry_click(title, link))

            toggle_button = QPushButton()
            self.configure_read_toggle(toggle_button, is_read)
            toggle_button.setMinimumWidth(130)
            toggle_button.clicked.connect(
                lambda _=False, title=entry.title:
                self.toggle_entry_read(title))

            title_layout = QHBoxLayout()
            title_layout.addWidget(entry_widget, 1)
            title_layout.addWidget(toggle_button)

            preview_text = entry.get("summary") or entry.get("description") or "No preview available."
            preview_widget = QLabel(preview_text[:200] + '<br>...')
            preview_widget.setWordWrap(True)
            preview_widget.setTextFormat(Qt.RichText)
            preview_widget.setTextInteractionFlags(Qt.TextBrowserInteraction)
            preview_widget.setOpenExternalLinks(False)
            preview_widget.setStyleSheet("font-size: 10pt; color: gray;")

            parsed_date = get_entry_date_parsed(entry)
            try:
                entry_date = datetime(*parsed_date[:6]).strftime("%Y-%m-%d %H:%M:%S") if parsed_date is not None else "Unknown date"
            except (TypeError, ValueError):
                entry_date = "Unknown date"
            date_widget = QLabel(f"Date: {entry_date}")
            date_widget.setStyleSheet("font-size: 9pt; color: #666;")

            container_layout.addLayout(title_layout)
            container_layout.addWidget(date_widget)
            container_layout.addWidget(preview_widget)
            self.feed_layout.addWidget(container)
            self.entry_widgets.append((container, entry.title, toggle_button))

        # Re-enable buttons after feed is refreshed
        self.mark_all_button.setDisabled(False)
        self.refresh_button.setDisabled(False)

    def clear_feed_entries(self):
        """Detach and destroy every entry widget currently shown."""
        # Release the Python references first, otherwise a wrapper outlives the
        # C++ object and PySide6 later frees memory Qt already released.
        self.entry_widgets.clear()
        while self.feed_layout.count():
            widget = self.feed_layout.takeAt(0).widget()
            if widget is not None:
                # setParent(None) gives ownership back to Python, so dropping the
                # last reference destroys the widget. Calling deleteLater() as
                # well would let Qt and PySide6 free the same object, which
                # crashes with an access violation on the next refresh.
                widget.setParent(None)

    def refresh_feeds(self):
        """Refresh and display all feeds, sorted by date."""
        # The 30 minute timer can fire while a fetch is still running; starting
        # a second QThread would drop the first one while it is still alive.
        if self.fetching:
            return
        self.fetching = True
        self.mark_all_button.setDisabled(True)
        self.refresh_button.setDisabled(True)

        # Start the FeedFetcher thread. The previous FeedFetcher is only
        # replaced here, once the guard above confirms its thread has finished.
        self.fetcher_thread = FeedFetcher(self.feeds)
        self.fetcher_thread.finished.connect(self.update_feed_entries)
        self.fetcher_thread.start()

    def on_entry_click(self, title, entry_link):
        """Open an entry and mark it as read."""
        webbrowser.open(entry_link)
        if title not in self.viewed_entries:
            self.set_entry_read(title, True)

    def mark_all_as_read(self):
        """Mark all displayed entries as read and update unread count."""
        for container, title, toggle_button in self.entry_widgets:
            self.viewed_entries.add(title)
            self.apply_entry_read_style(container, True)
            self.configure_read_toggle(toggle_button, True)

        # Save viewed entries and update unread count
        self.save_viewed_entries()
        self.update_unread_count()

if __name__ == "__main__":
    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(get_running_path(ICON_FILE)))
    reader = RSSReader()
    reader.show()
    sys.exit(app.exec())