from customtkinter import (CTk,
                           CTkLabel,
                           CTkButton,
                           CTkFrame,
                           CTkConfirmationDialog,
                           LEFT, BOTH,
                           X, W,
                           CTkScrollableFrame)
from tkinter import PhotoImage
import feedparser
from datetime import datetime
import webbrowser
import json
import os
from plyer import notification
from typing import List
from concurrent.futures import (ThreadPoolExecutor,
                                as_completed)

def boostrap_rss_feeds():
    if not os.path.isfile('rss_feeds.json'):
        with open('rss_feeds.json', 'w') as file_out_handle:
            file_out_handle.write(json.dumps({"New On Steam": "https://store.steampowered.com/feeds/newreleases.xml"},
                                             indent=2))

def fetch_feed(feed_name, url):
    """Fetch and parse an individual RSS feed."""
    to_return = []
    try:
        print(f'{feed_name}: Fetching ...')
        feed = feedparser.parse(url)
        if feed.bozo:
            raise ValueError("Feed could not be parsed")

        for entry in feed.entries:
            # Standardizing the date format
            if 'published_parsed' in entry:
                entry_date = datetime(*entry.published_parsed[:6])
            else:
                entry_date = datetime.now()  # Fallback if no date is found

            # Adding feed name to each entry
            entry_data = {
                'title': entry.title,
                'link': entry.link,
                'summary': entry.summary if 'summary' in entry else '',
                'published': entry_date,
                'feed_name': feed_name
            }
            to_return.append(entry_data)

    except Exception as e:
        print(f"Error fetching {feed_name}: {e}")

    print(f'{feed_name}: Fetched {len(to_return)} entries.')
    return to_return

