#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sistema de Gerenciamento de Video Wall - SALA NOC
Ministerio da Justica e Seguranca Publica
Versao Final - Portas 48686/8001/23
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import json
import socket
import threading
import time
import struct
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
import os
from datetime import datetime
import sys

if sys.platform == 'win32':
    try:
        import locale
        locale.setlocale(locale.LC_ALL, '')
    except:
        pass


@dataclass
class CropRegion:
    x: int = 0
    y: int = 0
    width: int = 1920
    height: int = 1080
    source_width: int = 1920
    source_height: int = 1080
    enabled: bool = False
    
    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data):
        return cls(**data)


@dataclass
class Encoder:
    id: str
    name: str
    ip: str
    port: int = 48686
    rtsp_port: int = 551
    description: str = ""
    status: str = "offline"
    width: int = 1920
    height: int = 1080
    
    def get_rtsp_url(self):
        return f"rtsp://{self.ip}:{self.rtsp_port}/2160"
    
    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data):
        return cls(**data)


@dataclass
class Decoder:
    id: str
    name: str
    ip: str
    port: int = 48686
    telnet_port: int = 23
    http_port: int = 8001
    position: Tuple[int, int] = (0, 0)
    current_source: Optional[str] = None
    status: str = "offline"
    crop: Optional[Dict] = None
    
    def to_dict(self):
        data = asdict(self)
        data['position'] = list(self.position)
        return data
    
    @classmethod
    def from_dict(cls, data):
        data['position'] = tuple(data['position'])
        return cls(**data)
    
    def get_crop_region(self):
        if self.crop:
            return CropRegion.from_dict(self.crop)
        return None
    
    def set_crop_region(self, crop):
        if crop:
            self.crop = crop.to_dict()
        else:
            self.crop = None


@dataclass
class MatrixGroup:
    id: str
    name: str
    decoders: List[str]
    rows: int
    cols: int
    source: Optional[str] = None
    auto_crop: bool = True
    
    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data):
        return cls(**data)


@dataclass
class Preset:
    id: str
    name: str
    timestamp: str
    mappings: Dict[str, str]
    matrices: List[Dict]
    crops: Dict[str, Dict]
    
    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data):
        if 'crops' not in data:
            data['crops'] = {}
        return cls(**data)


