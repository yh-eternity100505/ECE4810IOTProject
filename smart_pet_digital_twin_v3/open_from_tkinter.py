"""Add the button to an existing Tkinter GUI; ensure the server is running."""
import os
import webbrowser
import tkinter as tk

def add_digital_twin_button(parent):
    url = os.getenv('PET_VIEW_URL', 'http://127.0.0.1:5000')
    button = tk.Button(parent, text='Open Interactive 3D Digital Twin', command=lambda: webbrowser.open(url))
    button.pack(pady=8)
    return button
