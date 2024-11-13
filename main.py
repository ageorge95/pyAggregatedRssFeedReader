import sys
import os
import json
import feedparser
import webbrowser
import re
import ctypes
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

# Constants for taskbar flashing
FLASHW_TRAY = 0x2
FLASHW_TIMERNOFG = 0xC

class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD)]

# Load user32.dll for the FlashWindowEx function
user32 = ctypes.windll.user32

def flash_taskbar_icon(window_handle):
    """Flashes the taskbar icon when there are unread entries."""
    fInfo = FLASHWINFO(
        cbSize=ctypes.sizeof(FLASHWINFO),
        hwnd=window_handle,
        dwFlags=FLASHW_TRAY | FLASHW_TIMERNOFG,
        uCount=3,  # Number of flashes
        dwTimeout=0
    )
    user32.FlashWindowEx(ctypes.byref(fInfo))

def extract_title(full_text):
    """Extract the title text from an HTML link within the given text."""
    match = re.search(r"<a[^>]*>(.*?)</a>", full_text)
    return match.group(1) if match else None

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

        entries.sort(key=lambda x: x[1].published_parsed, reverse=True)
        self.finished.emit(entries)  # Emit the signal with fetched entries

    def fetch_feed(self, url):
        return feedparser.parse(url)

def get_running_path(relative_path):
    if '_internal' in os.listdir():
        return os.path.join('_internal', relative_path)
    else:
        return relative_path

