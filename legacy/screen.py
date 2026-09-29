import ctypes
ctypes.windll.shcore.SetProcessDpiAwareness(2)   # <<< FIX LEFT SHIFTING

import tkinter as tk
from PIL import ImageGrab,  ImageTk

class ScreenshotPopup:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Screenshot Tool")
        self.root.geometry("200x120")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        tk.Button(self.root, text="Take Screenshot", command=self.start_selection)\
            .pack(expand=True, fill=tk.BOTH, padx=20, pady=10)

        self.coord_label = tk.Label(self.root, text="Selected Coordinates: None")
        self.coord_label.pack(pady=5)

        self.root.mainloop()

    def on_close(self):
        for win in ["selection_window", "preview_window"]:
            if hasattr(self, win):
                w = getattr(self, win)
                if w.winfo_exists():
                    w.destroy()
        self.root.destroy()

    def start_selection(self):
        self.root.withdraw()

        self.selection_window = tk.Toplevel()
        self.selection_window.attributes('-fullscreen', True)
        self.selection_window.attributes('-alpha', 0.3)
        self.selection_window.config(cursor="cross")
        self.selection_window.protocol("WM_DELETE_WINDOW", self.on_close)

        self.canvas = tk.Canvas(self.selection_window, bg="black")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.start_x = self.start_y = None
        self.rect = None

        self.canvas.bind("<ButtonPress-1>", self.start_draw)
        self.canvas.bind("<B1-Motion>", self.update_draw)
        self.canvas.bind("<ButtonRelease-1>", self.finish_draw)

    def start_draw(self, event):
        self.start_x = self.selection_window.winfo_pointerx()
        self.start_y = self.selection_window.winfo_pointery()

        self.rect = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline='red', width=5
        )

    def update_draw(self, event):
        sx, sy, _, _ = self.canvas.coords(self.rect)
        self.canvas.coords(self.rect, sx, sy, event.x, event.y)

    def finish_draw(self, event):
        end_x = self.selection_window.winfo_pointerx()
        end_y = self.selection_window.winfo_pointery()

        self.x1, self.y1 = min(self.start_x, end_x), min(self.start_y, end_y)
        self.x2, self.y2 = max(self.start_x, end_x), max(self.start_y, end_y)

        self.coord_label.config(text=f"({self.x1},{self.y1}) -> ({self.x2},{self.y2})")

        self.img = ImageGrab.grab(bbox=(self.x1, self.y1, self.x2, self.y2))

        self.selection_window.destroy()

        self.show_preview()

    def show_preview(self):
        self.preview_window = tk.Toplevel()
        self.preview_window.title("Preview Screenshot")
        self.preview_window.protocol("WM_DELETE_WINDOW", self.on_close)

        self.tk_img = ImageTk.PhotoImage(self.img)

        tk.Label(self.preview_window, image=self.tk_img).pack()
        tk.Label(self.preview_window, text=f"Coordinates: ({self.x1},{self.y1}) → ({self.x2},{self.y2})")\
            .pack(pady=5)

        frame = tk.Frame(self.preview_window)
        frame.pack(pady=10)

        tk.Button(frame, text="Save", command=self.save).pack(side=tk.LEFT, padx=10)
        tk.Button(frame, text="Retake", command=self.retake).pack(side=tk.RIGHT, padx=10)

    def save(self):
        self.img.save("screenshot_region.png")
        print("Saved as screenshot_region.png")
        self.preview_window.destroy()
        self.root.deiconify()

    def retake(self):
        self.preview_window.destroy()
        self.start_selection()

if __name__ == "__main__":
    ScreenshotPopup()