class AVCITController:
    """Controlador AVCIT - Portas 48686, 8001, 23"""
    
    def __init__(self, timeout: float = 3.0):
        self.timeout = timeout
        self.command_log = []
        self.username = "admin"
        self.password = "admin"
    
    def log_command(self, target_ip, command, success, response=None):
        self.command_log.append({
            'timestamp': datetime.now().isoformat(),
            'target': target_ip,
            'command': command,
            'success': success,
            'response': str(response)[:200] if response else None
        })
        if len(self.command_log) > 200:
            self.command_log.pop(0)
    
    def set_credentials(self, username, password):
        self.username = username
        self.password = password
    
    def send_codec_command(self, ip: str, port: int, command: bytes):
        """Envia comando binario para codec.app na porta 48686"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect((ip, port))
                sock.sendall(command)
                time.sleep(0.3)
                response = sock.recv(4096)
                self.log_command(ip, f"CODEC:{command[:20].hex()}", True, response.hex() if response else None)
                return response
        except Exception as e:
            self.log_command(ip, f"CODEC:{port}", False, str(e))
            return None
    
    def send_http_command(self, ip: str, port: int, path: str, params: dict = None):
        """Envia comando HTTP para nvweb/codec"""
        try:
            query = ""
            if params:
                query = "?" + "&".join([f"{k}={v}" for k, v in params.items()])
            
            request = f"GET {path}{query} HTTP/1.1\r\nHost: {ip}\r\nConnection: close\r\n\r\n"
            
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect((ip, port))
                sock.sendall(request.encode())
                
                response = b""
                while True:
                    try:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        response += chunk
                    except socket.timeout:
                        break
                
                self.log_command(ip, f"HTTP:{port}{path}", True, response[:200].decode('utf-8', errors='ignore'))
                return response.decode('utf-8', errors='ignore')
        except Exception as e:
            self.log_command(ip, f"HTTP:{port}{path}", False, str(e))
            return None
    
    def send_telnet_command(self, ip: str, command: str):
        """Envia comando via Telnet com autenticacao"""
        try:
            import telnetlib
            tn = telnetlib.Telnet(ip, 23, timeout=self.timeout)
            
            tn.read_until(b"login:", timeout=2)
            tn.write((self.username + "\n").encode())
            tn.read_until(b"assword:", timeout=2)
            tn.write((self.password + "\n").encode())
            time.sleep(0.5)
            tn.read_very_eager()
            
            tn.write((command + "\n").encode())
            time.sleep(0.5)
            response = tn.read_very_eager().decode('utf-8', errors='ignore')
            
            tn.close()
            self.log_command(ip, f"TELNET:{command}", True, response)
            return response
        except Exception as e:
            self.log_command(ip, f"TELNET:{command}", False, str(e))
            return None
    
    def switch_source(self, decoder_ip: str, decoder_port: int, encoder_ip: str, encoder_port: int):
        """Tenta trocar fonte usando varios metodos"""
        
        # Metodo 1: Porta 48686 (codec.app control)
        # Formato provavel: comando binario com IP do encoder
        try:
            # Tenta comando binario simples
            ip_bytes = bytes(map(int, encoder_ip.split('.')))
            commands = [
                b'\x00\x01' + ip_bytes + b'\x00\x00',  # switch command type 1
                b'\x01\x00' + ip_bytes + b'\x00\x00',  # switch command type 2
                struct.pack('>BBBBBB', 0x01, *map(int, encoder_ip.split('.')), 0x00),
            ]
            
            for cmd in commands:
                response = self.send_codec_command(decoder_ip, 48686, cmd)
                if response:
                    return True
        except:
            pass
        
        # Metodo 2: HTTP na porta 8001
        endpoints = [
            ("/switch", {"ip": encoder_ip}),
            ("/api/switch", {"source": encoder_ip}),
            ("/source", {"ip": encoder_ip}),
            ("/set", {"source": encoder_ip}),
        ]
        
        for path, params in endpoints:
            response = self.send_http_command(decoder_ip, 8001, path, params)
            if response and ("ok" in response.lower() or "success" in response.lower()):
                return True
        
        # Metodo 3: Telnet
        telnet_commands = [
            f"switch {encoder_ip}",
            f"source {encoder_ip}",
            f"play rtsp://{encoder_ip}:551/2160",
        ]
        
        for cmd in telnet_commands:
            response = self.send_telnet_command(decoder_ip, cmd)
            if response and "ok" in response.lower():
                return True
        
        return False
    
    def set_crop(self, decoder_ip: str, decoder_port: int, crop):
        """Tenta configurar crop"""
        
        # Metodo 1: HTTP
        params = {"x": crop.x, "y": crop.y, "w": crop.width, "h": crop.height}
        response = self.send_http_command(decoder_ip, 8001, "/crop", params)
        if response and "ok" in response.lower():
            return True
        
        # Metodo 2: Telnet
        cmd = f"crop {crop.x} {crop.y} {crop.width} {crop.height}"
        response = self.send_telnet_command(decoder_ip, cmd)
        if response and "ok" in response.lower():
            return True
        
        return False
    
    def clear_crop(self, decoder_ip: str, decoder_port: int):
        self.send_telnet_command(decoder_ip, "crop reset")
        return True
    
    def ping_device(self, ip: str, port: int):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                result = sock.connect_ex((ip, port))
                return result == 0
        except:
            return False
    
    def check_device_status(self, ip: str, port: int = 48686):
        # Tenta varias portas
        for p in [48686, 8001, 23]:
            if self.ping_device(ip, p):
                return "online"
        return "offline"


class CropSelectorDialog(tk.Toplevel):
    """Dialogo para selecao visual de recorte"""
    
    def __init__(self, parent, encoder, current_crop, callback):
        super().__init__(parent)
        self.encoder = encoder
        self.callback = callback
        self.current_crop = current_crop
        
        self.title(f"Configurar Recorte - {encoder.name}")
        self.geometry("900x700")
        self.configure(bg='#2d2d2d')
        self.transient(parent)
        self.grab_set()
        
        self.selection_start = None
        self.current_rect_id = None
        self.source_width = encoder.width
        self.source_height = encoder.height
        self.display_width = 800
        self.display_height = 450
        self.scale_x = self.display_width / self.source_width
        self.scale_y = self.display_height / self.source_height
        
        self.build_ui()
        if current_crop and current_crop.enabled:
            self.load_existing_crop(current_crop)
    
    def build_ui(self):
        main_frame = tk.Frame(self, bg='#2d2d2d')
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Info
        info_frame = tk.Frame(main_frame, bg='#2d2d2d')
        info_frame.pack(fill='x', pady=5)
        tk.Label(info_frame, text=f"Fonte: {self.encoder.name} ({self.encoder.ip})", bg='#2d2d2d', fg='white', font=('Segoe UI', 11, 'bold')).pack(side='left')
        tk.Label(info_frame, text=f"Resolucao: {self.source_width}x{self.source_height}", bg='#2d2d2d', fg='#888', font=('Segoe UI', 10)).pack(side='right')
        
        tk.Label(main_frame, text="Clique e arraste para selecionar a regiao", bg='#2d2d2d', fg='#888', font=('Segoe UI', 9)).pack()
        
        # Canvas
        canvas_frame = tk.Frame(main_frame, bg='#1a1a2e', relief='solid', bd=2)
        canvas_frame.pack(pady=10)
        
        self.canvas = tk.Canvas(canvas_frame, width=self.display_width, height=self.display_height, bg='#0f0f23', highlightthickness=0)
        self.canvas.pack()
        
        self.draw_grid()
        self.draw_source()
        
        self.canvas.bind('<Button-1>', self.on_mouse_down)
        self.canvas.bind('<B1-Motion>', self.on_mouse_drag)
        self.canvas.bind('<ButtonRelease-1>', self.on_mouse_up)
        
        # Controles numericos
        ctrl_frame = tk.Frame(main_frame, bg='#2d2d2d')
        ctrl_frame.pack(fill='x', pady=10)
        
        input_frame = tk.Frame(ctrl_frame, bg='#2d2d2d')
        input_frame.pack()
        
        for i, (label, default) in enumerate([("X:", "0"), ("Y:", "0"), ("Largura:", str(self.source_width)), ("Altura:", str(self.source_height))]):
            tk.Label(input_frame, text=label, bg='#2d2d2d', fg='white').grid(row=0, column=i*2, padx=3)
            var = tk.StringVar(value=default)
            setattr(self, ['x_var', 'y_var', 'w_var', 'h_var'][i], var)
            ttk.Entry(input_frame, textvariable=var, width=8).grid(row=0, column=i*2+1, padx=3)
        
        ttk.Button(input_frame, text="Aplicar Valores", command=self.update_from_values).grid(row=0, column=8, padx=10)
        
        # Presets
        presets_frame = tk.LabelFrame(ctrl_frame, text="Presets Rapidos", bg='#2d2d2d', fg='white')
        presets_frame.pack(fill='x', pady=10, padx=20)
        
        presets_inner = tk.Frame(presets_frame, bg='#2d2d2d')
        presets_inner.pack(pady=5)
        
        presets = [
            ("Tela Cheia", 0, 0, 1, 1),
            ("Centro 50%", 0.25, 0.25, 0.5, 0.5),
            ("Sup. Esq.", 0, 0, 0.5, 0.5),
            ("Sup. Dir.", 0.5, 0, 0.5, 0.5),
            ("Inf. Esq.", 0, 0.5, 0.5, 0.5),
            ("Inf. Dir.", 0.5, 0.5, 0.5, 0.5),
            ("Esquerda", 0, 0, 0.5, 1),
            ("Direita", 0.5, 0, 0.5, 1),
            ("Superior", 0, 0, 1, 0.5),
            ("Inferior", 0, 0.5, 1, 0.5),
        ]
        
        for i, (name, px, py, pw, ph) in enumerate(presets):
            cmd = lambda x=px, y=py, w=pw, h=ph: self.set_crop_percent(x, y, w, h)
            ttk.Button(presets_inner, text=name, command=cmd, width=12).grid(row=i//5, column=i%5, padx=2, pady=2)
        
        # Info
        self.info_label = tk.Label(main_frame, text="Nenhuma regiao selecionada", bg='#2d2d2d', fg='#4a9eff', font=('Segoe UI', 10))
        self.info_label.pack(pady=5)
        
        # Botoes
        btn_frame = tk.Frame(main_frame, bg='#2d2d2d')
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Aplicar Recorte", command=self.apply_crop).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Limpar Recorte", command=self.clear_crop).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy).pack(side='left', padx=5)
    
    def draw_grid(self):
        for i in range(1, 4):
            x = i * self.display_width // 4
            self.canvas.create_line(x, 0, x, self.display_height, fill='#333', dash=(2, 4))
        for i in range(1, 4):
            y = i * self.display_height // 4
            self.canvas.create_line(0, y, self.display_width, y, fill='#333', dash=(2, 4))
    
    def draw_source(self):
        self.canvas.create_rectangle(2, 2, self.display_width-2, self.display_height-2, outline='#4a9eff', width=2)
        self.canvas.create_text(self.display_width//2, self.display_height//2, text=f"{self.encoder.name}\n{self.source_width}x{self.source_height}", fill='#666', font=('Segoe UI', 12), justify='center')
    
    def load_existing_crop(self, crop):
        self.x_var.set(str(crop.x))
        self.y_var.set(str(crop.y))
        self.w_var.set(str(crop.width))
        self.h_var.set(str(crop.height))
        self.update_from_values()
    
    def set_crop_percent(self, px, py, pw, ph):
        x = int(self.source_width * px)
        y = int(self.source_height * py)
        w = int(self.source_width * pw)
        h = int(self.source_height * ph)
        self.x_var.set(str(x))
        self.y_var.set(str(y))
        self.w_var.set(str(w))
        self.h_var.set(str(h))
        self.update_from_values()
    
    def on_mouse_down(self, event):
        self.selection_start = (event.x, event.y)
        if self.current_rect_id:
            self.canvas.delete(self.current_rect_id)
    
    def on_mouse_drag(self, event):
        if not self.selection_start:
            return
        x1, y1 = self.selection_start
        x2 = max(0, min(event.x, self.display_width))
        y2 = max(0, min(event.y, self.display_height))
        if self.current_rect_id:
            self.canvas.delete(self.current_rect_id)
        self.current_rect_id = self.canvas.create_rectangle(x1, y1, x2, y2, outline='#00ff00', width=2, dash=(5, 3))
        self.update_values_from_canvas(x1, y1, x2, y2)
    
    def on_mouse_up(self, event):
        if not self.selection_start:
            return
        x1, y1 = self.selection_start
        x2 = max(0, min(event.x, self.display_width))
        y2 = max(0, min(event.y, self.display_height))
        if x1 > x2: x1, x2 = x2, x1
        if y1 > y2: y1, y2 = y2, y1
        self.update_values_from_canvas(x1, y1, x2, y2)
        if self.current_rect_id:
            self.canvas.delete(self.current_rect_id)
        self.current_rect_id = self.canvas.create_rectangle(x1, y1, x2, y2, outline='#00ff00', width=3)
        self.selection_start = None
    
    def update_values_from_canvas(self, x1, y1, x2, y2):
        if x1 > x2: x1, x2 = x2, x1
        if y1 > y2: y1, y2 = y2, y1
        rx = int(x1 / self.scale_x)
        ry = int(y1 / self.scale_y)
        rw = int((x2 - x1) / self.scale_x)
        rh = int((y2 - y1) / self.scale_y)
        self.x_var.set(str(max(0, rx)))
        self.y_var.set(str(max(0, ry)))
        self.w_var.set(str(max(1, rw)))
        self.h_var.set(str(max(1, rh)))
        pct = (rw * rh) / (self.source_width * self.source_height) * 100
        self.info_label.configure(text=f"Regiao: ({rx}, {ry}) - {rw}x{rh} ({pct:.1f}%)")
    
    def update_from_values(self):
        try:
            x, y = int(self.x_var.get()), int(self.y_var.get())
            w, h = int(self.w_var.get()), int(self.h_var.get())
            cx1, cy1 = int(x * self.scale_x), int(y * self.scale_y)
            cx2, cy2 = int((x + w) * self.scale_x), int((y + h) * self.scale_y)
            self.canvas.delete('all')
            self.draw_grid()
            self.draw_source()
            self.current_rect_id = self.canvas.create_rectangle(cx1, cy1, cx2, cy2, outline='#00ff00', width=3)
            pct = (w * h) / (self.source_width * self.source_height) * 100
            self.info_label.configure(text=f"Regiao: ({x}, {y}) - {w}x{h} ({pct:.1f}%)")
        except:
            pass
    
    def apply_crop(self):
        try:
            crop = CropRegion(x=int(self.x_var.get()), y=int(self.y_var.get()), width=int(self.w_var.get()), height=int(self.h_var.get()), source_width=self.source_width, source_height=self.source_height, enabled=True)
            self.callback(crop)
            self.destroy()
        except ValueError as e:
            messagebox.showerror("Erro", f"Valores invalidos: {e}")
    
    def clear_crop(self):
        self.callback(None)
        self.destroy()


class CommandLogDialog(tk.Toplevel):
    def __init__(self, parent, avcit):
        super().__init__(parent)
        self.avcit = avcit
        self.title("Log de Comandos")
        self.geometry("900x500")
        self.configure(bg='#2d2d2d')
        
        text_frame = tk.Frame(self, bg='#2d2d2d')
        text_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side='right', fill='y')
        
        self.text = tk.Text(text_frame, bg='#1a1a2e', fg='white', font=('Consolas', 9), yscrollcommand=scrollbar.set)
        self.text.pack(fill='both', expand=True)
        scrollbar.config(command=self.text.yview)
        
        btn_frame = tk.Frame(self, bg='#2d2d2d')
        btn_frame.pack(fill='x', pady=5)
        ttk.Button(btn_frame, text="Atualizar", command=self.refresh).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Limpar", command=self.clear).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Fechar", command=self.destroy).pack(side='right', padx=5)
        
        self.refresh()
    
    def refresh(self):
        self.text.delete('1.0', tk.END)
        for e in reversed(self.avcit.command_log[-50:]):
            status = "OK" if e['success'] else "ERRO"
            self.text.insert('end', f"[{e['timestamp'][:19]}] {e['target']} - {e['command']}\n  {status}: {e['response']}\n\n")
    
    def clear(self):
        self.avcit.command_log.clear()
        self.refresh()


class DraggableSource(tk.Frame):
    def __init__(self, parent, encoder, controller, **kwargs):
        super().__init__(parent, **kwargs)
        self.encoder = encoder
        self.controller = controller
        self.configure(bg='#2d2d2d', relief='raised', bd=2, cursor='hand2')
        
        content = tk.Frame(self, bg='#2d2d2d')
        content.pack(fill='both', expand=True, padx=2, pady=2)
        
        color = '#00ff00' if encoder.status == 'online' else '#ff0000'
        self.status_ind = tk.Canvas(content, width=10, height=10, bg='#2d2d2d', highlightthickness=0)
        self.status_ind.create_oval(2, 2, 8, 8, fill=color, tags='status')
        self.status_ind.pack(side='left', padx=2)
        
        self.name_lbl = tk.Label(content, text=encoder.name, bg='#2d2d2d', fg='white', font=('Segoe UI', 9, 'bold'))
        self.name_lbl.pack(side='left', padx=5)
        
        self.ip_lbl = tk.Label(content, text=encoder.ip, bg='#2d2d2d', fg='#888', font=('Segoe UI', 8))
        self.ip_lbl.pack(side='right', padx=5)
        
        for w in [self, content, self.name_lbl, self.ip_lbl]:
            w.bind('<Button-1>', lambda e: controller.start_drag(encoder))
            w.bind('<B1-Motion>', lambda e: controller.drag_motion(e.x_root, e.y_root))
            w.bind('<ButtonRelease-1>', lambda e: controller.end_drag(e.x_root, e.y_root))
    
    def update_status(self, status):
        self.encoder.status = status
        color = '#00ff00' if status == 'online' else '#ff0000'
        self.status_ind.delete('status')
        self.status_ind.create_oval(2, 2, 8, 8, fill=color, tags='status')


class MonitorWidget(tk.Frame):
    def __init__(self, parent, decoder, controller, **kwargs):
        super().__init__(parent, **kwargs)
        self.decoder = decoder
        self.controller = controller
        self.selected = False
        
        self.configure(bg='#1a1a2e', relief='solid', bd=1, cursor='crosshair')
        
        self.canvas = tk.Canvas(self, bg='#0f0f23', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, padx=1, pady=1)
        
        self.id_lbl = tk.Label(self.canvas, text=decoder.name, bg='#0f0f23', fg='#4a9eff', font=('Segoe UI', 7, 'bold'))
        self.id_lbl.place(x=2, y=2)
        
        self.src_lbl = tk.Label(self.canvas, text="Sem fonte", bg='#0f0f23', fg='#888', font=('Segoe UI', 6))
        self.src_lbl.place(relx=0.5, rely=0.4, anchor='center')
        
        self.crop_lbl = tk.Label(self.canvas, text="", bg='#0f0f23', fg='#ffaa00', font=('Segoe UI', 5))
        self.crop_lbl.place(relx=0.5, rely=0.65, anchor='center')
        
        self.status_lbl = tk.Label(self.canvas, text="*", bg='#0f0f23', fg='#ff0000', font=('Segoe UI', 8))
        self.status_lbl.place(relx=1.0, x=-12, y=2)
        
        for w in [self, self.canvas, self.id_lbl, self.src_lbl]:
            w.bind('<Button-1>', lambda e: controller.monitor_clicked(self))
            w.bind('<Double-Button-1>', lambda e: controller.open_crop_dialog(self))
            w.bind('<Button-3>', self.show_menu)
        
        self.bind('<Enter>', lambda e: self.configure(bg='#2a2a4e') if not self.selected else None)
        self.bind('<Leave>', lambda e: self.configure(bg='#1a1a2e') if not self.selected else None)
        
        if decoder.crop:
            self.update_crop_display()
    
    def show_menu(self, event):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Configurar Recorte...", command=lambda: self.controller.open_crop_dialog(self))
        menu.add_command(label="Limpar Recorte", command=self.clear_crop)
        menu.add_separator()
        menu.add_command(label="Limpar Fonte", command=self.clear_source)
        menu.add_command(label="Propriedades", command=self.show_props)
        menu.tk_popup(event.x_root, event.y_root)
    
    def clear_crop(self):
        self.decoder.set_crop_region(None)
        self.update_crop_display()
        threading.Thread(target=self.controller.avcit.clear_crop, args=(self.decoder.ip, self.decoder.port), daemon=True).start()
    
    def clear_source(self):
        self.set_source(None)
        self.clear_crop()
        self.controller.update_mapping(self.decoder.id, None)
    
    def show_props(self):
        crop_info = "Nenhum"
        if self.decoder.crop:
            c = self.decoder.crop
            crop_info = f"({c['x']},{c['y']}) {c['width']}x{c['height']}"
        info = f"Decoder: {self.decoder.name}\nIP: {self.decoder.ip}\nPortas: 48686, 8001, 23\nStatus: {self.decoder.status}\nFonte: {self.decoder.current_source or 'Nenhuma'}\nRecorte: {crop_info}"
        messagebox.showinfo("Propriedades", info)
    
    def set_source(self, encoder):
        if encoder:
            self.decoder.current_source = encoder.id
            self.src_lbl.configure(text=encoder.name, fg='#00ff00')
            self.canvas.configure(bg='#1a2a1a')
        else:
            self.decoder.current_source = None
            self.src_lbl.configure(text="Sem fonte", fg='#888')
            self.canvas.configure(bg='#0f0f23')
    
    def set_crop(self, crop):
        self.decoder.set_crop_region(crop)
        self.update_crop_display()
    
    def update_crop_display(self):
        if self.decoder.crop and self.decoder.crop.get('enabled'):
            c = self.decoder.crop
            self.crop_lbl.configure(text=f"[{c['width']}x{c['height']}]")
        else:
            self.crop_lbl.configure(text="")
    
    def set_selected(self, selected):
        self.selected = selected
        self.configure(bg='#4a4a8e' if selected else '#1a1a2e', bd=2 if selected else 1)
    
    def update_status(self, status):
        self.decoder.status = status
        self.status_lbl.configure(fg='#00ff00' if status == 'online' else '#ff0000')


class MatrixSelector(tk.Toplevel):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("Criar Matriz")
        self.geometry("350x250")
        self.configure(bg='#2d2d2d')
        self.transient(parent)
        self.grab_set()
        
        main = tk.Frame(self, bg='#2d2d2d')
        main.pack(fill='both', expand=True, padx=20, pady=20)
        
        tk.Label(main, text="Configurar Matriz", bg='#2d2d2d', fg='white', font=('Segoe UI', 12, 'bold')).pack(pady=10)
        
        spin_frame = tk.Frame(main, bg='#2d2d2d')
        spin_frame.pack(pady=10)
        
        tk.Label(spin_frame, text="Linhas:", bg='#2d2d2d', fg='white').grid(row=0, column=0, padx=5)
        self.rows_var = tk.StringVar(value="2")
        ttk.Spinbox(spin_frame, from_=1, to=8, width=5, textvariable=self.rows_var).grid(row=0, column=1, padx=5)
        
        tk.Label(spin_frame, text="Colunas:", bg='#2d2d2d', fg='white').grid(row=0, column=2, padx=5)
        self.cols_var = tk.StringVar(value="2")
        ttk.Spinbox(spin_frame, from_=1, to=14, width=5, textvariable=self.cols_var).grid(row=0, column=3, padx=5)
        
        self.auto_crop = tk.BooleanVar(value=True)
        ttk.Checkbutton(main, text="Dividir imagem automaticamente", variable=self.auto_crop).pack(pady=10)
        
        btn_frame = tk.Frame(main, bg='#2d2d2d')
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="OK", command=self.ok).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy).pack(side='left', padx=5)
    
    def ok(self):
        try:
            self.callback(int(self.rows_var.get()), int(self.cols_var.get()), self.auto_crop.get())
            self.destroy()
        except:
            pass


class VideoWallController:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Sistema de Gerenciamento de Video Wall - SALA NOC")
        self.root.geometry("1600x900")
        self.root.configure(bg='#1a1a2e')
        
        self.setup_styles()
        
        self.encoders = {}
        self.decoders = {}
        self.matrices = {}
        self.presets = {}
        
        self.dragging_encoder = None
        self.selected_monitors = []
        self.monitor_widgets = {}
        self.source_widgets = {}
        
        self.avcit = AVCITController()
        self.drag_window = None
        
        self.grid_rows = 4
        self.grid_cols = 14
        
        self.init_devices()
        self.build_ui()
        self.load_config()
        
        self.monitoring = True
        threading.Thread(target=self.monitor_devices, daemon=True).start()
    
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TButton', background='#4a4a8e', foreground='white', padding=5, font=('Segoe UI', 9))
        style.map('TButton', background=[('active', '#5a5a9e')])
    
    def init_devices(self):
        # Encoders - IPs do arquivo .tp
        encoder_config = [
            ("enc_01", "Mesa-01", "172.16.207.75"), ("enc_02", "Mesa-02", "172.16.207.76"),
            ("enc_03", "Mesa-03", "172.16.207.77"), ("enc_04", "Mesa-03.1", "172.16.207.78"),
            ("enc_05", "Mesa-04", "172.16.207.79"), ("enc_06", "Mesa-04.1", "172.16.207.80"),
            ("enc_07", "Mesa-05", "172.16.207.81"), ("enc_08", "Mesa-06", "172.16.207.82"),
            ("enc_09", "Mesa-07", "172.16.207.83"), ("enc_10", "Mesa-08", "172.16.207.84"),
            ("enc_11", "Mesa-09", "172.16.207.85"), ("enc_12", "Mesa_Reuniao", "172.16.207.86"),
            ("enc_13", "Biamp_Noc_1", "172.16.207.87"), ("enc_14", "Biamp_Noc_2", "172.16.207.88"),
            ("enc_15", "Crise_AP_01", "172.16.207.89"), ("enc_16", "Crise_AP_02", "172.16.207.90"),
            ("enc_17", "Crise_AP_03", "172.16.207.91"), ("enc_18", "Crise_Biamp", "172.16.207.92"),
        ]
        for enc_id, name, ip in encoder_config:
            self.encoders[enc_id] = Encoder(id=enc_id, name=name, ip=ip, port=48686, rtsp_port=551)
        
        # Decoders - IPs 172.16.207.11 ate 172.16.207.66
        for row in range(self.grid_rows):
            for col in range(self.grid_cols):
                idx = row * self.grid_cols + col + 1
                ip = f"172.16.207.{10 + idx}"
                self.decoders[f"dec_{idx:02d}"] = Decoder(id=f"dec_{idx:02d}", name=f"M{idx:02d}", ip=ip, port=48686, telnet_port=23, http_port=8001, position=(row, col))
    
    def build_ui(self):
        # Header
        header = tk.Frame(self.root, bg='#0d1b2a', height=50)
        header.pack(fill='x')
        header.pack_propagate(False)
        tk.Label(header, text="MJSP - SALA NOC-Desenvolvido pelo Engenheiro Eletricista Daniel Paz", bg='#0d1b2a', fg='#4a9eff', font=('Segoe UI', 10, 'bold')).pack(side='left', padx=20)
        tk.Label(header, text="Sistema de Video Wall", bg='#0d1b2a', fg='white', font=('Segoe UI', 14, 'bold')).pack(expand=True)
        self.status_lbl = tk.Label(header, text="* Online", bg='#0d1b2a', fg='#00ff00', font=('Segoe UI', 10))
        self.status_lbl.pack(side='right', padx=20)
        
        # Main container
        main = tk.Frame(self.root, bg='#1a1a2e')
        main.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Left panel
        left = tk.Frame(main, bg='#2d2d2d', width=240)
        left.pack(side='left', fill='y', padx=(0, 10))
        left.pack_propagate(False)
        
        # Sources
        src_frame = tk.LabelFrame(left, text="Fontes (Encoders)", bg='#2d2d2d', fg='white', font=('Segoe UI', 10, 'bold'))
        src_frame.pack(fill='both', expand=True, padx=5, pady=5)
        
        canvas = tk.Canvas(src_frame, bg='#2d2d2d', highlightthickness=0)
        scrollbar = ttk.Scrollbar(src_frame, orient='vertical', command=canvas.yview)
        self.src_container = tk.Frame(canvas, bg='#2d2d2d')
        
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.create_window((0, 0), window=self.src_container, anchor='nw')
        self.src_container.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        
        for enc in self.encoders.values():
            w = DraggableSource(self.src_container, enc, self)
            w.pack(fill='x', padx=5, pady=2)
            self.source_widgets[enc.id] = w
        
        # Controls
        ctrl_frame = tk.LabelFrame(left, text="Controles", bg='#2d2d2d', fg='white', font=('Segoe UI', 10, 'bold'))
        ctrl_frame.pack(fill='x', padx=5, pady=5)
        
        ttk.Button(ctrl_frame, text="Salvar Preset 1", command=lambda: self.save_preset(1)).pack(fill='x', padx=5, pady=2)
        ttk.Button(ctrl_frame, text="Salvar Preset 2", command=lambda: self.save_preset(2)).pack(fill='x', padx=5, pady=2)
        ttk.Button(ctrl_frame, text="Carregar Preset 1", command=lambda: self.load_preset(1)).pack(fill='x', padx=5, pady=2)
        ttk.Button(ctrl_frame, text="Carregar Preset 2", command=lambda: self.load_preset(2)).pack(fill='x', padx=5, pady=2)
        ttk.Separator(ctrl_frame).pack(fill='x', pady=5)
        ttk.Button(ctrl_frame, text="Criar Matriz", command=self.create_matrix).pack(fill='x', padx=5, pady=2)
        ttk.Button(ctrl_frame, text="Configurar Recorte", command=self.configure_crop_selected).pack(fill='x', padx=5, pady=2)
        ttk.Button(ctrl_frame, text="Limpar Tela", command=self.clear_all).pack(fill='x', padx=5, pady=2)
        ttk.Separator(ctrl_frame).pack(fill='x', pady=5)
        ttk.Button(ctrl_frame, text="Atualizar Status", command=self.refresh_status).pack(fill='x', padx=5, pady=2)
        ttk.Button(ctrl_frame, text="Ver Log", command=lambda: CommandLogDialog(self.root, self.avcit)).pack(fill='x', padx=5, pady=2)
        
        # Video Wall
        center = tk.Frame(main, bg='#1a1a2e')
        center.pack(side='left', fill='both', expand=True)
        
        self.wall_frame = tk.LabelFrame(center, text=f"Video Wall (4x14 = 56 monitores)", bg='#1a1a2e', fg='white', font=('Segoe UI', 10, 'bold'))
        self.wall_frame.pack(fill='both', expand=True)
        
        self.monitors_frame = tk.Frame(self.wall_frame, bg='#0d0d1a')
        self.monitors_frame.pack(fill='both', expand=True, padx=5, pady=5)
        self.create_monitor_grid()
        
        # Bottom
        bottom = tk.Frame(self.root, bg='#0d1b2a', height=25)
        bottom.pack(fill='x', side='bottom')
        self.info_lbl = tk.Label(bottom, text="Arraste fonte para monitor | Duplo clique = Recorte | Clique direito = Opcoes", bg='#0d1b2a', fg='#888', font=('Segoe UI', 9))
        self.info_lbl.pack(side='left', padx=20, pady=3)
    
    def create_monitor_grid(self):
        for w in self.monitors_frame.winfo_children():
            w.destroy()
        self.monitor_widgets.clear()
        
        for i in range(self.grid_cols):
            self.monitors_frame.columnconfigure(i, weight=1, uniform='col')
        for i in range(self.grid_rows):
            self.monitors_frame.rowconfigure(i, weight=1, uniform='row')
        
        for dec in self.decoders.values():
            row, col = dec.position
            if row < self.grid_rows and col < self.grid_cols:
                m = MonitorWidget(self.monitors_frame, dec, self)
                m.grid(row=row, column=col, sticky='nsew', padx=1, pady=1)
                self.monitor_widgets[dec.id] = m
    
    def start_drag(self, encoder):
        self.dragging_encoder = encoder
        self.drag_window = tk.Toplevel(self.root)
        self.drag_window.overrideredirect(True)
        self.drag_window.attributes('-alpha', 0.8)
        self.drag_window.attributes('-topmost', True)
        tk.Label(self.drag_window, text=f"{encoder.name}\n{encoder.ip}", bg='#4a9eff', fg='white', font=('Segoe UI', 10, 'bold'), padx=10, pady=5).pack()
    
    def drag_motion(self, x, y):
        if self.drag_window:
            self.drag_window.geometry(f"+{x+10}+{y+10}")
    
    def end_drag(self, x, y):
        if self.drag_window:
            self.drag_window.destroy()
            self.drag_window = None
        if not self.dragging_encoder:
            return
        
        target = self.root.winfo_containing(x, y)
        while target and not isinstance(target, MonitorWidget):
            target = target.master
        
        if isinstance(target, MonitorWidget):
            monitors = self.selected_monitors if self.selected_monitors else [target]
            for m in monitors:
                self.apply_source(m, self.dragging_encoder)
            self.clear_selection()
        
        self.dragging_encoder = None
    
    def apply_source(self, monitor, encoder):
        monitor.set_source(encoder)
        threading.Thread(target=self._send_switch, args=(monitor.decoder, encoder), daemon=True).start()
    
    def _send_switch(self, decoder, encoder):
        self.avcit.switch_source(decoder.ip, decoder.port, encoder.ip, encoder.port)
        if decoder.crop and decoder.crop.get('enabled'):
            crop = CropRegion.from_dict(decoder.crop)
            self.avcit.set_crop(decoder.ip, decoder.port, crop)
    
    def monitor_clicked(self, monitor):
        if monitor in self.selected_monitors:
            monitor.set_selected(False)
            self.selected_monitors.remove(monitor)
        else:
            monitor.set_selected(True)
            self.selected_monitors.append(monitor)
    
    def clear_selection(self):
        for m in self.selected_monitors:
            m.set_selected(False)
        self.selected_monitors.clear()
    
    def open_crop_dialog(self, monitor):
        if not monitor.decoder.current_source:
            messagebox.showwarning("Aviso", "Selecione uma fonte primeiro")
            return
        encoder = self.encoders.get(monitor.decoder.current_source)
        if not encoder:
            return
        
        def on_crop(crop):
            monitor.set_crop(crop)
            if crop:
                threading.Thread(target=self.avcit.set_crop, args=(monitor.decoder.ip, monitor.decoder.port, crop), daemon=True).start()
        
        CropSelectorDialog(self.root, encoder, monitor.decoder.get_crop_region(), on_crop)
    
    def configure_crop_selected(self):
        if not self.selected_monitors:
            messagebox.showwarning("Aviso", "Selecione monitores primeiro")
            return
        sources = set(m.decoder.current_source for m in self.selected_monitors if m.decoder.current_source)
        if len(sources) != 1:
            messagebox.showwarning("Aviso", "Monitores precisam ter a mesma fonte")
            return
        
        encoder = self.encoders.get(list(sources)[0])
        
        def on_crop(crop):
            for m in self.selected_monitors:
                m.set_crop(crop)
                if crop:
                    threading.Thread(target=self.avcit.set_crop, args=(m.decoder.ip, m.decoder.port, crop), daemon=True).start()
            self.clear_selection()
        
        CropSelectorDialog(self.root, encoder, None, on_crop)
    
    def create_matrix(self):
        if len(self.selected_monitors) < 2:
            messagebox.showwarning("Aviso", "Selecione 2+ monitores")
            return
        MatrixSelector(self.root, self._apply_matrix)
    
    def _apply_matrix(self, rows, cols, auto_crop):
        if len(self.selected_monitors) != rows * cols:
            messagebox.showwarning("Aviso", f"Selecione exatamente {rows*cols} monitores")
            return
        
        # Selecionar fonte
        dialog = tk.Toplevel(self.root)
        dialog.title("Fonte para Matriz")
        dialog.geometry("300x400")
        dialog.configure(bg='#2d2d2d')
        dialog.transient(self.root)
        dialog.grab_set()
        
        tk.Label(dialog, text="Selecione a fonte:", bg='#2d2d2d', fg='white').pack(pady=10)
        
        listbox = tk.Listbox(dialog, bg='#1a1a2e', fg='white', font=('Segoe UI', 10))
        listbox.pack(fill='both', expand=True, padx=10, pady=5)
        
        enc_list = list(self.encoders.values())
        for e in enc_list:
            listbox.insert('end', f"{e.name} ({e.ip})")
        
        def apply():
            sel = listbox.curselection()
            if not sel:
                return
            encoder = enc_list[sel[0]]
            sorted_monitors = sorted(self.selected_monitors, key=lambda m: (m.decoder.position[0], m.decoder.position[1]))
            
            tile_w = encoder.width // cols
            tile_h = encoder.height // rows
            
            for i, m in enumerate(sorted_monitors):
                row_idx, col_idx = i // cols, i % cols
                m.set_source(encoder)
                if auto_crop:
                    crop = CropRegion(x=col_idx*tile_w, y=row_idx*tile_h, width=tile_w, height=tile_h, source_width=encoder.width, source_height=encoder.height, enabled=True)
                    m.set_crop(crop)
                    threading.Thread(target=self._send_matrix, args=(m.decoder, encoder, crop), daemon=True).start()
                else:
                    threading.Thread(target=self._send_switch, args=(m.decoder, encoder), daemon=True).start()
            
            self.clear_selection()
            dialog.destroy()
            messagebox.showinfo("Sucesso", f"Matriz {rows}x{cols} criada!")
        
        ttk.Button(dialog, text="Aplicar", command=apply).pack(pady=10)
    
    def _send_matrix(self, decoder, encoder, crop):
        self.avcit.switch_source(decoder.ip, decoder.port, encoder.ip, encoder.port)
        time.sleep(0.1)
        self.avcit.set_crop(decoder.ip, decoder.port, crop)
    
    def save_preset(self, num):
        name = simpledialog.askstring("Salvar", f"Nome do Preset {num}:", initialvalue=f"Preset {num}")
        if not name:
            return
        mappings, crops = {}, {}
        for did, m in self.monitor_widgets.items():
            if m.decoder.current_source:
                mappings[did] = m.decoder.current_source
            if m.decoder.crop:
                crops[did] = m.decoder.crop
        self.presets[f"preset_{num}"] = Preset(id=f"preset_{num}", name=name, timestamp=datetime.now().isoformat(), mappings=mappings, matrices=[], crops=crops)
        self.save_config()
        messagebox.showinfo("Sucesso", f"Preset '{name}' salvo!")
    
    def load_preset(self, num):
        preset = self.presets.get(f"preset_{num}")
        if not preset:
            messagebox.showwarning("Aviso", f"Preset {num} nao encontrado")
            return
        
        for m in self.monitor_widgets.values():
            m.set_source(None)
            m.set_crop(None)
        
        for did, eid in preset.mappings.items():
            if did in self.monitor_widgets and eid in self.encoders:
                self.apply_source(self.monitor_widgets[did], self.encoders[eid])
        
        for did, crop_data in preset.crops.items():
            if did in self.monitor_widgets:
                m = self.monitor_widgets[did]
                crop = CropRegion.from_dict(crop_data)
                m.set_crop(crop)
                threading.Thread(target=self.avcit.set_crop, args=(m.decoder.ip, m.decoder.port, crop), daemon=True).start()
        
        messagebox.showinfo("Sucesso", f"Preset '{preset.name}' carregado!")
    
    def clear_all(self):
        if messagebox.askyesno("Confirmar", "Limpar tudo?"):
            for m in self.monitor_widgets.values():
                m.clear_source()
    
    def refresh_status(self):
        threading.Thread(target=self._refresh_status, daemon=True).start()
    
    def _refresh_status(self):
        for e in self.encoders.values():
            e.status = self.avcit.check_device_status(e.ip, e.port)
            self.root.after(0, lambda enc=e: self.source_widgets[enc.id].update_status(enc.status))
        for d in self.decoders.values():
            d.status = self.avcit.check_device_status(d.ip, d.port)
            if d.id in self.monitor_widgets:
                self.root.after(0, lambda dec=d: self.monitor_widgets[dec.id].update_status(dec.status))
    
    def monitor_devices(self):
        while self.monitoring:
            time.sleep(30)
            try:
                self._refresh_status()
            except:
                pass
    
    def update_mapping(self, did, eid):
        if did in self.decoders:
            self.decoders[did].current_source = eid
    
    def get_config_path(self):
        if getattr(sys, 'frozen', False):
            return os.path.join(os.path.dirname(sys.executable), 'videowall_config.json')
        return 'videowall_config.json'
    
    def save_config(self):
        config = {
            'credentials': {'username': self.avcit.username, 'password': self.avcit.password},
            'presets': {k: v.to_dict() for k, v in self.presets.items()}
        }
        try:
            with open(self.get_config_path(), 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except:
            pass
    
    def load_config(self):
        try:
            path = self.get_config_path()
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                if 'credentials' in config:
                    self.avcit.set_credentials(config['credentials'].get('username', 'admin'), config['credentials'].get('password', 'admin'))
                for pid, pdata in config.get('presets', {}).items():
                    self.presets[pid] = Preset.from_dict(pdata)
        except:
            pass
    
    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind('<Escape>', lambda e: self.clear_selection())
        self.root.mainloop()
    
    def on_close(self):
        self.monitoring = False
        self.save_config()
        self.root.destroy()


if __name__ == "__main__":
    app = VideoWallController()
    app.run()
