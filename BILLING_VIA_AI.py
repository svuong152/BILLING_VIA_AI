import os
import sys
import json
import tkinter as tk
from tkinter import Canvas, messagebox, ttk, Toplevel, Label
from PIL import Image, ImageTk
import cv2
import numpy as np
import serial
import serial.tools.list_ports
from ultralytics import YOLO
from tensorflow.keras.models import load_model
from datetime import datetime
import pandas as pd

# =====================
# Configuration
# =====================
SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
MENU_PATH = os.path.join('MENU', 'MENU.json')
HISTORY_PATH = os.path.join('MENU', 'history.csv')
MODEL_DIR = 'MODEL'
ASSETS_DIR = 'assets'
BACKGROUND_PATH = os.path.join(ASSETS_DIR, 'homescreen.png')
BACKGROUNDMAIN_PATH = os.path.join(ASSETS_DIR, 'bg.png')
QR_PATH = os.path.join(ASSETS_DIR, 'qr.jpg')

CAMERA_DEVICE = 1
CAMERA_RESOLUTION = (1280, 720)
DISPLAY_SIZE = (800, 450)
CAMERA_POSITION = (646, 210)

LIST_POSITION = (105, 250)
LINE_HEIGHT = 40
TOTAL_POSITION = (240, 757)
TOTAL_FONT = ('Arial', 40, 'bold')

ROI_SIZE = (224, 224)

SERIAL_BAUDRATE = 9600
REFRESH_INTERVAL = 50

ZOOM_FACTOR = 1.5

CNN_LABELS = {
    0: 'CAHUKHO', 1: 'CANHCAI', 2: 'CANHCHUA', 3: 'COM',
    4: 'DAUHUSOTCA', 5: 'GACHIEN', 6: 'RAUMUONGXAO',
    7: 'THITKHOTIEU', 8: 'THITKHOTRUNG', 9: 'TRUNGCHIEN',
}

BUTTON_CONFIGS = [
    {'coords': (960, 520, 1350, 640), 'command': 'run_billing'},
    {'coords': (960, 673, 1350, 793), 'command': 'on_quit'}
]


def resource_path(relative_path: str) -> str:
    if hasattr(sys, '_MEIPASS'):
        base = sys._MEIPASS
    else:
        base = SCRIPT_DIR
    return os.path.join(base, relative_path)


def find_arduino_port() -> str | None:
    for port in serial.tools.list_ports.comports():
        desc = port.description.lower()
        if any(k in desc for k in ('arduino', 'usb serial', 'ch340')):
            return port.device
    return None


class CommandMenuApp:
    def __init__(self, root, serial_port):
        self.root = root
        self.serial_port = serial_port
        self.root.title("BILLING_VIA_AI")
        self.root.geometry("1600x900")
        self.root.resizable(True, True)
        self.fullscreen = False

        self.canvas = Canvas(root, width=1600, height=900, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)

        self.load_background()
        self.create_buttons()
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", self.exit_fullscreen)
        self.root.bind("<Configure>", self.on_resize)

    def load_background(self):
        try:
            img = Image.open(resource_path(BACKGROUND_PATH))
            self.bg_image = img
            self.update_background()
        except Exception:
            self.canvas.configure(bg='gray')

    def update_background(self):
        w, h = self.root.winfo_width(), self.root.winfo_height()
        resized = self.bg_image.resize((w, h), Image.Resampling.LANCZOS)
        self.bg_photo = ImageTk.PhotoImage(resized)
        self.canvas.create_image(0, 0, image=self.bg_photo, anchor=tk.NW)

    def create_buttons(self):
        for cfg in BUTTON_CONFIGS:
            x1, y1, x2, y2 = cfg['coords']
            rect = self.canvas.create_rectangle(x1, y1, x2, y2, outline='', fill='', width=2)
            text = self.canvas.create_text((x1+x2)//2, (y1+y2)//2, text='', fill='white', font=('Arial',20,'bold'))
            cmd = getattr(self, cfg['command'])
            for tag in (rect, text):
                self.canvas.tag_bind(tag, '<Button-1>', lambda e, c=cmd: c())
                self.canvas.tag_bind(tag, '<Enter>', lambda e: self.root.config(cursor="hand2"))
                self.canvas.tag_bind(tag, '<Leave>', lambda e: self.root.config(cursor=""))

    def run_billing(self):
        # Unbind resize to prevent invalid canvas error
        try:
            self.root.unbind("<Configure>")
        except:
            pass
        self.canvas.destroy()
        AnnotationApp(self.root, self.serial_port)

    def on_resize(self, event):
        self.canvas.delete('all')
        self.update_background()
        self.create_buttons()

    def toggle_fullscreen(self, event=None):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)
        return "break"

    def exit_fullscreen(self, event=None):
        self.fullscreen = False
        self.root.attributes("-fullscreen", False)
        return "break"

    def on_quit(self):
        self.root.destroy()