class RSSReader(QMainWindow):
    new_entries_signal = Signal(list)  # Signal to pass the fetched entries to the main thread

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RSS Feed Reader V"+open(get_running_path('version.txt'), 'r').read())
        self.resize(1200, 600)
        self.setWindowIcon(QIcon(get_running_path('icon.ico')))

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

        # Add labels for total and unread entries at the top (outside scroll area)
        self.total_label = QLabel("Total Entries: 0")
        self.unread_label = QLabel("Unread Entries: 0")

        # Set label alignment and add them in a horizontal layout
        self.total_label.setAlignment(Qt.AlignCenter)
        self.unread_label.setAlignment(Qt.AlignCenter)
        label_layout = QHBoxLayout()
        label_layout.addWidget(self.total_label)
        label_layout.addWidget(self.unread_label)

        # Add label layout to the main layout
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

        # Timer to simulate refresh the RSS feed entries
        self.timer1 = QTimer(self)
        self.timer1.timeout.connect(self.refresh_feeds)
        self.timer1.start(30*60*1000)  # Update every 30 minutes

        # Timer to flash the taskbar icon if there are unread entries
        self.unread_entries = 0
        self.timer2 = QTimer(self)
        self.timer2.timeout.connect(self.update_unread_entries)
        self.timer2.start(5000)  # Update every 5 seconds

        # Connect the signal to a method that will update the GUI
        self.new_entries_signal.connect(self.update_feed_entries)

        # Display the feeds
        self.refresh_feeds()

    def update_unread_entries(self):
        if self.unread_entries > 0:
            flash_taskbar_icon(self.winId())  # Flash the taskbar icon if unread entries exist

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

    def fetch_feed(self, url):
        """Fetch and parse a single RSS feed."""
        return feedparser.parse(url)

    def update_feed_entries(self, entries):
        """Update the feed entries in the GUI."""
        # Clear existing feed entries
        for i in reversed(range(self.feed_layout.count())):
            widget = self.feed_layout.itemAt(i).widget()
            if widget is not None:
                widget.deleteLater()

        # Count unread entries and update the labels
        total_entries = len(entries)
        self.unread_entries = sum(1 for _, entry in entries if entry.get("title") not in self.viewed_entries)

        self.total_label.setText(f"Total Entries: {total_entries}")
        self.unread_label.setText(f"Unread Entries: {self.unread_entries}")

        # Display each entry in the GUI
        for feed_name, entry in entries:
            border_color = "grey" if entry.title in self.viewed_entries else "blue"
            self.entry_container = QFrame()
            self.entry_container.setStyleSheet(f"""
                        QFrame {{
                            background-color: #f5f5f5;
                            border: 2px solid {border_color};
                            border-radius: 10px;
                            padding: 10px;
                        }}
                    """)
            self.container_layout = QVBoxLayout(self.entry_container)
            self.container_layout.setSpacing(1)

            self.entry_widget = QLabel(f"<b>{feed_name}</b>: <a href='{entry.link}'>{entry.title}</a>")
            self.entry_widget.setTextFormat(Qt.RichText)
            self.entry_widget.setTextInteractionFlags(Qt.TextBrowserInteraction)
            self.entry_widget.setOpenExternalLinks(False)

            self.entry_widget.linkActivated.connect(
                lambda _, title=entry.title, container=self.entry_container: self.on_entry_click(title, container, entry.link))

            self.preview_text = entry.get("summary") or entry.get("description") or "No preview available."
            self.preview_widget = QLabel(self.preview_text[:200] + '<br>...')
            self.preview_widget.setWordWrap(True)
            self.preview_widget.setTextFormat(Qt.RichText)
            self.preview_widget.setTextInteractionFlags(Qt.TextBrowserInteraction)
            self.preview_widget.setOpenExternalLinks(False)
            self.preview_widget.setStyleSheet("font-size: 10pt; color: gray;")

            self.entry_date = datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d %H:%M:%S")
            self.date_widget = QLabel(f"Date: {self.entry_date}")
            self.date_widget.setStyleSheet("font-size: 9pt; color: #666;")

            self.container_layout.addWidget(self.entry_widget)
            self.container_layout.addWidget(self.date_widget)
            self.container_layout.addWidget(self.preview_widget)
            self.feed_layout.addWidget(self.entry_container)

        # Re-enable buttons after feed is refreshed
        self.mark_all_button.setDisabled(False)
        self.refresh_button.setDisabled(False)

    def refresh_feeds(self):
        """Refresh and display all feeds, sorted by date."""
        self.mark_all_button.setDisabled(True)
        self.refresh_button.setDisabled(True)

        # Start the FeedFetcher thread
        self.fetcher_thread = FeedFetcher(self.feeds)
        self.fetcher_thread.finished.connect(self.new_entries_signal.emit)  # Connect the signal
        self.fetcher_thread.start()

    def on_entry_click(self, title, entry_container, entry_link):
        """Handle link click to mark the entry as read and update border color."""
        webbrowser.open(entry_link)
        if title not in self.viewed_entries:
            self.viewed_entries.add(title)
            # Change the border color of the clicked entry container to grey
            entry_container.setStyleSheet("""
                QFrame {
                    background-color: #f5f5f5;
                    border: 2px solid grey;
                    border-radius: 10px;
                    padding: 10px;
                }
            """)
            # Save viewed entries after marking this entry as read
            self.save_viewed_entries()

            self.unread_entries -= 1
            self.unread_label.setText(f"Unread Entries: {self.unread_entries}")

    def mark_all_as_read(self):
        """Mark all displayed entries as read and update unread count."""
        for i in range(self.feed_layout.count()):
            frame = self.feed_layout.itemAt(i).widget()
            if isinstance(frame, QFrame):
                # Access the layout of each frame to find the title label
                container_layout = frame.layout()
                title_label = container_layout.itemAt(0).widget()
                if isinstance(title_label, QLabel):
                    # Extract the title from the label's HTML content
                    title_text = extract_title(title_label.text())
                    self.viewed_entries.add(title_text)

                    # Change the border color of the frame to grey
                    frame.setStyleSheet("""
                        QFrame {
                            background-color: #f5f5f5;
                            border: 2px solid grey;
                            border-radius: 10px;
                            padding: 10px;
                        }
                    """)

        # Save viewed entries and update unread count
        self.save_viewed_entries()
        self.unread_entries = 0
        self.unread_label.setText(f"Unread Entries: {self.unread_entries}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    reader = RSSReader()
    reader.show()
    sys.exit(app.exec())