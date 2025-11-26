#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sistema de Gerenciamento de Video Wall - SALA NOC
Ministerio da Justica e Seguranca Publica
Versao com Telnet + Autenticacao
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import json
import socket
import threading
import time
import telnetlib
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
    port: int = 22
    rtsp_port: int = 551
    rtsp_port_preview: int = 2554
    description: str = ""
    status: str = "offline"
    width: int = 1920
    height: int = 1080
    
    def get_rtsp_url(self, resolution="1080"):
        if resolution == "4k":
            return f"rtsp://{self.ip}:551/2160"
        elif resolution == "preview":
            return f"rtsp://{self.ip}:2554/352"
        else:
            return f"rtsp://{self.ip}:551/2160"
    
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
    port: int = 23
    position: Tuple[int, int] = (0, 0)
    current_source: Optional[str] = None
    status: str = "offline"
    resolution: str = "1920x1080"
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
    """Controlador de comunicacao com dispositivos AVCIT via Telnet com autenticacao"""
    
    # Credenciais padrao para tentar
    DEFAULT_CREDENTIALS = [
        ("admin", "admin"),
        ("admin", ""),
        ("root", "root"),
        ("root", ""),
        ("admin", "123456"),
        ("admin", "password"),
        ("user", "user"),
        ("guest", "guest"),
    ]
    
    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout
        self.command_log = []
        # Credenciais configuradas (podem ser alteradas pelo usuario)
        self.username = "admin"
        self.password = "admin"
        # Cache de sessoes autenticadas
        self.authenticated_sessions = {}
    
    def log_command(self, target_ip, command, success, response=None):
        """Log de comandos para debug"""
        self.command_log.append({
            'timestamp': datetime.now().isoformat(),
            'target': target_ip,
            'command': command,
            'success': success,
            'response': response
        })
        if len(self.command_log) > 200:
            self.command_log.pop(0)
    
    def set_credentials(self, username, password):
        """Define credenciais para autenticacao"""
        self.username = username
        self.password = password
        self.authenticated_sessions.clear()
    
    def telnet_login(self, ip: str, port: int = 23, username: str = None, password: str = None):
        """Conecta via Telnet com autenticacao"""
        if username is None:
            username = self.username
        if password is None:
            password = self.password
        
        try:
            tn = telnetlib.Telnet(ip, port, timeout=self.timeout)
            
            # Aguarda prompt de login
            response = tn.read_until(b"login:", timeout=3).decode('utf-8', errors='ignore')
            
            # Envia usuario
            tn.write((username + "\n").encode('utf-8'))
            
            # Aguarda prompt de senha
            response += tn.read_until(b"assword:", timeout=3).decode('utf-8', errors='ignore')
            
            # Envia senha
            tn.write((password + "\n").encode('utf-8'))
            
            # Aguarda resposta (prompt de comando ou erro)
            time.sleep(0.5)
            response += tn.read_very_eager().decode('utf-8', errors='ignore')
            
            # Verifica se login foi bem sucedido
            if "incorrect" in response.lower() or "failed" in response.lower() or "denied" in response.lower():
                tn.close()
                self.log_command(ip, f"LOGIN {username}", False, "Credenciais incorretas")
                return None, response
            
            self.log_command(ip, f"LOGIN {username}", True, "Autenticado")
            return tn, response
            
        except Exception as e:
            self.log_command(ip, f"LOGIN {username}", False, str(e))
            return None, str(e)
    
    def try_default_credentials(self, ip: str, port: int = 23):
        """Tenta credenciais padrao ate encontrar uma que funcione"""
        for username, password in self.DEFAULT_CREDENTIALS:
            tn, response = self.telnet_login(ip, port, username, password)
            if tn:
                self.username = username
                self.password = password
                return tn, response, username, password
        return None, "Nenhuma credencial padrao funcionou", None, None
    
    def send_telnet_command_auth(self, ip: str, port: int, command: str):
        """Envia comando via Telnet com autenticacao"""
        try:
            tn, login_response = self.telnet_login(ip, port)
            if not tn:
                return None
            
            # Envia comando
            tn.write((command + "\n").encode('utf-8'))
            time.sleep(0.5)
            
            # Le resposta
            response = tn.read_very_eager().decode('utf-8', errors='ignore')
            
            tn.close()
            
            full_response = login_response + "\n" + response
            self.log_command(ip, command, True, response.strip()[:300])
            return full_response
            
        except Exception as e:
            self.log_command(ip, command, False, str(e))
            return None
    
    def send_telnet_command_simple(self, ip: str, port: int, command: str):
        """Envia comando via Telnet sem autenticacao (para dispositivos que nao requerem)"""
        try:
            tn = telnetlib.Telnet(ip, port, timeout=self.timeout)
            time.sleep(0.3)
            
            # Le qualquer prompt inicial
            try:
                initial = tn.read_very_eager().decode('utf-8', errors='ignore')
            except:
                initial = ""
            
            # Envia comando
            tn.write((command + "\n").encode('utf-8'))
            time.sleep(0.5)
            
            # Le resposta
            try:
                response = tn.read_very_eager().decode('utf-8', errors='ignore')
            except:
                response = ""
            
            tn.close()
            
            self.log_command(ip, command, True, (initial + response).strip()[:300])
            return initial + response
            
        except Exception as e:
            self.log_command(ip, command, False, str(e))
            return None
    
    def discover_commands_auth(self, ip: str, port: int = 23):
        """Descobre comandos disponiveis apos autenticacao"""
        results = []
        
        try:
            tn, login_response = self.telnet_login(ip, port)
            if not tn:
                return [("LOGIN", f"Falha: {login_response}")]
            
            results.append(("LOGIN", f"OK - Prompt: {login_response[-200:]}"))
            
            # Comandos para descoberta
            discovery_commands = [
                "help",
                "?",
                "list",
                "status",
                "show",
                "info",
                "version",
                "get",
                "commands",
            ]
            
            for cmd in discovery_commands:
                tn.write((cmd + "\n").encode('utf-8'))
                time.sleep(0.5)
                try:
                    response = tn.read_very_eager().decode('utf-8', errors='ignore')
                    if response.strip():
                        results.append((cmd, response.strip()[:500]))
                except:
                    pass
            
            tn.close()
            
        except Exception as e:
            results.append(("ERRO", str(e)))
        
        return results
    
    def switch_source_auth(self, decoder_ip: str, decoder_port: int, encoder_ip: str):
        """Troca fonte via Telnet com autenticacao"""
        
        try:
            tn, login_response = self.telnet_login(decoder_ip, decoder_port)
            if not tn:
                return False, "LOGIN", login_response
            
            # Comandos para tentar
            commands = [
                f"switch {encoder_ip}",
                f"SWITCH {encoder_ip}",
                f"set source {encoder_ip}",
                f"source {encoder_ip}",
                f"connect {encoder_ip}",
                f"play rtsp://{encoder_ip}:551/2160",
                f"url rtsp://{encoder_ip}:551/2160",
                f"stream {encoder_ip}",
                f"input {encoder_ip}",
                f"route {encoder_ip}",
                f"link {encoder_ip}",
            ]
            
            for cmd in commands:
                tn.write((cmd + "\n").encode('utf-8'))
                time.sleep(0.3)
                try:
                    response = tn.read_very_eager().decode('utf-8', errors='ignore')
                    self.log_command(decoder_ip, cmd, True, response.strip()[:200])
                    
                    response_upper = response.upper()
                    if any(word in response_upper for word in ["OK", "SUCCESS", "DONE", "ACCEPTED"]):
                        tn.close()
                        return True, cmd, response
                except:
                    pass
            
            tn.close()
            return False, None, "Nenhum comando funcionou"
            
        except Exception as e:
            return False, None, str(e)
    
    def set_crop_auth(self, decoder_ip: str, decoder_port: int, crop):
        """Configura crop via Telnet com autenticacao"""
        
        try:
            tn, login_response = self.telnet_login(decoder_ip, decoder_port)
            if not tn:
                return False, "LOGIN", login_response
            
            commands = [
                f"crop {crop.x} {crop.y} {crop.width} {crop.height}",
                f"window {crop.x} {crop.y} {crop.width} {crop.height}",
                f"set crop {crop.x},{crop.y},{crop.width},{crop.height}",
                f"region {crop.x} {crop.y} {crop.width} {crop.height}",
                f"zoom {crop.x} {crop.y} {crop.width} {crop.height}",
            ]
            
            for cmd in commands:
                tn.write((cmd + "\n").encode('utf-8'))
                time.sleep(0.3)
                try:
                    response = tn.read_very_eager().decode('utf-8', errors='ignore')
                    self.log_command(decoder_ip, cmd, True, response.strip()[:200])
                    
                    if "OK" in response.upper() or "SUCCESS" in response.upper():
                        tn.close()
                        return True, cmd, response
                except:
                    pass
            
            tn.close()
            return False, None, "Nenhum comando de crop funcionou"
            
        except Exception as e:
            return False, None, str(e)
    
    def switch_source(self, decoder_ip, decoder_port, encoder_ip, encoder_port):
        """Metodo principal para trocar fonte"""
        success, cmd, response = self.switch_source_auth(decoder_ip, decoder_port, encoder_ip)
        return success
    
    def set_crop(self, decoder_ip, decoder_port, crop):
        """Metodo principal para configurar crop"""
        success, cmd, response = self.set_crop_auth(decoder_ip, decoder_port, crop)
        return success
    
    def clear_crop(self, decoder_ip, decoder_port):
        """Limpa crop"""
        try:
            tn, _ = self.telnet_login(decoder_ip, decoder_port)
            if not tn:
                return False
            
            commands = ["crop reset", "window reset", "zoom reset", "fullscreen", "crop 0 0 1920 1080"]
            for cmd in commands:
                tn.write((cmd + "\n").encode('utf-8'))
                time.sleep(0.2)
            
            tn.close()
            return True
        except:
            return False
    
    def ping_device(self, ip, port):
        """Verifica se dispositivo esta online"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                result = sock.connect_ex((ip, port))
                return result == 0
        except:
            return False
    
    def check_device_status(self, ip, port=23):
        """Verifica status do dispositivo"""
        return "online" if self.ping_device(ip, port) else "offline"


class CredentialsDialog(tk.Toplevel):
    """Dialogo para configurar credenciais de acesso"""
    
    def __init__(self, parent, avcit_controller, callback=None):
        super().__init__(parent)
        self.avcit = avcit_controller
        self.callback = callback
        
        self.title("Configurar Credenciais AVCIT")
        self.geometry("450x350")
        self.configure(bg='#2d2d2d')
        self.transient(parent)
        self.grab_set()
        
        main_frame = tk.Frame(self, bg='#2d2d2d')
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        tk.Label(main_frame, text="Credenciais de Acesso Telnet", 
                 bg='#2d2d2d', fg='white', font=('Segoe UI', 14, 'bold')).pack(pady=10)
        
        tk.Label(main_frame, text="Os decoders AVCIT requerem autenticacao.\nConfigure o usuario e senha abaixo:",
                 bg='#2d2d2d', fg='#888888', font=('Segoe UI', 10), justify='center').pack(pady=5)
        
        # Frame de entrada
        input_frame = tk.Frame(main_frame, bg='#2d2d2d')
        input_frame.pack(pady=20)
        
        tk.Label(input_frame, text="Usuario:", bg='#2d2d2d', fg='white', 
                 font=('Segoe UI', 10)).grid(row=0, column=0, sticky='e', padx=10, pady=10)
        self.user_entry = ttk.Entry(input_frame, width=25, font=('Segoe UI', 10))
        self.user_entry.insert(0, avcit_controller.username)
        self.user_entry.grid(row=0, column=1, padx=10, pady=10)
        
        tk.Label(input_frame, text="Senha:", bg='#2d2d2d', fg='white',
                 font=('Segoe UI', 10)).grid(row=1, column=0, sticky='e', padx=10, pady=10)
        self.pass_entry = ttk.Entry(input_frame, width=25, show="*", font=('Segoe UI', 10))
        self.pass_entry.insert(0, avcit_controller.password)
        self.pass_entry.grid(row=1, column=1, padx=10, pady=10)
        
        # Botao para mostrar senha
        self.show_pass = tk.BooleanVar(value=False)
        ttk.Checkbutton(input_frame, text="Mostrar senha", variable=self.show_pass,
                       command=self.toggle_password).grid(row=2, column=1, sticky='w', padx=10)
        
        # Status
        self.status_label = tk.Label(main_frame, text="", bg='#2d2d2d', fg='#ffaa00', font=('Segoe UI', 10))
        self.status_label.pack(pady=5)
        
        # Botoes
        btn_frame = tk.Frame(main_frame, bg='#2d2d2d')
        btn_frame.pack(pady=15)
        
        ttk.Button(btn_frame, text="Testar Credenciais", command=self.test_credentials).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Tentar Padrao", command=self.try_default).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Salvar", command=self.save).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy).pack(side='left', padx=5)
    
    def toggle_password(self):
        self.pass_entry.configure(show="" if self.show_pass.get() else "*")
    
    def test_credentials(self):
        user = self.user_entry.get()
        password = self.pass_entry.get()
        
        self.status_label.configure(text="Testando...", fg='#ffaa00')
        self.update()
        
        # Testa no primeiro decoder
        test_ip = "172.16.207.11"
        tn, response = self.avcit.telnet_login(test_ip, 23, user, password)
        
        if tn:
            tn.close()
            self.status_label.configure(text=f"SUCESSO! Conectado em {test_ip}", fg='#00ff00')
        else:
            self.status_label.configure(text=f"FALHA: {response[:50]}", fg='#ff0000')
    
    def try_default(self):
        self.status_label.configure(text="Testando credenciais padrao...", fg='#ffaa00')
        self.update()
        
        test_ip = "172.16.207.11"
        tn, response, user, password = self.avcit.try_default_credentials(test_ip, 23)
        
        if tn:
            tn.close()
            self.user_entry.delete(0, tk.END)
            self.user_entry.insert(0, user)
            self.pass_entry.delete(0, tk.END)
            self.pass_entry.insert(0, password)
            self.status_label.configure(text=f"Encontrado: {user}/{password}", fg='#00ff00')
        else:
            self.status_label.configure(text="Nenhuma credencial padrao funcionou", fg='#ff0000')
    
    def save(self):
        user = self.user_entry.get()
        password = self.pass_entry.get()
        self.avcit.set_credentials(user, password)
        if self.callback:
            self.callback(user, password)
        self.destroy()


class DiscoveryDialog(tk.Toplevel):
    """Dialogo para descoberta de comandos do dispositivo"""
    
    def __init__(self, parent, avcit_controller, device_ip, device_port=23):
        super().__init__(parent)
        self.avcit = avcit_controller
        self.device_ip = device_ip
        self.device_port = device_port
        
        self.title(f"Descoberta de Comandos - {device_ip}")
        self.geometry("750x550")
        self.configure(bg='#2d2d2d')
        self.transient(parent)
        
        main_frame = tk.Frame(self, bg='#2d2d2d')
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Info
        info_frame = tk.Frame(main_frame, bg='#2d2d2d')
        info_frame.pack(fill='x')
        
        tk.Label(info_frame, text=f"Dispositivo: {device_ip}:{device_port}",
                 bg='#2d2d2d', fg='white', font=('Segoe UI', 12, 'bold')).pack(side='left')
        tk.Label(info_frame, text=f"Usuario: {self.avcit.username}",
                 bg='#2d2d2d', fg='#4a9eff', font=('Segoe UI', 10)).pack(side='right', padx=10)
        
        # Frame para comando manual
        cmd_frame = tk.Frame(main_frame, bg='#2d2d2d')
        cmd_frame.pack(fill='x', pady=10)
        
        tk.Label(cmd_frame, text="Comando:", bg='#2d2d2d', fg='white').pack(side='left', padx=5)
        self.cmd_entry = ttk.Entry(cmd_frame, width=50)
        self.cmd_entry.pack(side='left', padx=5)
        ttk.Button(cmd_frame, text="Enviar", command=self.send_manual_command).pack(side='left', padx=5)
        
        # Text area
        text_frame = tk.Frame(main_frame, bg='#2d2d2d')
        text_frame.pack(fill='both', expand=True, pady=10)
        
        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side='right', fill='y')
        
        self.text = tk.Text(text_frame, bg='#1a1a2e', fg='white', font=('Consolas', 10),
                           yscrollcommand=scrollbar.set)
        self.text.pack(fill='both', expand=True)
        scrollbar.config(command=self.text.yview)
        
        # Botoes
        btn_frame = tk.Frame(main_frame, bg='#2d2d2d')
        btn_frame.pack(fill='x', pady=10)
        
        ttk.Button(btn_frame, text="Testar Login", command=self.test_login).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Descobrir Comandos", command=self.run_discovery).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Testar Switch", command=self.test_switch).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Limpar", command=self.clear_text).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Fechar", command=self.destroy).pack(side='right', padx=5)
        
        self.cmd_entry.bind('<Return>', lambda e: self.send_manual_command())
    
    def log(self, text):
        self.text.insert('end', text + "\n")
        self.text.see('end')
        self.update()
    
    def clear_text(self):
        self.text.delete('1.0', tk.END)
    
    def test_login(self):
        self.log("\n" + "="*50)
        self.log(f"TESTANDO LOGIN em {self.device_ip}...")
        self.log(f"Usuario: {self.avcit.username}")
        self.log("="*50)
        
        tn, response = self.avcit.telnet_login(self.device_ip, self.device_port)
        
        if tn:
            self.log("\nLOGIN BEM SUCEDIDO!")
            self.log(f"Resposta:\n{response}")
            
            # Tenta ler mais
            try:
                tn.write(b"\n")
                time.sleep(0.3)
                more = tn.read_very_eager().decode('utf-8', errors='ignore')
                if more.strip():
                    self.log(f"\nPrompt:\n{more}")
            except:
                pass
            
            tn.close()
        else:
            self.log(f"\nFALHA NO LOGIN: {response}")
    
    def send_manual_command(self):
        cmd = self.cmd_entry.get().strip()
        if not cmd:
            return
        
        self.log(f"\n>>> Enviando: {cmd}")
        response = self.avcit.send_telnet_command_auth(self.device_ip, self.device_port, cmd)
        if response:
            self.log(f"<<< Resposta:\n{response}")
        else:
            self.log("<<< Sem resposta ou erro")
        
        self.cmd_entry.delete(0, tk.END)
    
    def run_discovery(self):
        self.log("\n" + "="*50)
        self.log("DESCOBRINDO COMANDOS (com autenticacao)...")
        self.log("="*50)
        
        results = self.avcit.discover_commands_auth(self.device_ip, self.device_port)
        
        for cmd, response in results:
            self.log(f"\n>>> {cmd}")
            self.log(f"<<< {response[:400]}")
    
    def test_switch(self):
        self.log("\n" + "="*50)
        self.log("TESTANDO SWITCH...")
        self.log("="*50)
        
        test_encoder = "172.16.207.75"
        success, cmd, response = self.avcit.switch_source_auth(self.device_ip, self.device_port, test_encoder)
        
        if success:
            self.log(f"\nSUCESSO! Comando: {cmd}")
        else:
            self.log(f"\nFalha. Resposta: {response}")


class CropSelectorDialog(tk.Toplevel):
    """Dialogo para selecao interativa de regiao de recorte"""
    
    def __init__(self, parent, encoder, current_crop, callback):
        super().__init__(parent)
        self.encoder = encoder
        self.callback = callback
        self.current_crop = current_crop
        
        self.title(f"Configurar Recorte - {encoder.name}")
        self.geometry("900x750")
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
        
        info_frame = tk.Frame(main_frame, bg='#2d2d2d')
        info_frame.pack(fill='x', pady=5)
        
        tk.Label(info_frame, text=f"Fonte: {self.encoder.name}", bg='#2d2d2d', fg='white', font=('Segoe UI', 12, 'bold')).pack(side='left', padx=5)
        tk.Label(info_frame, text=f"IP: {self.encoder.ip}", bg='#2d2d2d', fg='#4a9eff', font=('Segoe UI', 10)).pack(side='left', padx=10)
        tk.Label(info_frame, text=f"Resolucao: {self.source_width}x{self.source_height}", bg='#2d2d2d', fg='#888888', font=('Segoe UI', 10)).pack(side='left', padx=10)
        
        tk.Label(main_frame, text="Clique e arraste para selecionar a regiao de recorte", bg='#2d2d2d', fg='#888888', font=('Segoe UI', 10)).pack(pady=2)
        
        canvas_frame = tk.Frame(main_frame, bg='#1a1a2e', relief='solid', bd=2)
        canvas_frame.pack(pady=10)
        
        self.canvas = tk.Canvas(canvas_frame, width=self.display_width, height=self.display_height, bg='#0f0f23', highlightthickness=0)
        self.canvas.pack()
        
        self.draw_grid()
        self.draw_source_representation()
        
        self.canvas.bind('<Button-1>', self.on_mouse_down)
        self.canvas.bind('<B1-Motion>', self.on_mouse_drag)
        self.canvas.bind('<ButtonRelease-1>', self.on_mouse_up)
        
        controls_frame = tk.Frame(main_frame, bg='#2d2d2d')
        controls_frame.pack(fill='x', pady=10)
        
        input_frame = tk.Frame(controls_frame, bg='#2d2d2d')
        input_frame.pack()
        
        tk.Label(input_frame, text="X:", bg='#2d2d2d', fg='white').grid(row=0, column=0, padx=5, pady=5)
        self.x_var = tk.StringVar(value="0")
        ttk.Entry(input_frame, textvariable=self.x_var, width=8).grid(row=0, column=1, padx=5, pady=5)
        
        tk.Label(input_frame, text="Y:", bg='#2d2d2d', fg='white').grid(row=0, column=2, padx=5, pady=5)
        self.y_var = tk.StringVar(value="0")
        ttk.Entry(input_frame, textvariable=self.y_var, width=8).grid(row=0, column=3, padx=5, pady=5)
        
        tk.Label(input_frame, text="Largura:", bg='#2d2d2d', fg='white').grid(row=0, column=4, padx=5, pady=5)
        self.w_var = tk.StringVar(value=str(self.source_width))
        ttk.Entry(input_frame, textvariable=self.w_var, width=8).grid(row=0, column=5, padx=5, pady=5)
        
        tk.Label(input_frame, text="Altura:", bg='#2d2d2d', fg='white').grid(row=0, column=6, padx=5, pady=5)
        self.h_var = tk.StringVar(value=str(self.source_height))
        ttk.Entry(input_frame, textvariable=self.h_var, width=8).grid(row=0, column=7, padx=5, pady=5)
        
        ttk.Button(input_frame, text="Aplicar Valores", command=self.update_selection_from_values).grid(row=0, column=8, padx=10)
        
        presets_frame = tk.LabelFrame(controls_frame, text="Presets de Recorte", bg='#2d2d2d', fg='white')
        presets_frame.pack(fill='x', pady=10, padx=20)
        
        presets_inner = tk.Frame(presets_frame, bg='#2d2d2d')
        presets_inner.pack(pady=5)
        
        preset_buttons = [
            ("Tela Cheia", self.preset_full), ("Centro 50%", self.preset_center_50),
            ("Quadrante Sup.Esq.", self.preset_top_left), ("Quadrante Sup.Dir.", self.preset_top_right),
            ("Quadrante Inf.Esq.", self.preset_bottom_left), ("Quadrante Inf.Dir.", self.preset_bottom_right),
            ("Metade Esquerda", self.preset_left_half), ("Metade Direita", self.preset_right_half),
            ("Metade Superior", self.preset_top_half), ("Metade Inferior", self.preset_bottom_half),
            ("Terco Central", self.preset_center_third), ("16:9 Central", self.preset_16_9_center),
        ]
        
        for i, (text, command) in enumerate(preset_buttons):
            ttk.Button(presets_inner, text=text, command=command, width=16).grid(row=i//4, column=i%4, padx=3, pady=3)
        
        self.info_label = tk.Label(main_frame, text="Nenhuma regiao selecionada", bg='#2d2d2d', fg='#4a9eff', font=('Segoe UI', 10))
        self.info_label.pack(pady=5)
        
        btn_frame = tk.Frame(main_frame, bg='#2d2d2d')
        btn_frame.pack(pady=10)
        
        ttk.Button(btn_frame, text="Aplicar Recorte", command=self.apply_crop).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Limpar Recorte", command=self.clear_crop).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy).pack(side='left', padx=5)
    
    def draw_grid(self):
        for i in range(1, 4):
            x = i * self.display_width // 4
            self.canvas.create_line(x, 0, x, self.display_height, fill='#333333', dash=(2, 4))
        for i in range(1, 4):
            y = i * self.display_height // 4
            self.canvas.create_line(0, y, self.display_width, y, fill='#333333', dash=(2, 4))
        cx = self.display_width // 2
        self.canvas.create_line(cx, 0, cx, self.display_height, fill='#444444', width=1)
        cy = self.display_height // 2
        self.canvas.create_line(0, cy, self.display_width, cy, fill='#444444', width=1)
    
    def draw_source_representation(self):
        self.canvas.create_rectangle(2, 2, self.display_width - 2, self.display_height - 2, outline='#4a9eff', width=2)
        self.canvas.create_text(self.display_width // 2, self.display_height // 2,
                                text=f"{self.encoder.name}\n{self.source_width}x{self.source_height}\n{self.encoder.ip}",
                                fill='#666666', font=('Segoe UI', 14), justify='center')
    
    def load_existing_crop(self, crop):
        self.x_var.set(str(crop.x))
        self.y_var.set(str(crop.y))
        self.w_var.set(str(crop.width))
        self.h_var.set(str(crop.height))
        self.update_selection_from_values()
    
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
        self.update_values_from_selection(x1, y1, x2, y2)
    
    def on_mouse_up(self, event):
        if not self.selection_start:
            return
        x1, y1 = self.selection_start
        x2 = max(0, min(event.x, self.display_width))
        y2 = max(0, min(event.y, self.display_height))
        if x1 > x2: x1, x2 = x2, x1
        if y1 > y2: y1, y2 = y2, y1
        self.update_values_from_selection(x1, y1, x2, y2)
        if self.current_rect_id:
            self.canvas.delete(self.current_rect_id)
        self.current_rect_id = self.canvas.create_rectangle(x1, y1, x2, y2, outline='#00ff00', width=3)
        self.selection_start = None
    
    def update_values_from_selection(self, x1, y1, x2, y2):
        if x1 > x2: x1, x2 = x2, x1
        if y1 > y2: y1, y2 = y2, y1
        real_x = max(0, min(int(x1 / self.scale_x), self.source_width - 1))
        real_y = max(0, min(int(y1 / self.scale_y), self.source_height - 1))
        real_w = max(1, min(int((x2 - x1) / self.scale_x), self.source_width - real_x))
        real_h = max(1, min(int((y2 - y1) / self.scale_y), self.source_height - real_y))
        self.x_var.set(str(real_x))
        self.y_var.set(str(real_y))
        self.w_var.set(str(real_w))
        self.h_var.set(str(real_h))
        percentage = (real_w * real_h) / (self.source_width * self.source_height) * 100
        self.info_label.configure(text=f"Regiao: ({real_x}, {real_y}) - {real_w}x{real_h} pixels ({percentage:.1f}% da fonte)")
    
    def update_selection_from_values(self):
        try:
            x, y = int(self.x_var.get()), int(self.y_var.get())
            w, h = int(self.w_var.get()), int(self.h_var.get())
            cx1, cy1 = int(x * self.scale_x), int(y * self.scale_y)
            cx2, cy2 = int((x + w) * self.scale_x), int((y + h) * self.scale_y)
            self.canvas.delete('all')
            self.draw_grid()
            self.draw_source_representation()
            self.current_rect_id = self.canvas.create_rectangle(cx1, cy1, cx2, cy2, outline='#00ff00', width=3)
            percentage = (w * h) / (self.source_width * self.source_height) * 100
            self.info_label.configure(text=f"Regiao: ({x}, {y}) - {w}x{h} pixels ({percentage:.1f}% da fonte)")
        except ValueError:
            pass
    
    def set_crop_values(self, x, y, w, h):
        self.x_var.set(str(x))
        self.y_var.set(str(y))
        self.w_var.set(str(w))
        self.h_var.set(str(h))
        self.update_selection_from_values()
    
    def preset_full(self): self.set_crop_values(0, 0, self.source_width, self.source_height)
    def preset_center_50(self):
        w, h = self.source_width // 2, self.source_height // 2
        self.set_crop_values((self.source_width - w) // 2, (self.source_height - h) // 2, w, h)
    def preset_top_left(self): self.set_crop_values(0, 0, self.source_width // 2, self.source_height // 2)
    def preset_top_right(self): self.set_crop_values(self.source_width // 2, 0, self.source_width // 2, self.source_height // 2)
    def preset_bottom_left(self): self.set_crop_values(0, self.source_height // 2, self.source_width // 2, self.source_height // 2)
    def preset_bottom_right(self): self.set_crop_values(self.source_width // 2, self.source_height // 2, self.source_width // 2, self.source_height // 2)
    def preset_left_half(self): self.set_crop_values(0, 0, self.source_width // 2, self.source_height)
    def preset_right_half(self): self.set_crop_values(self.source_width // 2, 0, self.source_width // 2, self.source_height)
    def preset_top_half(self): self.set_crop_values(0, 0, self.source_width, self.source_height // 2)
    def preset_bottom_half(self): self.set_crop_values(0, self.source_height // 2, self.source_width, self.source_height // 2)
    def preset_center_third(self):
        w, h = self.source_width // 3, self.source_height // 3
        self.set_crop_values((self.source_width - w) // 2, (self.source_height - h) // 2, w, h)
    def preset_16_9_center(self):
        target_ratio = 16 / 9
        if self.source_width / self.source_height > target_ratio:
            h = self.source_height
            w = int(h * target_ratio)
        else:
            w = self.source_width
            h = int(w / target_ratio)
        self.set_crop_values((self.source_width - w) // 2, (self.source_height - h) // 2, w, h)
    
    def apply_crop(self):
        try:
            crop = CropRegion(x=int(self.x_var.get()), y=int(self.y_var.get()),
                width=int(self.w_var.get()), height=int(self.h_var.get()),
                source_width=self.source_width, source_height=self.source_height, enabled=True)
            if crop.x < 0 or crop.y < 0: raise ValueError("Posicao negativa")
            if crop.width <= 0 or crop.height <= 0: raise ValueError("Dimensoes invalidas")
            if crop.x + crop.width > self.source_width or crop.y + crop.height > self.source_height: raise ValueError("Excede limites")
            self.callback(crop)
            self.destroy()
        except ValueError as e:
            messagebox.showerror("Erro", f"Valores invalidos: {e}")
    
    def clear_crop(self):
        self.callback(None)
        self.destroy()


class CommandLogDialog(tk.Toplevel):
    def __init__(self, parent, avcit_controller):
        super().__init__(parent)
        self.avcit = avcit_controller
        self.title("Log de Comandos AVCIT")
        self.geometry("950x600")
        self.configure(bg='#2d2d2d')
        self.transient(parent)
        
        text_frame = tk.Frame(self, bg='#2d2d2d')
        text_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side='right', fill='y')
        
        self.text = tk.Text(text_frame, bg='#1a1a2e', fg='white', font=('Consolas', 10), yscrollcommand=scrollbar.set)
        self.text.pack(fill='both', expand=True)
        scrollbar.config(command=self.text.yview)
        
        btn_frame = tk.Frame(self, bg='#2d2d2d')
        btn_frame.pack(fill='x', pady=10)
        
        ttk.Button(btn_frame, text="Atualizar", command=self.refresh).pack(side='left', padx=10)
        ttk.Button(btn_frame, text="Limpar", command=self.clear_log).pack(side='left', padx=10)
        ttk.Button(btn_frame, text="Fechar", command=self.destroy).pack(side='right', padx=10)
        
        self.refresh()
    
    def refresh(self):
        self.text.delete('1.0', tk.END)
        for entry in reversed(self.avcit.command_log):
            status = "OK" if entry['success'] else "FALHA"
            line = f"[{entry['timestamp']}] {entry['target']} - {entry['command'].strip()}\n"
            line += f"  Status: {status} | Resposta: {entry['response']}\n\n"
            self.text.insert('end', line)
    
    def clear_log(self):
        self.avcit.command_log.clear()
        self.refresh()


class DraggableSource(tk.Frame):
    def __init__(self, parent, encoder, controller, **kwargs):
        super().__init__(parent, **kwargs)
        self.encoder = encoder
        self.controller = controller
        self.configure(bg='#2d2d2d', relief='raised', bd=2, cursor='hand2')
        
        self.content_frame = tk.Frame(self, bg='#2d2d2d')
        self.content_frame.pack(fill='both', expand=True, padx=2, pady=2)
        
        status_color = '#00ff00' if encoder.status == 'online' else '#ff0000'
        self.status_indicator = tk.Canvas(self.content_frame, width=10, height=10, bg='#2d2d2d', highlightthickness=0)
        self.status_indicator.create_oval(2, 2, 8, 8, fill=status_color, outline='')
        self.status_indicator.pack(side='left', padx=2)
        
        self.name_label = tk.Label(self.content_frame, text=encoder.name, bg='#2d2d2d', fg='white', font=('Segoe UI', 9, 'bold'))
        self.name_label.pack(side='left', padx=5)
        
        self.ip_label = tk.Label(self.content_frame, text=encoder.ip, bg='#2d2d2d', fg='#888888', font=('Segoe UI', 8))
        self.ip_label.pack(side='right', padx=5)
        
        for widget in [self, self.content_frame, self.name_label, self.ip_label, self.status_indicator]:
            widget.bind('<Button-1>', self.on_drag_start)
            widget.bind('<B1-Motion>', self.on_drag_motion)
            widget.bind('<ButtonRelease-1>', self.on_drag_release)
    
    def on_drag_start(self, event): self.controller.start_drag(self.encoder)
    def on_drag_motion(self, event): self.controller.drag_motion(event.x_root, event.y_root)
    def on_drag_release(self, event): self.controller.end_drag(event.x_root, event.y_root)
    
    def update_status(self, status):
        self.encoder.status = status
        color = '#00ff00' if status == 'online' else '#ff0000'
        self.status_indicator.delete('all')
        self.status_indicator.create_oval(2, 2, 8, 8, fill=color, outline='')


class MonitorWidget(tk.Frame):
    def __init__(self, parent, decoder, controller, **kwargs):
        super().__init__(parent, **kwargs)
        self.decoder = decoder
        self.controller = controller
        self.selected = False
        self.has_crop = False
        
        self.configure(bg='#1a1a2e', relief='solid', bd=1, cursor='crosshair')
        
        self.inner_frame = tk.Frame(self, bg='#1a1a2e')
        self.inner_frame.pack(fill='both', expand=True, padx=1, pady=1)
        
        self.canvas = tk.Canvas(self.inner_frame, bg='#0f0f23', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        
        self.id_label = tk.Label(self.canvas, text=decoder.name, bg='#0f0f23', fg='#4a9eff', font=('Segoe UI', 7, 'bold'))
        self.id_label.place(x=2, y=2)
        
        self.source_label = tk.Label(self.canvas, text="Sem fonte", bg='#0f0f23', fg='#888888', font=('Segoe UI', 6))
        self.source_label.place(relx=0.5, rely=0.4, anchor='center')
        
        self.crop_label = tk.Label(self.canvas, text="", bg='#0f0f23', fg='#ffaa00', font=('Segoe UI', 5))
        self.crop_label.place(relx=0.5, rely=0.65, anchor='center')
        
        self.status_label = tk.Label(self.canvas, text="*", bg='#0f0f23', fg='#ff0000', font=('Segoe UI', 8))
        self.status_label.place(relx=1.0, x=-12, y=2)
        
        self.crop_icon = tk.Label(self.canvas, text="", bg='#0f0f23', fg='#ffaa00', font=('Segoe UI', 8))
        self.crop_icon.place(x=2, rely=1.0, y=-15)
        
        for widget in [self, self.canvas, self.id_label, self.source_label, self.crop_label]:
            widget.bind('<Button-1>', self.on_click)
            widget.bind('<Button-3>', self.on_right_click)
            widget.bind('<Double-Button-1>', self.on_double_click)
        
        self.bind('<Enter>', self.on_enter)
        self.bind('<Leave>', self.on_leave)
        
        if decoder.crop: self.update_crop_display()
    
    def on_click(self, event): self.controller.monitor_clicked(self)
    def on_double_click(self, event):
        if self.decoder.current_source: self.controller.open_crop_dialog(self)
    
    def on_right_click(self, event):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Configurar Recorte...", command=self.open_crop_config)
        menu.add_command(label="Limpar Recorte", command=self.clear_crop)
        menu.add_separator()
        menu.add_command(label="Limpar fonte", command=self.clear_source)
        menu.add_command(label="Propriedades", command=self.show_properties)
        menu.add_separator()
        menu.add_command(label="Descobrir Comandos", command=self.open_discovery)
        menu.tk_popup(event.x_root, event.y_root)
    
    def on_enter(self, event):
        if not self.selected: self.configure(bg='#2a2a4e')
    def on_leave(self, event):
        if not self.selected: self.configure(bg='#1a1a2e')
    
    def open_crop_config(self):
        if self.decoder.current_source: self.controller.open_crop_dialog(self)
        else: messagebox.showwarning("Aviso", "Selecione uma fonte primeiro")
    
    def open_discovery(self):
        DiscoveryDialog(self.controller.root, self.controller.avcit, self.decoder.ip, self.decoder.port)
    
    def clear_crop(self):
        self.decoder.set_crop_region(None)
        self.has_crop = False
        self.update_crop_display()
        threading.Thread(target=self.controller.avcit.clear_crop, args=(self.decoder.ip, self.decoder.port), daemon=True).start()
    
    def clear_source(self):
        self.set_source(None)
        self.clear_crop()
        self.controller.update_mapping(self.decoder.id, None)
    
    def show_properties(self):
        crop_info = "Nenhum"
        if self.decoder.crop:
            c = self.decoder.crop
            crop_info = f"({c['x']}, {c['y']}) - {c['width']}x{c['height']}"
        info = f"Decoder: {self.decoder.name}\nIP: {self.decoder.ip}\nPorta: {self.decoder.port}\nStatus: {self.decoder.status}\nFonte: {self.decoder.current_source or 'Nenhuma'}\nRecorte: {crop_info}"
        messagebox.showinfo("Propriedades", info)
    
    def set_source(self, encoder):
        if encoder:
            self.decoder.current_source = encoder.id
            self.source_label.configure(text=encoder.name, fg='#00ff00')
            self.canvas.configure(bg='#1a2a1a')
        else:
            self.decoder.current_source = None
            self.source_label.configure(text="Sem fonte", fg='#888888')
            self.canvas.configure(bg='#0f0f23')
    
    def set_crop(self, crop):
        self.decoder.set_crop_region(crop)
        self.has_crop = crop is not None and crop.enabled
        self.update_crop_display()
    
    def update_crop_display(self):
        if self.decoder.crop and self.decoder.crop.get('enabled', False):
            c = self.decoder.crop
            self.crop_label.configure(text=f"[{c['width']}x{c['height']}]", fg='#ffaa00')
            self.crop_icon.configure(text="[C]")
            self.has_crop = True
        else:
            self.crop_label.configure(text="")
            self.crop_icon.configure(text="")
            self.has_crop = False
    
    def set_selected(self, selected):
        self.selected = selected
        self.configure(bg='#4a4a8e' if selected else '#1a1a2e', relief='solid', bd=2 if selected else 1)
    
    def update_status(self, status):
        self.decoder.status = status
        self.status_label.configure(fg='#00ff00' if status == 'online' else '#ff0000')


class MatrixSelector(tk.Toplevel):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("Selecionar Matriz")
        self.geometry("350x280")
        self.configure(bg='#2d2d2d')
        self.transient(parent)
        self.grab_set()
        
        main_frame = tk.Frame(self, bg='#2d2d2d')
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        tk.Label(main_frame, text="Configuracao de Matriz", bg='#2d2d2d', fg='white', font=('Segoe UI', 12, 'bold')).pack(pady=10)
        
        spin_frame = tk.Frame(main_frame, bg='#2d2d2d')
        spin_frame.pack(pady=10)
        
        tk.Label(spin_frame, text="Linhas:", bg='#2d2d2d', fg='white').grid(row=0, column=0, padx=5)
        self.rows_var = tk.StringVar(value="2")
        ttk.Spinbox(spin_frame, from_=1, to=8, width=5, textvariable=self.rows_var).grid(row=0, column=1, padx=5)
        
        tk.Label(spin_frame, text="Colunas:", bg='#2d2d2d', fg='white').grid(row=0, column=2, padx=5)
        self.cols_var = tk.StringVar(value="2")
        ttk.Spinbox(spin_frame, from_=1, to=14, width=5, textvariable=self.cols_var).grid(row=0, column=3, padx=5)
        
        self.auto_crop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(main_frame, text="Dividir imagem automaticamente", variable=self.auto_crop_var).pack(pady=10)
        
        btn_frame = tk.Frame(main_frame, bg='#2d2d2d')
        btn_frame.pack(pady=20)
        ttk.Button(btn_frame, text="OK", command=self.on_ok).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy).pack(side='left', padx=5)
    
    def on_ok(self):
        try:
            rows, cols = int(self.rows_var.get()), int(self.cols_var.get())
            self.callback(rows, cols, self.auto_crop_var.get())
            self.destroy()
        except ValueError:
            messagebox.showerror("Erro", "Valores invalidos")


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
        self.monitor_thread = threading.Thread(target=self.monitor_devices, daemon=True)
        self.monitor_thread.start()
    
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TButton', background='#4a4a8e', foreground='white', padding=5, font=('Segoe UI', 9))
        style.map('TButton', background=[('active', '#5a5a9e'), ('pressed', '#3a3a7e')])
        style.configure('TFrame', background='#1a1a2e')
        style.configure('TLabel', background='#1a1a2e', foreground='white')
        style.configure('TLabelframe', background='#1a1a2e', foreground='white')
        style.configure('TLabelframe.Label', background='#1a1a2e', foreground='white')
    
    def init_devices(self):
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
            self.encoders[enc_id] = Encoder(id=enc_id, name=name, ip=ip, port=22, rtsp_port=551, rtsp_port_preview=2554, status="offline", width=1920, height=1080)
        
        decoder_ip_start = 11
        for row in range(self.grid_rows):
            for col in range(self.grid_cols):
                idx = row * self.grid_cols + col + 1
                ip_suffix = decoder_ip_start + (idx - 1)
                self.decoders[f"dec_{idx:02d}"] = Decoder(id=f"dec_{idx:02d}", name=f"M{idx:02d}", ip=f"172.16.207.{ip_suffix}", port=23, position=(row, col), status="offline")
    
    def build_ui(self):
        self.build_header()
        
        main_container = tk.Frame(self.root, bg='#1a1a2e')
        main_container.pack(fill='both', expand=True, padx=10, pady=10)
        
        left_panel = tk.Frame(main_container, bg='#2d2d2d', width=250)
        left_panel.pack(side='left', fill='y', padx=(0, 10))
        left_panel.pack_propagate(False)
        
        self.build_sources_panel(left_panel)
        self.build_controls_panel(left_panel)
        
        center_panel = tk.Frame(main_container, bg='#1a1a2e')
        center_panel.pack(side='left', fill='both', expand=True)
        
        self.build_videowall_panel(center_panel)
        self.build_bottom_panel()
    
    def build_header(self):
        header = tk.Frame(self.root, bg='#0d1b2a', height=60)
        header.pack(fill='x')
        header.pack_propagate(False)
        
        tk.Label(header, text="MINISTERIO DA JUSTICA E SEGURANCA PUBLICA", bg='#0d1b2a', fg='#4a9eff', font=('Segoe UI', 10, 'bold')).pack(side='left', padx=20)
        tk.Label(header, text="SALA NOC - Video Wall (Telnet Auth)", bg='#0d1b2a', fg='white', font=('Segoe UI', 16, 'bold')).pack(expand=True)
        
        right_frame = tk.Frame(header, bg='#0d1b2a')
        right_frame.pack(side='right', padx=20)
        self.cred_label = tk.Label(right_frame, text=f"User: {self.avcit.username}", bg='#0d1b2a', fg='#ffaa00', font=('Segoe UI', 9))
        self.cred_label.pack(side='left', padx=10)
        self.status_label = tk.Label(right_frame, text="* Online", bg='#0d1b2a', fg='#00ff00', font=('Segoe UI', 10))
        self.status_label.pack(side='left')
    
    def build_sources_panel(self, parent):
        sources_frame = tk.LabelFrame(parent, text="Fontes (Encoders)", bg='#2d2d2d', fg='white', font=('Segoe UI', 10, 'bold'))
        sources_frame.pack(fill='both', expand=True, padx=5, pady=5)
        
        canvas = tk.Canvas(sources_frame, bg='#2d2d2d', highlightthickness=0)
        scrollbar = ttk.Scrollbar(sources_frame, orient='vertical', command=canvas.yview)
        self.sources_container = tk.Frame(canvas, bg='#2d2d2d')
        
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas_frame = canvas.create_window((0, 0), window=self.sources_container, anchor='nw')
        
        def configure_scroll(event):
            canvas.configure(scrollregion=canvas.bbox('all'))
            canvas.itemconfig(canvas_frame, width=event.width)
        self.sources_container.bind('<Configure>', configure_scroll)
        
        for encoder in self.encoders.values():
            source_widget = DraggableSource(self.sources_container, encoder, self)
            source_widget.pack(fill='x', padx=5, pady=2)
            self.source_widgets[encoder.id] = source_widget
    
    def build_controls_panel(self, parent):
        controls_frame = tk.LabelFrame(parent, text="Controles", bg='#2d2d2d', fg='white', font=('Segoe UI', 10, 'bold'))
        controls_frame.pack(fill='x', padx=5, pady=5)
        
        ttk.Button(controls_frame, text="Configurar Credenciais", command=self.open_credentials).pack(fill='x', padx=5, pady=2)
        
        ttk.Separator(controls_frame, orient='horizontal').pack(fill='x', pady=5)
        
        ttk.Button(controls_frame, text="Salvar Preset 1", command=lambda: self.save_preset(1)).pack(fill='x', padx=5, pady=2)
        ttk.Button(controls_frame, text="Salvar Preset 2", command=lambda: self.save_preset(2)).pack(fill='x', padx=5, pady=2)
        ttk.Button(controls_frame, text="Carregar Preset 1", command=lambda: self.load_preset(1)).pack(fill='x', padx=5, pady=2)
        ttk.Button(controls_frame, text="Carregar Preset 2", command=lambda: self.load_preset(2)).pack(fill='x', padx=5, pady=2)
        
        ttk.Separator(controls_frame, orient='horizontal').pack(fill='x', pady=5)
        
        ttk.Button(controls_frame, text="Criar Matriz", command=self.create_matrix).pack(fill='x', padx=5, pady=2)
        ttk.Button(controls_frame, text="Configurar Recorte", command=self.configure_crop_selected).pack(fill='x', padx=5, pady=2)
        ttk.Button(controls_frame, text="Limpar Tela", command=self.clear_all).pack(fill='x', padx=5, pady=2)
        ttk.Button(controls_frame, text="Atualizar Status", command=self.refresh_status).pack(fill='x', padx=5, pady=2)
        
        ttk.Separator(controls_frame, orient='horizontal').pack(fill='x', pady=5)
        
        ttk.Button(controls_frame, text="Ver Log", command=self.show_command_log).pack(fill='x', padx=5, pady=2)
        ttk.Button(controls_frame, text="Descobrir Comandos", command=self.open_discovery_first).pack(fill='x', padx=5, pady=2)
        
        ttk.Separator(controls_frame, orient='horizontal').pack(fill='x', pady=5)
        
        tk.Label(controls_frame, text="Modo:", bg='#2d2d2d', fg='white', font=('Segoe UI', 9)).pack(anchor='w', padx=5)
        self.display_mode = tk.StringVar(value="4x14")
        for text, mode in [("4x14 (56)", "4x14"), ("2x14 (28)", "2x14"), ("4x7 (28)", "4x7")]:
            ttk.Radiobutton(controls_frame, text=text, variable=self.display_mode, value=mode, command=self.change_display_mode).pack(anchor='w', padx=10)
    
    def open_credentials(self):
        def on_save(user, password):
            self.cred_label.configure(text=f"User: {user}")
        CredentialsDialog(self.root, self.avcit, on_save)
    
    def show_command_log(self):
        CommandLogDialog(self.root, self.avcit)
    
    def open_discovery_first(self):
        first_decoder = list(self.decoders.values())[0]
        DiscoveryDialog(self.root, self.avcit, first_decoder.ip, first_decoder.port)
    
    def build_videowall_panel(self, parent):
        wall_frame = tk.LabelFrame(parent, text="Video Wall (4x14=56) - Clique direito = opcoes", bg='#1a1a2e', fg='white', font=('Segoe UI', 10, 'bold'))
        wall_frame.pack(fill='both', expand=True)
        self.wall_container = wall_frame
        
        self.monitors_frame = tk.Frame(wall_frame, bg='#0d0d1a')
        self.monitors_frame.pack(fill='both', expand=True, padx=5, pady=5)
        self.create_monitor_grid()
    
    def create_monitor_grid(self):
        for widget in self.monitors_frame.winfo_children():
            widget.destroy()
        self.monitor_widgets.clear()
        
        for i in range(self.grid_cols):
            self.monitors_frame.columnconfigure(i, weight=1, uniform='col')
        for i in range(self.grid_rows):
            self.monitors_frame.rowconfigure(i, weight=1, uniform='row')
        
        for decoder in self.decoders.values():
            row, col = decoder.position
            if row < self.grid_rows and col < self.grid_cols:
                monitor = MonitorWidget(self.monitors_frame, decoder, self)
                monitor.grid(row=row, column=col, sticky='nsew', padx=1, pady=1)
                self.monitor_widgets[decoder.id] = monitor
    
    def build_bottom_panel(self):
        bottom = tk.Frame(self.root, bg='#0d1b2a', height=30)
        bottom.pack(fill='x', side='bottom')
        self.info_label = tk.Label(bottom, text="Arraste fonte | Duplo clique = Recorte | Clique direito = Opcoes", bg='#0d1b2a', fg='#888888', font=('Segoe UI', 9))
        self.info_label.pack(side='left', padx=20, pady=5)
        self.counter_label = tk.Label(bottom, text=f"Monitores: {len(self.decoders)} | Fontes: {len(self.encoders)}", bg='#0d1b2a', fg='#888888', font=('Segoe UI', 9))
        self.counter_label.pack(side='right', padx=20, pady=5)
    
    def start_drag(self, encoder):
        self.dragging_encoder = encoder
        self.drag_window = tk.Toplevel(self.root)
        self.drag_window.overrideredirect(True)
        self.drag_window.attributes('-alpha', 0.8)
        self.drag_window.attributes('-topmost', True)
        tk.Label(self.drag_window, text=f"{encoder.name}\n{encoder.ip}", bg='#4a9eff', fg='white', font=('Segoe UI', 10, 'bold'), padx=10, pady=5).pack()
    
    def drag_motion(self, x, y):
        if self.drag_window:
            self.drag_window.geometry(f"+{x + 10}+{y + 10}")
    
    def end_drag(self, x, y):
        if self.drag_window:
            self.drag_window.destroy()
            self.drag_window = None
        if not self.dragging_encoder:
            return
        target_widget = self.root.winfo_containing(x, y)
        while target_widget and not isinstance(target_widget, MonitorWidget):
            target_widget = target_widget.master
        if isinstance(target_widget, MonitorWidget):
            if self.selected_monitors:
                for monitor in self.selected_monitors:
                    self.apply_source_to_monitor(monitor, self.dragging_encoder)
                self.clear_selection()
            else:
                self.apply_source_to_monitor(target_widget, self.dragging_encoder)
        self.dragging_encoder = None
    
    def apply_source_to_monitor(self, monitor, encoder):
        monitor.set_source(encoder)
        threading.Thread(target=self._send_switch_command, args=(monitor.decoder, encoder), daemon=True).start()
    
    def _send_switch_command(self, decoder, encoder):
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
        for monitor in self.selected_monitors:
            monitor.set_selected(False)
        self.selected_monitors.clear()
    
    def open_crop_dialog(self, monitor):
        if not monitor.decoder.current_source:
            messagebox.showwarning("Aviso", "Selecione uma fonte primeiro")
            return
        encoder = self.encoders.get(monitor.decoder.current_source)
        if not encoder:
            return
        current_crop = monitor.decoder.get_crop_region()
        
        def on_crop_applied(crop):
            monitor.set_crop(crop)
            if crop:
                threading.Thread(target=self.avcit.set_crop, args=(monitor.decoder.ip, monitor.decoder.port, crop), daemon=True).start()
            else:
                threading.Thread(target=self.avcit.clear_crop, args=(monitor.decoder.ip, monitor.decoder.port), daemon=True).start()
        
        CropSelectorDialog(self.root, encoder, current_crop, on_crop_applied)
    
    def configure_crop_selected(self):
        if not self.selected_monitors:
            messagebox.showwarning("Aviso", "Selecione monitores primeiro")
            return
        sources = set(m.decoder.current_source for m in self.selected_monitors if m.decoder.current_source)
        if not sources:
            messagebox.showwarning("Aviso", "Monitores sem fonte")
            return
        if len(sources) > 1:
            messagebox.showwarning("Aviso", "Fontes diferentes")
            return
        encoder = self.encoders.get(list(sources)[0])
        
        def on_crop_applied(crop):
            for monitor in self.selected_monitors:
                monitor.set_crop(crop)
                if crop:
                    threading.Thread(target=self.avcit.set_crop, args=(monitor.decoder.ip, monitor.decoder.port, crop), daemon=True).start()
            self.clear_selection()
        
        CropSelectorDialog(self.root, encoder, None, on_crop_applied)
    
    def create_matrix(self):
        if len(self.selected_monitors) < 2:
            messagebox.showwarning("Aviso", "Selecione 2+ monitores")
            return
        MatrixSelector(self.root, self._apply_matrix)
    
    def _apply_matrix(self, rows, cols, auto_crop):
        if len(self.selected_monitors) != rows * cols:
            messagebox.showwarning("Aviso", f"Selecione exatamente {rows*cols} monitores")
            return
        
        source_dialog = tk.Toplevel(self.root)
        source_dialog.title("Fonte para Matriz")
        source_dialog.geometry("300x400")
        source_dialog.configure(bg='#2d2d2d')
        source_dialog.transient(self.root)
        source_dialog.grab_set()
        
        tk.Label(source_dialog, text="Selecione a fonte:", bg='#2d2d2d', fg='white').pack(pady=10)
        
        listbox = tk.Listbox(source_dialog, bg='#1a1a2e', fg='white', font=('Segoe UI', 10))
        listbox.pack(fill='both', expand=True, padx=10, pady=5)
        
        encoder_list = list(self.encoders.values())
        for encoder in encoder_list:
            listbox.insert('end', f"{encoder.name} ({encoder.ip})")
        
        def apply():
            selection = listbox.curselection()
            if selection:
                encoder = encoder_list[selection[0]]
                sorted_monitors = sorted(self.selected_monitors, key=lambda m: (m.decoder.position[0], m.decoder.position[1]))
                
                tile_width = encoder.width // cols
                tile_height = encoder.height // rows
                
                for i, monitor in enumerate(sorted_monitors):
                    row_idx, col_idx = i // cols, i % cols
                    monitor.set_source(encoder)
                    if auto_crop:
                        crop = CropRegion(x=col_idx * tile_width, y=row_idx * tile_height, width=tile_width, height=tile_height, source_width=encoder.width, source_height=encoder.height, enabled=True)
                        monitor.set_crop(crop)
                        threading.Thread(target=self._send_matrix_commands, args=(monitor.decoder, encoder, crop), daemon=True).start()
                    else:
                        threading.Thread(target=self._send_switch_command, args=(monitor.decoder, encoder), daemon=True).start()
                
                self.clear_selection()
                source_dialog.destroy()
                messagebox.showinfo("Sucesso", f"Matriz {rows}x{cols} criada!")
        
        ttk.Button(source_dialog, text="Aplicar", command=apply).pack(pady=10)
    
    def _send_matrix_commands(self, decoder, encoder, crop):
        self.avcit.switch_source(decoder.ip, decoder.port, encoder.ip, encoder.port)
        time.sleep(0.1)
        self.avcit.set_crop(decoder.ip, decoder.port, crop)
    
    def save_preset(self, preset_num):
        name = simpledialog.askstring("Salvar", f"Nome do Preset {preset_num}:", initialvalue=f"Preset {preset_num}")
        if not name: return
        mappings, crops = {}, {}
        for decoder_id, monitor in self.monitor_widgets.items():
            if monitor.decoder.current_source:
                mappings[decoder_id] = monitor.decoder.current_source
            if monitor.decoder.crop:
                crops[decoder_id] = monitor.decoder.crop
        preset = Preset(id=f"preset_{preset_num}", name=name, timestamp=datetime.now().isoformat(), mappings=mappings, matrices=[], crops=crops)
        self.presets[preset.id] = preset
        self.save_config()
        messagebox.showinfo("Sucesso", f"Preset '{name}' salvo!")
    
    def load_preset(self, preset_num):
        preset_id = f"preset_{preset_num}"
        if preset_id not in self.presets:
            messagebox.showwarning("Aviso", f"Preset {preset_num} nao encontrado")
            return
        preset = self.presets[preset_id]
        for monitor in self.monitor_widgets.values():
            monitor.set_source(None)
            monitor.set_crop(None)
        for decoder_id, encoder_id in preset.mappings.items():
            if decoder_id in self.monitor_widgets and encoder_id in self.encoders:
                self.apply_source_to_monitor(self.monitor_widgets[decoder_id], self.encoders[encoder_id])
        for decoder_id, crop_data in preset.crops.items():
            if decoder_id in self.monitor_widgets:
                monitor = self.monitor_widgets[decoder_id]
                crop = CropRegion.from_dict(crop_data)
                monitor.set_crop(crop)
                threading.Thread(target=self.avcit.set_crop, args=(monitor.decoder.ip, monitor.decoder.port, crop), daemon=True).start()
        messagebox.showinfo("Sucesso", f"Preset '{preset.name}' carregado!")
    
    def get_config_path(self):
        if getattr(sys, 'frozen', False):
            return os.path.join(os.path.dirname(sys.executable), 'videowall_config.json')
        return 'videowall_config.json'
    
    def save_config(self):
        config = {
            'credentials': {'username': self.avcit.username, 'password': self.avcit.password},
            'presets': {k: v.to_dict() for k, v in self.presets.items()},
            'grid': {'rows': self.grid_rows, 'cols': self.grid_cols}
        }
        try:
            with open(self.get_config_path(), 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except: pass
    
    def load_config(self):
        try:
            config_path = self.get_config_path()
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                if 'credentials' in config:
                    self.avcit.set_credentials(config['credentials'].get('username', 'admin'), config['credentials'].get('password', 'admin'))
                    self.cred_label.configure(text=f"User: {self.avcit.username}")
                for preset_id, preset_data in config.get('presets', {}).items():
                    self.presets[preset_id] = Preset.from_dict(preset_data)
        except: pass
    
    def clear_all(self):
        if messagebox.askyesno("Confirmar", "Limpar tudo?"):
            for monitor in self.monitor_widgets.values():
                monitor.clear_source()
    
    def refresh_status(self):
        threading.Thread(target=self._refresh_status_thread, daemon=True).start()
    
    def _refresh_status_thread(self):
        for encoder in self.encoders.values():
            status = self.avcit.check_device_status(encoder.ip, encoder.port)
            encoder.status = status
            self.root.after(0, lambda e=encoder: self.source_widgets[e.id].update_status(e.status))
        for decoder in self.decoders.values():
            status = self.avcit.check_device_status(decoder.ip, decoder.port)
            decoder.status = status
            if decoder.id in self.monitor_widgets:
                self.root.after(0, lambda d=decoder: self.monitor_widgets[d.id].update_status(d.status))
    
    def monitor_devices(self):
        while self.monitoring:
            time.sleep(30)
            try: self._refresh_status_thread()
            except: pass
    
    def update_mapping(self, decoder_id, encoder_id):
        if decoder_id in self.decoders:
            self.decoders[decoder_id].current_source = encoder_id
    
    def change_display_mode(self):
        mode = self.display_mode.get()
        if mode == "4x14": self.grid_rows, self.grid_cols = 4, 14
        elif mode == "2x14": self.grid_rows, self.grid_cols = 2, 14
        elif mode == "4x7": self.grid_rows, self.grid_cols = 4, 7
        self.create_monitor_grid()
        self.wall_container.configure(text=f"Video Wall ({self.grid_rows}x{self.grid_cols}={self.grid_rows*self.grid_cols})")
    
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