class AnnotationApp:
    def __init__(self, root: tk.Tk, serial_port: str):
        self.root = root
        self.root.title("BILLING_VIA_AI")
        self.root.geometry("1600x900")
        self.root.resizable(True, True)
        self.is_live = True
        self.captured = False
        self.last_frame = None
        self.item_tags = []
        self.banking_btn = None
        self.banking_window = None
        self.ser = None

        self._initialize_csv(clean=True)
        self.yolo = YOLO(resource_path(os.path.join(MODEL_DIR, 'best.pt')))
        self.cnn = load_model(resource_path(os.path.join(MODEL_DIR, 'cnnmodel.h5')), compile=False)
        self.menu = self._load_menu()
        self.ser = serial.Serial(serial_port, SERIAL_BAUDRATE, timeout=0)

        self.cap = cv2.VideoCapture(CAMERA_DEVICE)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_RESOLUTION[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_RESOLUTION[1])

        self.canvas = Canvas(self.root, bg='black', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', self._on_resize)

        self._load_background()
        self._build_overlays()

        self._loop_camera()
        self._loop_serial()
        self.root.protocol('WM_DELETE_WINDOW', self.close)

    def _initialize_csv(self, clean: bool = False):
        path = resource_path(HISTORY_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cols = ['STT','Time','Mon1','Mon2','Mon3','Mon4','Mon5','Mon6','Thanhtien','Total']
        if clean or not os.path.exists(path):
            pd.DataFrame(columns=cols).to_csv(path, index=False)

    def _load_menu(self) -> list[dict]:
        try:
            with open(resource_path(MENU_PATH), 'r', encoding='utf-8') as f:
                return json.load(f).get('MENU', [])
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không đọc được file menu: {e}")
            return []

    def _load_background(self):
        """Load and store original background for AnnotationApp."""
        try:
            orig = Image.open(resource_path(BACKGROUNDMAIN_PATH))
            self.bg_orig = orig  # store for resize events
            resized = orig.resize((self.root.winfo_width(), self.root.winfo_height()), Image.Resampling.LANCZOS)
            self.bg_photo = ImageTk.PhotoImage(resized)
            self.canvas.create_image(0, 0, image=self.bg_photo, anchor=tk.NW, tags='bg')
        except Exception:
            self.canvas.configure(bg='black')

    def _build_overlays(self):
        self.image_label = tk.Label(self.root, bd=0)
        self.cam_window = self.canvas.create_window(
            CAMERA_POSITION, window=self.image_label,
            width=DISPLAY_SIZE[0], height=DISPLAY_SIZE[1], anchor=tk.NW)
        self.history_btn = self.canvas.create_rectangle(
            1400, 10, 1500, 60, fill='', outline='', tags='history_btn')
        self.canvas.tag_bind('history_btn', '<Button-1>', self._show_history)

    def _on_resize(self, event):
        self.canvas.delete('bg')
        try:
            resized = self.bg_orig.resize((event.width, event.height), Image.Resampling.LANCZOS)
            self.bg_imgtk = ImageTk.PhotoImage(resized)
            self.canvas.create_image(0, 0, image=self.bg_imgtk, anchor=tk.NW, tags='bg')
            self.canvas.tag_lower('bg')
        except AttributeError:
            pass
        self.canvas.coords(self.cam_window, CAMERA_POSITION)

    def _zoom_frame(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        cw, ch = int(w/ZOOM_FACTOR), int(h/ZOOM_FACTOR)
        x, y = (w-cw)//2, (h-ch)//2
        crop = frame[y:y+ch, x:x+cw]
        return cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)

    def _loop_camera(self):
        if self.is_live:
            ret, frame = self.cap.read()
            if ret:
                zoomed = self._zoom_frame(frame)
                self.last_frame = zoomed.copy()
                disp = cv2.cvtColor(zoomed, cv2.COLOR_BGR2RGB)
                disp_img = Image.fromarray(disp).resize(DISPLAY_SIZE, Image.LANCZOS)
                photo = ImageTk.PhotoImage(disp_img)
                self.image_label.config(image=photo)
                self.image_label.image = photo
        self.root.after(10, self._loop_camera)

    def _loop_serial(self):
        try:
            while self.ser.in_waiting:
                data = self.ser.readline().decode(errors='ignore').strip()
                if data == '1':
                    if not self.captured:
                        self._process_capture()
                        self.captured = True
                    else:
                        self._reset()
                        self.captured = False
        except:
            pass
        finally:
            self.root.after(REFRESH_INTERVAL, self._loop_serial)

    def _process_capture(self):
        if self.last_frame is None:
            return
        frame = self.last_frame.copy()
        results = self.yolo(frame)[0]
        items, prices = [], []
        annotated = frame.copy()
        boxes = results.boxes.xyxy.cpu().numpy().astype(int)
        classes = results.boxes.cls.cpu().numpy().astype(int)
        for (x1, y1, x2, y2), cls_id in zip(boxes, classes):
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0,255,0), 2)
            try:
                roi = cv2.resize(frame[y1:y2, x1:x2], ROI_SIZE)
                rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB) / 255.0
                idx = np.argmax(self.cnn.predict(np.expand_dims(rgb,0)), axis=1)[0]
                item = CNN_LABELS.get(idx)
                if item and item not in items:
                    items.append(item)
                    for e in self.menu:
                        if item == e['MON'].replace(' ','_').upper():
                            prices.append(e['GIA'])
                            break
            except:
                pass
        self._display_result(annotated, items, prices)
        self._save_to_csv(items, sum(prices))

    def _display_result(self, frame, items, prices):
        disp_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        disp_img = disp_img.resize(DISPLAY_SIZE, Image.LANCZOS)
        photo = ImageTk.PhotoImage(disp_img)
        self.image_label.config(image=photo)
        self.image_label.image = photo
        self.is_live = False
        for tag in self.item_tags:
            self.canvas.delete(tag)
        self.item_tags.clear()
        x, y = LIST_POSITION
        for i, (m, p) in enumerate(zip(items, prices)):
            self.item_tags.append(
                self.canvas.create_text(x, y+i*LINE_HEIGHT, text=m, anchor=tk.NW,
                                        font=('Arial',24), fill='white'))
            self.item_tags.append(
                self.canvas.create_text(x+300, y+i*LINE_HEIGHT, text=str(p),
                                        anchor=tk.NW, font=('Arial',24), fill='white'))
        total = sum(prices)
        self.item_tags.append(
            self.canvas.create_text(TOTAL_POSITION[0], TOTAL_POSITION[1],
                                    text=str(total), anchor=tk.NW,
                                    font=TOTAL_FONT, fill='white'))
        
        x2 = 220
        y2 = 60
        x1 = 20
        y1 = 20
        self.banking_btn = self.canvas.create_rectangle(x1, y2, x2, y1, fill='blue', outline='')
        self.canvas.create_text((x1+x2)//2, (y2+y1)//2,
                                text='Tap to send money',
                                fill='white', font=('Arial',14,'bold'),
                                tags='send_money_text')
        for tag in (self.banking_btn, 'send_money_text'):
            self.canvas.tag_bind(tag, '<Button-1>', lambda e: self.tap_to_send_money())

    def tap_to_send_money(self):
        if self.banking_window and tk.Toplevel.winfo_exists(self.banking_window):
            return
        top = Toplevel(self.root)
        top.title("Send Money")
        img = Image.open(resource_path(QR_PATH))
        img = img.resize((781, 781), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        lbl = Label(top, image=photo)
        lbl.image = photo
        lbl.pack()
        self.banking_window = top

    def _save_to_csv(self, items, total):
        path = resource_path(HISTORY_PATH)
        try:
            df = pd.read_csv(path)
        except FileNotFoundError:
            cols = ['STT','Time','Mon1','Mon2','Mon3','Mon4','Mon5','Mon6','Thanhtien','Total']
            df = pd.DataFrame(columns=cols)
        new_record = {
            'STT': len(df)+1,
            'Time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            **{f'Mon{i+1}': items[i] if i < len(items) else '' for i in range(6)},
            'Thanhtien': total,
            'Total': 0
        }
        df = pd.concat([df, pd.DataFrame([new_record])], ignore_index=True)
        df['Total'] = df['Thanhtien'].cumsum()
        df.to_csv(path, index=False)

    def _show_history(self, event=None):
        path = resource_path(HISTORY_PATH)
        if not os.path.exists(path):
            messagebox.showinfo("Thông báo","Chưa có dữ liệu lịch sử")
            return
        win = Toplevel(self.root)
        win.title("Lịch sử giao dịch")
        win.geometry("1200x500")
        cols = ("STT","Time","Mon1","Mon2","Mon3","Mon4","Mon5","Mon6","Thanhtien","Total")
        tree = ttk.Treeview(win, columns=cols, show='headings')
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=120, anchor='center')
        vsb = ttk.Scrollbar(win, orient='vertical', command=tree.yview)
        hsb = ttk.Scrollbar(win, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        df = pd.read_csv(path).fillna('')
        for _, row in df.iterrows():
            tree.insert('', 'end', values=tuple(row))
        tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        win.grid_rowconfigure(0, weight=1)
        win.grid_columnconfigure(0, weight=1)

    def _reset(self):
        self.is_live = True
        for tag in self.item_tags:
            self.canvas.delete(tag)
        self.item_tags.clear()
        if self.banking_btn:
            self.canvas.delete(self.banking_btn)
            self.banking_btn = None
        self.canvas.delete('send_money_text')
        if self.banking_window:
            self.banking_window.destroy()
            self.banking_window = None

    def close(self):
        try:
            if self.cap.isOpened():
                self.cap.release()
            if self.ser.is_open:
                self.ser.close()
        except:
            pass
        finally:
            self.root.destroy()


if __name__ == '__main__':
    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
    port =	find_arduino_port()
    if not port:
        messagebox.showerror('Lỗi', 'Không tìm thấy Arduino!')
        sys.exit(1)
    root = tk.Tk()
    CommandMenuApp(root, port)
    root.mainloop()