class RSSFeedReader:
    """A simple RSS feed reader using Tkinter for GUI."""

    def __init__(self):
        """Initialize the RSS feed reader."""
        self.viewed_entries = set()
        self.cached_entries: List[datetime | List] = [0, []]
        self.rss_feeds = {}
        self.viewed_entries_file = "viewed_entries.json"
        self.rss_feeds_file = "rss_feeds.json"
        self.root = CTk()
        self.root.title("Aggregated RSS Feed Reader")
        self.root.geometry("900x600")
        icon = PhotoImage(file="icon.png")
        self.root.iconphoto(True, icon)

        # Load viewed entries and RSS feeds from files
        self.load_viewed_entries()
        self.load_rss_feeds()

        # Create a frame to hold the buttons and the counter
        self.button_frame = CTkFrame(self.root)
        self.button_frame.pack(pady=10)

        # Create a label to display unread entries count
        self.unread_count_label = CTkLabel(self.button_frame, text=f"Unread Entries: {self.get_unread_count()}", font=("Helvetica", 12))
        self.unread_count_label.pack(side=LEFT, padx=10)

        # Create a label to display all entries count
        self.all_count_label = CTkLabel(self.button_frame, text=f"All Entries: {self.get_all_count()}", font=("Helvetica", 12))
        self.all_count_label.pack(side=LEFT, padx=10)

        # Create Mark All as Read button
        self.mark_all_read_button = CTkButton(self.button_frame, text="Mark All as Read", command=self.mark_all_as_read)
        self.mark_all_read_button.pack(side=LEFT, padx=5)

        # Create Refresh button
        self.refresh_button = CTkButton(self.button_frame, text="Refresh", command=self.refresh_entries)
        self.refresh_button.pack(side=LEFT, padx=5)

        # Add a separator between button frame and content frame
        self.separator = CTkFrame(self.root, height=2, bg_color="gray")
        self.separator.pack(fill=X, padx=5, pady=5)

        # Initial fetch and display of RSS entries
        self.check_and_display_new_entries()

        # Save viewed entries on close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.scroll_update_pending = False  # Variable to control update frequency during dragging

    def load_viewed_entries(self):
        """Load viewed entries from a JSON file."""
        if os.path.exists(self.viewed_entries_file):
            with open(self.viewed_entries_file, 'r') as file:
                self.viewed_entries = set(json.load(file))

    def save_viewed_entries(self):
        """Save viewed entries to a JSON file."""
        with open(self.viewed_entries_file, 'w') as file:
            json.dump(list(self.viewed_entries), file)

    def load_rss_feeds(self):
        """Load RSS feeds from a JSON file."""
        if os.path.exists(self.rss_feeds_file):
            with open(self.rss_feeds_file, 'r') as file:
                self.rss_feeds = json.load(file)
        else:
            print(f"Warning: {self.rss_feeds_file} not found. Using empty feed list.")

    def fetch_all_feeds(self):
        """Fetch and parse all RSS feeds."""
        # BUT first check if the cache can be used
        CACHE_REFRESH_s = 10
        now = datetime.now()
        cache_age_s = (now - self.cached_entries[0]).seconds if isinstance(self.cached_entries[0], datetime) else 99999999
        if cache_age_s > 10:
            all_entries = []
            with ThreadPoolExecutor(max_workers=4) as executor:
                future_to_feed = [executor.submit(fetch_feed, feed_name, url,) for feed_name, url in self.rss_feeds.items()]
                for future in as_completed(future_to_feed):
                    all_entries += future.result()
            print(f'Fetched all RSS entries in {(datetime.now() - now).seconds} seconds.')

            # Sort entries by date, most recent first
            all_entries.sort(key=lambda e: e['published'], reverse=True)

            # and add them to the cache and return them afterwards
            self.cached_entries[0], self.cached_entries[1] = datetime.now(), all_entries
            return all_entries
        else:
            print(f'Cache hit {cache_age_s}s < {CACHE_REFRESH_s}s, returning entries from cache')
            return self.cached_entries[1]

    def open_entry(self, link, title, title_label):
        """Open the entry link and mark as viewed."""
        self.viewed_entries.add(title)

        # Change the color of the title label to gray
        title_label.configure(text_color="gray")

        webbrowser.open(link)
        self.update_unread_count()

    def check_new_entries(self, entries):
        """Check for new entries that have not been viewed."""
        new_entries = [entry for entry in entries if entry['title'] not in self.viewed_entries]
        return new_entries

    def display_entries(self, entries):
        """Display the fetched RSS entries in a scrollable frame."""
        # Clear the window
        for widget in self.root.winfo_children():
            if widget is not self.button_frame and widget is not self.separator:  # Keep the button frame and separator
                widget.destroy()

        # Adding a scrollable frame to hold all the entries
        scroll_frame = CTkScrollableFrame(self.root,
                                          bg_color='transparent',
                                          fg_color='transparent')
        scroll_frame.pack(fill=BOTH, expand=True)

        # Displaying all entries in the scrollable frame
        for entry in entries:
            entry_frame = CTkFrame(scroll_frame)
            entry_frame.pack(fill=X, padx=10, pady=10)

            # Change color if the entry has been viewed
            title_color = "gray" if entry['title'] in self.viewed_entries else "blue"

            title_label = CTkLabel(entry_frame, text=entry['title'], font=("Helvetica", 14, "bold"), text_color=title_color,
                                   cursor="hand2")
            title_label.pack(anchor=W)
            title_label.bind("<Button-1>",
                             lambda e, url=entry['link'], title=entry['title'], label=title_label: self.open_entry(url,
                                                                                                                   title,
                                                                                                                   label))

            date_label = CTkLabel(entry_frame,
                                  text=f"Published on {entry['published'].strftime('%Y-%m-%d %H:%M')} - {entry['feed_name']}",
                                  font=("Helvetica", 10), text_color="gray")
            date_label.pack(anchor=W)

            # Display only the first 150 characters of the summary, with a "Read more" link
            summary_text = entry['summary'][:150] + "..." if len(entry['summary']) > 150 else entry['summary']
            summary_label = CTkLabel(entry_frame, text=summary_text, wraplength=800, justify="left",
                                     font=("Helvetica", 12))
            summary_label.pack(anchor=W)

            if len(entry['summary']) > 150:
                read_more_label = CTkLabel(entry_frame, text="Read more", text_color="blue", cursor="hand2")
                read_more_label.pack(anchor=W)
                read_more_label.bind("<Button-1>", lambda e, url=entry['link']: webbrowser.open(url))

    def on_scroll(self, *args):
        """Throttle updates during scrollbar drag."""
        if not self.scroll_update_pending:
            self.scroll_update_pending = True
            self.root.after(10, self._update_scroll, *args)

    def _update_scroll(self, *args):
        """Actual scroll update, delayed to reduce flickering."""
        self.canvas.yview(*args)
        self.scroll_update_pending = False  # Reset flag for next scroll

    def mark_all_as_read(self):
        """Mark all entries as read."""
        for entry in self.fetch_all_feeds():
            self.viewed_entries.add(entry['title'])

        # Refresh the display to update colors
        self.check_and_display_new_entries()
        self.update_unread_count()
        self.update_all_count()

    def refresh_entries(self):
        """Refresh the displayed RSS entries manually."""
        self.check_and_display_new_entries()

    def update_unread_count(self):
        """Update the unread entries count label."""
        self.unread_count_label.configure(text=f"Unread Entries: {self.get_unread_count()}")

    def update_all_count(self):
        """Update the unread entries count label."""
        self.all_count_label.configure(text=f"All Entries: {self.get_all_count()}")

    def get_unread_count(self):
        """Get the count of unread entries."""
        return len([entry for entry in self.fetch_all_feeds() if entry['title'] not in self.viewed_entries])

    def get_all_count(self):
        """Get the count of unread entries."""
        return len([entry for entry in self.fetch_all_feeds()])

    def send_notification(self, new_entries):
        """Send a notification for new entries."""
        if new_entries:
            # Gather the titles
            titles = [entry['title'] for entry in new_entries]

            # Limit to 3 titles and create a message
            limited_titles = titles[:3]  # Take the first 3 entries
            message = "\n".join(limited_titles)

            # If there are more than 3 entries, indicate that there are more
            if len(titles) > 3:
                message += "\n...and more"

            # Ensure the message does not exceed 256 characters
            if len(message) > 256:
                message = message[:253] + "..."  # Allow space for ellipsis

            notification.notify(
                title="New RSS Entries Available",
                message=f"You have new unread entries:\n{message}",
                app_name="RSS Feed Reader"
            )

    def check_and_display_new_entries(self):
        """Check for new entries and update the display."""
        all_entries = self.fetch_all_feeds()
        new_entries = self.check_new_entries(all_entries)
        self.send_notification(new_entries)
        self.display_entries(all_entries)
        self.update_unread_count()
        self.update_all_count()

        # Schedule the next check in 5 minutes (300000 milliseconds)
        self.root.after(300000, self.check_and_display_new_entries)

    def on_closing(self):
        """Confirm exit and save viewed entries."""
        if CTkConfirmationDialog(title="Quit", message="Do you really want to quit?").get_selection():
            self.save_viewed_entries()
            self.root.destroy()

    def run(self):
        """Run the application."""
        self.root.mainloop()

if __name__ == "__main__":
    boostrap_rss_feeds()
    app = RSSFeedReader()
    app.run()
